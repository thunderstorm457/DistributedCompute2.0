import base64
import binascii
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Boolean, DateTime, JSON, String, create_engine, select, update
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./distributedcompute.db")
CHALLENGE_TTL = timedelta(minutes=5)
NODE_STALE_AFTER = timedelta(minutes=5)

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class NodeRole(str, Enum):
    COMPUTE = "compute"
    PROVISIONING = "provisioning"
    ID_STORAGE = "id_storage"
    LOAD_BALANCER = "load_balancer"


class Node(Base):
    __tablename__ = "nodes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    public_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    role: Mapped[str] = mapped_column(String(32), default=NodeRole.COMPUTE.value)
    hardware: Mapped[dict[str, Any]] = mapped_column(JSON)
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Challenge(Base):
    __tablename__ = "challenges"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    public_key: Mapped[str] = mapped_column(String(64), index=True)
    nonce: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used: Mapped[bool] = mapped_column(Boolean, default=False)


Base.metadata.create_all(engine)

app = FastAPI(title="DistributedCompute Provisioning API", version="0.1.0")


def get_db():
    with SessionLocal() as db:
        yield db


class ChallengeRequest(BaseModel):
    public_key: str = Field(min_length=44, max_length=44)


class ChallengeResponse(BaseModel):
    challenge_id: str
    challenge: str
    expires_at: datetime


class RegistrationRequest(BaseModel):
    challenge_id: str
    public_key: str = Field(min_length=44, max_length=44)
    signature: str = Field(min_length=80, max_length=100)
    hardware: dict[str, Any]


class RecoveryRequest(BaseModel):
    challenge_id: str
    public_key: str = Field(min_length=44, max_length=44)
    signature: str = Field(min_length=80, max_length=100)


class HeartbeatRequest(BaseModel):
    challenge_id: str
    signature: str = Field(min_length=80, max_length=100)


class NodeResponse(BaseModel):
    node_id: str
    role: NodeRole
    registered_at: datetime
    last_seen: datetime


class RoleRequest(BaseModel):
    role: NodeRole


class AdminNodeResponse(NodeResponse):
    hardware: dict[str, Any]
    status: str


def decode_base64(value: str, expected_bytes: int, field: str) -> bytes:
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"Invalid {field}") from exc
    if len(decoded) != expected_bytes:
        raise HTTPException(status_code=422, detail=f"Invalid {field}")
    return decoded


def canonical_public_key(value: str) -> str:
    raw_key = decode_base64(value, 32, "public key")
    return base64.b64encode(raw_key).decode("ascii")


def signed_message(action: str, challenge_id: str, nonce: str, node_id: str = "") -> bytes:
    return f"distributedcompute:v1:{action}:{node_id}:{challenge_id}:{nonce}".encode()


def verify_and_consume_challenge(
    db: Session,
    challenge_id: str,
    public_key: str,
    signature: str,
    action: str,
    node_id: str = "",
) -> None:
    challenge = db.get(Challenge, challenge_id)
    now = datetime.now(timezone.utc)
    if (
        challenge is None
        or challenge.used
        or challenge.public_key != public_key
        or challenge.expires_at.replace(tzinfo=timezone.utc) <= now
    ):
        raise HTTPException(status_code=401, detail="Invalid or expired challenge")

    raw_signature = decode_base64(signature, 64, "signature")
    try:
        Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key)).verify(
            raw_signature,
            signed_message(action, challenge_id, challenge.nonce, node_id),
        )
    except InvalidSignature as exc:
        raise HTTPException(status_code=401, detail="Invalid signature") from exc

    consumed = db.execute(
        update(Challenge)
        .where(Challenge.id == challenge_id, Challenge.used.is_(False))
        .values(used=True)
    )
    if consumed.rowcount != 1:
        db.rollback()
        raise HTTPException(status_code=401, detail="Challenge already used")


def require_admin(authorization: str | None) -> None:
    admin_token = os.getenv("PROVISIONING_ADMIN_TOKEN")
    if not admin_token:
        raise HTTPException(status_code=503, detail="Administrative access is not configured")
    supplied_token = (
        authorization.removeprefix("Bearer ")
        if authorization and authorization.startswith("Bearer ")
        else ""
    )
    if not secrets.compare_digest(supplied_token, admin_token):
        raise HTTPException(status_code=403, detail="Administrative access required")


def node_response(node: Node) -> NodeResponse:
    return NodeResponse(
        node_id=node.id,
        role=NodeRole(node.role),
        registered_at=node.registered_at,
        last_seen=node.last_seen,
    )


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/v1/challenges", response_model=ChallengeResponse)
def create_challenge(request: ChallengeRequest, db: Session = Depends(get_db)):
    public_key = canonical_public_key(request.public_key)
    challenge = Challenge(
        id=str(uuid.uuid4()),
        public_key=public_key,
        nonce=base64.b64encode(secrets.token_bytes(32)).decode("ascii"),
        expires_at=datetime.now(timezone.utc) + CHALLENGE_TTL,
        used=False,
    )
    db.add(challenge)
    db.commit()
    return ChallengeResponse(
        challenge_id=challenge.id,
        challenge=challenge.nonce,
        expires_at=challenge.expires_at,
    )


@app.post("/v1/nodes/register", response_model=NodeResponse, status_code=201)
def register_node(request: RegistrationRequest, db: Session = Depends(get_db)):
    public_key = canonical_public_key(request.public_key)
    verify_and_consume_challenge(
        db, request.challenge_id, public_key, request.signature, "register"
    )
    if db.scalar(select(Node).where(Node.public_key == public_key)):
        db.rollback()
        raise HTTPException(status_code=409, detail="Node identity already registered")

    now = datetime.now(timezone.utc)
    node = Node(
        id=str(uuid.uuid4()),
        public_key=public_key,
        role=NodeRole.COMPUTE.value,
        hardware=request.hardware,
        registered_at=now,
        last_seen=now,
    )
    db.add(node)
    db.commit()
    return NodeResponse(
        node_id=node.id,
        role=NodeRole(node.role),
        registered_at=node.registered_at,
        last_seen=node.last_seen,
    )


@app.post("/v1/nodes/recover", response_model=NodeResponse)
def recover_node(request: RecoveryRequest, db: Session = Depends(get_db)):
    public_key = canonical_public_key(request.public_key)
    verify_and_consume_challenge(
        db, request.challenge_id, public_key, request.signature, "recover"
    )
    node = db.scalar(select(Node).where(Node.public_key == public_key))
    if node is None:
        raise HTTPException(status_code=404, detail="Node not found")
    db.commit()
    return node_response(node)


@app.post("/v1/nodes/{node_id}/heartbeat", response_model=NodeResponse)
def heartbeat(node_id: str, request: HeartbeatRequest, db: Session = Depends(get_db)):
    node = db.get(Node, node_id)
    if node is None:
        raise HTTPException(status_code=404, detail="Node not found")
    verify_and_consume_challenge(
        db,
        request.challenge_id,
        node.public_key,
        request.signature,
        "heartbeat",
        node_id,
    )
    node.last_seen = datetime.now(timezone.utc)
    db.commit()
    return node_response(node)


@app.get("/v1/admin/nodes", response_model=list[AdminNodeResponse])
def list_nodes(
    authorization: str | None = Header(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    require_admin(authorization)
    nodes = db.scalars(
        select(Node).order_by(Node.last_seen.desc()).limit(limit).offset(offset)
    ).all()
    now = datetime.now(timezone.utc)
    response = []
    for node in nodes:
        last_seen = node.last_seen.replace(tzinfo=timezone.utc)
        response.append(
            AdminNodeResponse(
                **node_response(node).model_dump(),
                hardware=node.hardware,
                status="online" if now - last_seen <= NODE_STALE_AFTER else "offline",
            )
        )
    return response


@app.put("/v1/admin/nodes/{node_id}/role", response_model=NodeResponse)
def assign_role(
    node_id: str,
    request: RoleRequest,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    require_admin(authorization)

    node = db.get(Node, node_id)
    if node is None:
        raise HTTPException(status_code=404, detail="Node not found")
    node.role = request.role.value
    db.commit()
    return node_response(node)