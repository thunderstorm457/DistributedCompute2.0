import base64
import binascii
import hashlib
import json
import os
import secrets
import threading
import uuid
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    UniqueConstraint,
    create_engine,
    func,
    select,
    text,
)
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from server.scheduler import choose_compute_nodes


DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./distributedcompute.db")
APP_ENV = os.getenv("APP_ENV", "development").lower()
RATE_LIMIT_STORAGE_URI = os.getenv("RATE_LIMIT_STORAGE_URI", "memory://")
CHALLENGE_TTL = timedelta(minutes=5)
NODE_STALE_AFTER = timedelta(minutes=5)
PRESENCE_RETENTION = timedelta(days=7)
MAX_HARDWARE_REPORT_BYTES = 8192
MAX_TEST_WORK_ITEMS = 2_000_000
MAX_TEST_NODE_CANDIDATES = 256
TEST_RUN_TIMEOUT = timedelta(minutes=15)

if APP_ENV in {"prod", "production"} and RATE_LIMIT_STORAGE_URI == "memory://":
    raise RuntimeError("Production requires shared RATE_LIMIT_STORAGE_URI storage")
if APP_ENV in {"prod", "production"}:
    if DATABASE_URL.startswith("sqlite"):
        raise RuntimeError("Production requires PostgreSQL, not SQLite")
    admin_token = os.getenv("PROVISIONING_ADMIN_TOKEN", "")
    if len(admin_token) < 32:
        raise RuntimeError("Production requires a PROVISIONING_ADMIN_TOKEN of at least 32 characters")

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
redis_client = (
    Redis.from_url(RATE_LIMIT_STORAGE_URI)
    if RATE_LIMIT_STORAGE_URI.startswith(("redis://", "rediss://"))
    else None
)
_local_state_lock = threading.Lock()
_local_challenges = {}
_local_presence = {}


class Base(DeclarativeBase):
    pass


class NodeRole(str, Enum):
    COMPUTE = "compute"
    PROVISIONING = "provisioning"
    ID_STORAGE = "id_storage"
    LOAD_BALANCER = "load_balancer"
    ECONOMY = "economy"


class EnrollmentStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class EnrollmentDecision(str, Enum):
    APPROVED = "approved"
    REJECTED = "rejected"


class Node(Base):
    __tablename__ = "nodes"
    __table_args__ = (Index("ix_nodes_role_enrollment_status", "role", "enrollment_status"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    public_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    role: Mapped[str] = mapped_column(String(32), default=NodeRole.COMPUTE.value)
    enrollment_status: Mapped[str] = mapped_column(
        String(16), default=EnrollmentStatus.PENDING.value,
        server_default=EnrollmentStatus.PENDING.value,
    )
    hardware: Mapped[dict[str, Any]] = mapped_column(JSON)
    logical_cpu_cores: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    ram_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    storage_total_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    storage_available_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    gpu_cores: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Challenge(Base):
    __tablename__ = "challenges"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    public_key: Mapped[str] = mapped_column(String(64), index=True)
    nonce: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used: Mapped[bool] = mapped_column(Boolean, default=False)


class TestRun(Base):
    __tablename__ = "test_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), default="running")
    total_items: Mapped[int] = mapped_column(Integer)
    shard_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TestShard(Base):
    __tablename__ = "test_shards"
    __table_args__ = (
        UniqueConstraint("run_id", "shard_index"),
        Index("ix_test_shards_node_status_run", "node_id", "status", "run_id", "shard_index"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("test_runs.id", ondelete="CASCADE"), index=True)
    shard_index: Mapped[int] = mapped_column(Integer)
    node_id: Mapped[str] = mapped_column(ForeignKey("nodes.id"), index=True)
    start_inclusive: Mapped[int] = mapped_column(Integer)
    end_exclusive: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="queued")
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result_sum: Mapped[int | None] = mapped_column(BigInteger)
    duration_ms: Mapped[int | None] = mapped_column(Integer)


app = FastAPI(title="DistributedCompute Provisioning API", version="0.1.0")
ROLE_CAPABILITIES = {
    NodeRole.COMPUTE.value: {
        "status": "active",
        "description": "Registers, heartbeats, and runs the built-in diagnostic workload.",
    },
    NodeRole.PROVISIONING.value: {
        "status": "centralized",
        "description": "Provisioning is currently handled by this API, not a peer node service.",
    },
    NodeRole.ID_STORAGE.value: {
        "status": "planned",
        "description": "Replicated identity storage is not implemented.",
    },
    NodeRole.LOAD_BALANCER.value: {
        "status": "prototype",
        "description": "The API has a least-loaded shard selector; peer load-balancer nodes are not active.",
    },
    NodeRole.ECONOMY.value: {
        "status": "prototype",
        "description": "A CPU/RAM quote calculator exists; settlement receipts, quorum, and ledger are not implemented.",
    },
}
limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=RATE_LIMIT_STORAGE_URI,
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


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

    @field_validator("hardware")
    @classmethod
    def validate_hardware_size(cls, value):
        return validate_hardware_report(value)


class HardwareUpdateRequest(BaseModel):
    challenge_id: str
    hardware: dict[str, Any]
    signature: str = Field(min_length=80, max_length=100)

    @field_validator("hardware")
    @classmethod
    def validate_hardware_size(cls, value):
        return validate_hardware_report(value)


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
    status: EnrollmentStatus
    registered_at: datetime
    last_seen: datetime


class RoleRequest(BaseModel):
    role: NodeRole


class EnrollmentRequest(BaseModel):
    status: EnrollmentDecision


class TestRunCreateRequest(BaseModel):
    total_items: int = Field(ge=2, le=MAX_TEST_WORK_ITEMS)
    shard_count: int = Field(ge=2, le=64)


class TestTaskClaimRequest(BaseModel):
    challenge_id: str
    signature: str = Field(min_length=80, max_length=100)


class TestTaskResultRequest(BaseModel):
    challenge_id: str
    item_count: int = Field(ge=1, le=MAX_TEST_WORK_ITEMS)
    sum_squares: int = Field(ge=0)
    signature: str = Field(min_length=80, max_length=100)


class TestShardResponse(BaseModel):
    shard_id: str
    shard_index: int
    node_id: str
    start_inclusive: int
    end_exclusive: int
    status: str
    sum_squares: int | None
    duration_ms: int | None


class TestRunResponse(BaseModel):
    run_id: str
    workload: str
    status: str
    total_items: int
    shard_count: int
    completed_shards: int
    participating_nodes: int
    split_verified: bool
    elapsed_ms: int | None
    items_per_second: float | None
    shards: list[TestShardResponse]


class ClaimedTestTaskResponse(BaseModel):
    task_id: str
    run_id: str
    shard_index: int
    start_inclusive: int
    end_exclusive: int


class TestTaskCompletionResponse(BaseModel):
    task_id: str
    status: str
    verified: bool
    duration_ms: int


class AdminNodeResponse(NodeResponse):
    hardware: dict[str, Any]
    health: str


class ResourceTotals(BaseModel):
    logical_cpu_cores: int
    ram_bytes: int
    storage_total_bytes: int
    storage_available_bytes: int
    gpu_cores: int
    nodes_reporting_cpu: int
    nodes_reporting_ram: int
    nodes_reporting_storage: int
    nodes_reporting_gpu: int


class AdminSummaryResponse(BaseModel):
    generated_at: datetime
    registered_nodes: int
    online_nodes: int
    nodes_by_role: dict[str, int]
    role_capabilities: dict[str, dict[str, str]]
    registered_capacity: ResourceTotals


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


def validate_hardware_report(value: dict[str, Any]) -> dict[str, Any]:
    try:
        encoded = json.dumps(value, separators=(",", ":"), allow_nan=False).encode()
    except (TypeError, ValueError) as exc:
        raise ValueError("Hardware report must contain valid JSON values") from exc
    if len(encoded) > MAX_HARDWARE_REPORT_BYTES:
        raise ValueError("Hardware report must be 8 KiB or smaller")
    return value


def hardware_report_digest(hardware: dict[str, Any]) -> str:
    serialized = json.dumps(
        hardware,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    return hashlib.sha256(serialized).hexdigest()


def reported_capacity(hardware: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = hardware.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    return None


def set_node_capacity(node: Node, hardware: dict[str, Any]) -> None:
    node.logical_cpu_cores = reported_capacity(
        hardware, "logical_cpu_cores", "logical_cpu_count", "cpu_cores"
    )
    node.ram_bytes = reported_capacity(hardware, "ram_bytes", "memory_bytes")
    node.storage_total_bytes = reported_capacity(
        hardware, "storage_total_bytes", "storage_bytes"
    )
    node.storage_available_bytes = reported_capacity(
        hardware, "storage_available_bytes"
    )
    node.gpu_cores = reported_capacity(hardware, "gpu_cores", "gpu_core_count")


def signed_message(
    action: str,
    challenge_id: str,
    nonce: str,
    node_id: str = "",
    context: str = "",
) -> bytes:
    message = f"distributedcompute:v1:{action}:{node_id}:{challenge_id}:{nonce}"
    if context:
        message = f"{message}:{context}"
    return message.encode()


def store_challenge(challenge_id: str, public_key: str, nonce: str, expires_at: datetime):
    if redis_client is not None:
        try:
            stored = redis_client.set(
                f"distributedcompute:challenge:{challenge_id}",
                json.dumps({"public_key": public_key, "nonce": nonce}),
                ex=int(CHALLENGE_TTL.total_seconds()),
                nx=True,
            )
        except RedisError as exc:
            raise HTTPException(status_code=503, detail="Challenge storage is unavailable") from exc
        if not stored:
            raise HTTPException(status_code=503, detail="Could not create challenge")
        return

    now = datetime.now(timezone.utc)
    with _local_state_lock:
        expired = [key for key, value in _local_challenges.items() if value["expires_at"] <= now]
        for key in expired:
            _local_challenges.pop(key, None)
        _local_challenges[challenge_id] = {
            "public_key": public_key,
            "nonce": nonce,
            "expires_at": expires_at,
        }


def consume_challenge(challenge_id: str):
    if redis_client is not None:
        try:
            payload = redis_client.eval(
                "local value = redis.call('GET', KEYS[1]); "
                "if value then redis.call('DEL', KEYS[1]); end; return value",
                1,
                f"distributedcompute:challenge:{challenge_id}",
            )
        except RedisError as exc:
            raise HTTPException(status_code=503, detail="Challenge storage is unavailable") from exc
        return json.loads(payload) if payload else None

    with _local_state_lock:
        challenge = _local_challenges.pop(challenge_id, None)
    if challenge and challenge["expires_at"] > datetime.now(timezone.utc):
        return challenge
    return None


def record_presence(node_id: str, last_seen: datetime):
    if redis_client is not None:
        try:
            with redis_client.pipeline(transaction=False) as pipeline:
                pipeline.set(
                    f"distributedcompute:last-seen:{node_id}",
                    last_seen.isoformat(),
                    ex=int(PRESENCE_RETENTION.total_seconds()),
                )
                pipeline.set(
                    f"distributedcompute:online:{node_id}",
                    "1",
                    ex=int(NODE_STALE_AFTER.total_seconds()),
                )
                pipeline.zadd(
                    "distributedcompute:online-nodes",
                    {node_id: last_seen.timestamp()},
                )
                pipeline.execute()
        except RedisError as exc:
            raise HTTPException(status_code=503, detail="Presence storage is unavailable") from exc
        return

    with _local_state_lock:
        _local_presence[node_id] = last_seen


def get_presence(node_ids: list[str]):
    if not node_ids:
        return {}
    if redis_client is not None:
        keys = []
        for node_id in node_ids:
            keys.extend(
                [
                    f"distributedcompute:last-seen:{node_id}",
                    f"distributedcompute:online:{node_id}",
                ]
            )
        try:
            values = redis_client.mget(keys)
        except RedisError as exc:
            raise HTTPException(status_code=503, detail="Presence storage is unavailable") from exc
        result = {}
        for index, node_id in enumerate(node_ids):
            timestamp, online = values[index * 2 : index * 2 + 2]
            last_seen = datetime.fromisoformat(timestamp.decode()) if timestamp else None
            result[node_id] = (last_seen, online is not None)
        return result

    now = datetime.now(timezone.utc)
    with _local_state_lock:
        return {
            node_id: (
                _local_presence.get(node_id),
                bool(
                    _local_presence.get(node_id)
                    and now - _local_presence[node_id] <= NODE_STALE_AFTER
                ),
            )
            for node_id in node_ids
        }


def get_recent_online_node_ids(limit: int):
    now = datetime.now(timezone.utc)
    if redis_client is not None:
        try:
            node_ids = redis_client.eval(
                "local key = KEYS[1]; "
                "local cutoff = tonumber(ARGV[1]); "
                "local expired = redis.call('ZRANGEBYSCORE', key, '-inf', cutoff, 'LIMIT', 0, 512); "
                "for _, member in ipairs(expired) do "
                "local score = redis.call('ZSCORE', key, member); "
                "if score and tonumber(score) <= cutoff then redis.call('ZREM', key, member); end; "
                "end; "
                "return redis.call('ZREVRANGE', key, 0, tonumber(ARGV[2]) - 1)",
                1,
                "distributedcompute:online-nodes",
                now.timestamp() - NODE_STALE_AFTER.total_seconds(),
                limit,
            )
            return [node_id.decode() for node_id in node_ids]
        except RedisError as exc:
            raise HTTPException(status_code=503, detail="Presence storage is unavailable") from exc

    with _local_state_lock:
        online = [
            (node_id, last_seen)
            for node_id, last_seen in _local_presence.items()
            if now - last_seen <= NODE_STALE_AFTER
        ]
    online.sort(key=lambda entry: entry[1], reverse=True)
    return [node_id for node_id, _ in online[:limit]]


def count_online_nodes() -> int:
    if redis_client is not None:
        try:
            return redis_client.zcount(
                "distributedcompute:online-nodes",
                datetime.now(timezone.utc).timestamp() - NODE_STALE_AFTER.total_seconds(),
                "+inf",
            )
        except RedisError as exc:
            raise HTTPException(status_code=503, detail="Presence storage is unavailable") from exc
    now = datetime.now(timezone.utc)
    with _local_state_lock:
        return sum(
            now - last_seen <= NODE_STALE_AFTER
            for last_seen in _local_presence.values()
        )


def verify_and_consume_challenge(
    challenge_id: str,
    public_key: str,
    signature: str,
    action: str,
    node_id: str = "",
    context: str = "",
) -> None:
    challenge = consume_challenge(challenge_id)
    if (
        challenge is None
        or challenge["public_key"] != public_key
    ):
        raise HTTPException(status_code=401, detail="Invalid or expired challenge")

    raw_signature = decode_base64(signature, 64, "signature")
    try:
        Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key)).verify(
            raw_signature,
            signed_message(action, challenge_id, challenge["nonce"], node_id, context),
        )
    except InvalidSignature as exc:
        raise HTTPException(status_code=401, detail="Invalid signature") from exc

def require_admin(admin_passcode: str | None) -> None:
    admin_token = os.getenv("PROVISIONING_ADMIN_TOKEN")
    if not admin_token:
        raise HTTPException(status_code=503, detail="Administrative access is not configured")
    if not secrets.compare_digest(admin_passcode or "", admin_token):
        raise HTTPException(status_code=403, detail="Administrative access required")


def node_response(node: Node, last_seen: datetime | None = None) -> NodeResponse:
    if last_seen is None:
        last_seen = node.last_seen.replace(tzinfo=timezone.utc)
    return NodeResponse(
        node_id=node.id,
        role=NodeRole(node.role),
        status=EnrollmentStatus(node.enrollment_status),
        registered_at=node.registered_at,
        last_seen=last_seen,
    )


def sum_squares_before(value: int) -> int:
    return value * (value - 1) * (2 * value - 1) // 6


def test_run_response(db: Session, run: TestRun) -> TestRunResponse:
    shards = db.scalars(
        select(TestShard)
        .where(TestShard.run_id == run.id)
        .order_by(TestShard.shard_index)
    ).all()
    completed = [shard for shard in shards if shard.status == "completed"]
    distinct_nodes = {shard.node_id for shard in completed}
    next_item = 0
    coverage_valid = True
    for shard in shards:
        if shard.start_inclusive != next_item or shard.end_exclusive <= shard.start_inclusive:
            coverage_valid = False
        next_item = shard.end_exclusive
    coverage_valid = coverage_valid and next_item == run.total_items
    split_verified = (
        len(completed) == run.shard_count
        and coverage_valid
        and len(distinct_nodes) == run.shard_count
        and all(
            shard.result_sum
            == sum_squares_before(shard.end_exclusive)
            - sum_squares_before(shard.start_inclusive)
            for shard in completed
        )
    )
    elapsed_ms = None
    items_per_second = None
    if run.completed_at is not None:
        completed_at = run.completed_at.replace(tzinfo=timezone.utc)
        created_at = run.created_at.replace(tzinfo=timezone.utc)
        elapsed_ms = max(1, int((completed_at - created_at).total_seconds() * 1000))
        items_per_second = run.total_items * 1000 / elapsed_ms
    return TestRunResponse(
        run_id=run.id,
        workload="sum_squares",
        status=run.status,
        total_items=run.total_items,
        shard_count=run.shard_count,
        completed_shards=len(completed),
        participating_nodes=len(distinct_nodes),
        split_verified=split_verified,
        elapsed_ms=elapsed_ms,
        items_per_second=items_per_second,
        shards=[
            TestShardResponse(
                shard_id=shard.id,
                shard_index=shard.shard_index,
                node_id=shard.node_id,
                start_inclusive=shard.start_inclusive,
                end_exclusive=shard.end_exclusive,
                status=shard.status,
                sum_squares=shard.result_sum,
                duration_ms=shard.duration_ms,
            )
            for shard in shards
        ],
    )


def fail_expired_test_run(db: Session, run: TestRun) -> bool:
    now = datetime.now(timezone.utc)
    created_at = run.created_at.replace(tzinfo=timezone.utc)
    if run.status == "running" and now - created_at >= TEST_RUN_TIMEOUT:
        run.status = "failed"
        run.completed_at = now
        db.commit()
        return True
    return False


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/admin", include_in_schema=False)
def admin_dashboard():
    return FileResponse(Path(__file__).parent / "static" / "admin.html")


@app.get("/ready")
def readiness(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Database is unavailable") from exc
    if redis_client is not None:
        try:
            redis_client.ping()
        except RedisError as exc:
            raise HTTPException(status_code=503, detail="Rate-limit storage is unavailable") from exc
    return {"status": "ready"}


@app.post("/v1/challenges", response_model=ChallengeResponse)
@limiter.limit("30/minute")
def create_challenge(
    request: Request,
    challenge_request: ChallengeRequest,
):
    public_key = canonical_public_key(challenge_request.public_key)
    challenge_id = str(uuid.uuid4())
    nonce = base64.b64encode(secrets.token_bytes(32)).decode("ascii")
    expires_at = datetime.now(timezone.utc) + CHALLENGE_TTL
    store_challenge(challenge_id, public_key, nonce, expires_at)
    return ChallengeResponse(
        challenge_id=challenge_id,
        challenge=nonce,
        expires_at=expires_at,
    )


@app.post("/v1/nodes/register", response_model=NodeResponse, status_code=201)
@limiter.limit("10/hour")
def register_node(
    request: Request,
    registration: RegistrationRequest,
    db: Session = Depends(get_db),
):
    public_key = canonical_public_key(registration.public_key)
    verify_and_consume_challenge(
        registration.challenge_id, public_key, registration.signature, "register"
    )
    if db.scalar(select(Node).where(Node.public_key == public_key)):
        db.rollback()
        raise HTTPException(status_code=409, detail="Node identity already registered")

    now = datetime.now(timezone.utc)
    node = Node(
        id=str(uuid.uuid4()),
        public_key=public_key,
        role=NodeRole.COMPUTE.value,
        enrollment_status=EnrollmentStatus.APPROVED.value,
        hardware=registration.hardware,
        registered_at=now,
        last_seen=now,
    )
    set_node_capacity(node, registration.hardware)
    db.add(node)
    db.commit()
    record_presence(node.id, now)
    return node_response(node)


@app.post("/v1/nodes/recover", response_model=NodeResponse)
def recover_node(request: RecoveryRequest, db: Session = Depends(get_db)):
    public_key = canonical_public_key(request.public_key)
    verify_and_consume_challenge(
        request.challenge_id, public_key, request.signature, "recover"
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
    if node.enrollment_status == EnrollmentStatus.REJECTED.value:
        raise HTTPException(status_code=403, detail="Node enrollment was rejected")
    verify_and_consume_challenge(
        request.challenge_id,
        node.public_key,
        request.signature,
        "heartbeat",
        node_id,
    )
    now = datetime.now(timezone.utc)
    record_presence(node.id, now)
    return node_response(node, last_seen=now)


@app.put("/v1/nodes/{node_id}/hardware", response_model=NodeResponse)
def update_node_hardware(
    node_id: str,
    request: HardwareUpdateRequest,
    db: Session = Depends(get_db),
):
    node = db.get(Node, node_id)
    if node is None:
        raise HTTPException(status_code=404, detail="Node not found")
    if node.enrollment_status == EnrollmentStatus.REJECTED.value:
        raise HTTPException(status_code=403, detail="Node enrollment was rejected")
    verify_and_consume_challenge(
        request.challenge_id,
        node.public_key,
        request.signature,
        "update_hardware",
        node_id,
        hardware_report_digest(request.hardware),
    )
    node.hardware = request.hardware
    set_node_capacity(node, request.hardware)
    db.commit()
    return node_response(node)


@app.get("/v1/admin/nodes", response_model=list[AdminNodeResponse])
def list_nodes(
    admin_passcode: str | None = Header(default=None, alias="X-Admin-Passcode"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    require_admin(admin_passcode)
    nodes = db.scalars(
        select(Node).order_by(Node.registered_at.desc()).limit(limit).offset(offset)
    ).all()
    presence = get_presence([node.id for node in nodes])
    response = []
    for node in nodes:
        last_seen, online = presence[node.id]
        if last_seen is None:
            last_seen = node.last_seen.replace(tzinfo=timezone.utc)
        response.append(
            AdminNodeResponse(
                **node_response(node, last_seen=last_seen).model_dump(),
                hardware=node.hardware,
                health="online" if online else "offline",
            )
        )
    return response


@app.get("/v1/admin/summary", response_model=AdminSummaryResponse)
def admin_summary(
    admin_passcode: str | None = Header(default=None, alias="X-Admin-Passcode"),
    db: Session = Depends(get_db),
):
    require_admin(admin_passcode)
    aggregate = db.execute(
        select(
            func.count(Node.id),
            func.coalesce(func.sum(Node.logical_cpu_cores), 0),
            func.coalesce(func.sum(Node.ram_bytes), 0),
            func.coalesce(func.sum(Node.storage_total_bytes), 0),
            func.coalesce(func.sum(Node.storage_available_bytes), 0),
            func.coalesce(func.sum(Node.gpu_cores), 0),
            func.count(Node.logical_cpu_cores),
            func.count(Node.ram_bytes),
            func.count(Node.storage_total_bytes),
            func.count(Node.gpu_cores),
        )
    ).one()
    nodes_by_role = {role.value: 0 for role in NodeRole}
    for role, count in db.execute(
        select(Node.role, func.count(Node.id)).group_by(Node.role)
    ):
        nodes_by_role[role] = count

    return AdminSummaryResponse(
        generated_at=datetime.now(timezone.utc),
        registered_nodes=aggregate[0],
        online_nodes=count_online_nodes(),
        nodes_by_role=nodes_by_role,
        role_capabilities=ROLE_CAPABILITIES,
        registered_capacity=ResourceTotals(
            logical_cpu_cores=aggregate[1],
            ram_bytes=aggregate[2],
            storage_total_bytes=aggregate[3],
            storage_available_bytes=aggregate[4],
            gpu_cores=aggregate[5],
            nodes_reporting_cpu=aggregate[6],
            nodes_reporting_ram=aggregate[7],
            nodes_reporting_storage=aggregate[8],
            nodes_reporting_gpu=aggregate[9],
        ),
    )


@app.put("/v1/admin/nodes/{node_id}/role", response_model=NodeResponse)
def assign_role(
    node_id: str,
    request: RoleRequest,
    admin_passcode: str | None = Header(default=None, alias="X-Admin-Passcode"),
    db: Session = Depends(get_db),
):
    require_admin(admin_passcode)

    node = db.get(Node, node_id)
    if node is None:
        raise HTTPException(status_code=404, detail="Node not found")
    if node.enrollment_status != EnrollmentStatus.APPROVED.value:
        raise HTTPException(status_code=409, detail="Approve node enrollment before assigning a role")
    node.role = request.role.value
    db.commit()
    return node_response(node)


@app.put("/v1/admin/nodes/{node_id}/enrollment", response_model=NodeResponse)
def decide_enrollment(
    node_id: str,
    request: EnrollmentRequest,
    admin_passcode: str | None = Header(default=None, alias="X-Admin-Passcode"),
    db: Session = Depends(get_db),
):
    require_admin(admin_passcode)

    node = db.get(Node, node_id)
    if node is None:
        raise HTTPException(status_code=404, detail="Node not found")
    node.enrollment_status = request.status.value
    db.commit()
    return node_response(node)


@app.post("/v1/admin/test-runs", response_model=TestRunResponse, status_code=201)
@limiter.limit("5/hour")
def create_test_run(
    request: Request,
    run_request: TestRunCreateRequest,
    admin_passcode: str | None = Header(default=None, alias="X-Admin-Passcode"),
    db: Session = Depends(get_db),
):
    require_admin(admin_passcode)
    candidate_ids = get_recent_online_node_ids(MAX_TEST_NODE_CANDIDATES)
    online_nodes = db.scalars(
        select(Node)
        .where(
            Node.id.in_(candidate_ids),
            Node.role == NodeRole.COMPUTE.value,
            Node.enrollment_status == EnrollmentStatus.APPROVED.value,
        )
        .order_by(Node.registered_at)
    ).all()
    if len(online_nodes) < run_request.shard_count:
        raise HTTPException(
            status_code=409,
            detail=f"Test needs {run_request.shard_count} distinct online compute nodes; found {len(online_nodes)}",
        )
    if run_request.total_items < run_request.shard_count:
        raise HTTPException(
            status_code=422,
            detail="total_items must be at least the number of shards",
        )

    active_task_counts = dict(
        db.execute(
            select(TestShard.node_id, func.count(TestShard.id))
            .where(TestShard.status.in_(("queued", "claimed")))
            .group_by(TestShard.node_id)
        ).all()
    )
    try:
        selected_nodes = choose_compute_nodes(
            online_nodes, active_task_counts, run_request.shard_count
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    now = datetime.now(timezone.utc)
    run = TestRun(
        id=str(uuid.uuid4()),
        status="running",
        total_items=run_request.total_items,
        shard_count=run_request.shard_count,
        created_at=now,
    )
    db.add(run)
    base_size, remainder = divmod(run.total_items, run.shard_count)
    start = 0
    shards = []
    for index in range(run.shard_count):
        size = base_size + (1 if index < remainder else 0)
        shard = TestShard(
            id=str(uuid.uuid4()),
            run_id=run.id,
            shard_index=index,
            node_id=selected_nodes[index].id,
            start_inclusive=start,
            end_exclusive=start + size,
            status="queued",
        )
        shards.append(shard)
        start += size
    db.add_all(shards)
    db.commit()
    return test_run_response(db, run)


@app.get("/v1/admin/test-runs/{run_id}", response_model=TestRunResponse)
def get_test_run(
    run_id: str,
    admin_passcode: str | None = Header(default=None, alias="X-Admin-Passcode"),
    db: Session = Depends(get_db),
):
    require_admin(admin_passcode)
    run = db.get(TestRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Test run not found")
    fail_expired_test_run(db, run)
    return test_run_response(db, run)


@app.post(
    "/v1/nodes/{node_id}/test-tasks/claim",
    response_model=ClaimedTestTaskResponse,
    status_code=200,
)
@limiter.limit("30/minute")
def claim_test_task(
    request: Request,
    node_id: str,
    claim_request: TestTaskClaimRequest,
    db: Session = Depends(get_db),
):
    node = db.get(Node, node_id)
    if (
        node is None
        or node.role != NodeRole.COMPUTE.value
        or node.enrollment_status != EnrollmentStatus.APPROVED.value
    ):
        raise HTTPException(status_code=403, detail="An approved compute node is required")
    verify_and_consume_challenge(
        claim_request.challenge_id,
        node.public_key,
        claim_request.signature,
        "claim_test_task",
        node_id,
    )
    shard = db.scalar(
        select(TestShard)
        .where(TestShard.node_id == node_id, TestShard.status == "queued")
        .order_by(TestShard.run_id, TestShard.shard_index)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if shard is None:
        return Response(status_code=204)
    run = db.scalar(
        select(TestRun)
        .where(TestRun.id == shard.run_id)
        .with_for_update()
    )
    if run is None or fail_expired_test_run(db, run) or run.status != "running":
        return Response(status_code=204)
    shard.status = "claimed"
    shard.claimed_at = datetime.now(timezone.utc)
    db.commit()
    return ClaimedTestTaskResponse(
        task_id=shard.id,
        run_id=shard.run_id,
        shard_index=shard.shard_index,
        start_inclusive=shard.start_inclusive,
        end_exclusive=shard.end_exclusive,
    )


@app.post(
    "/v1/nodes/{node_id}/test-tasks/{task_id}/complete",
    response_model=TestTaskCompletionResponse,
)
def complete_test_task(
    node_id: str,
    task_id: str,
    result_request: TestTaskResultRequest,
    db: Session = Depends(get_db),
):
    node = db.get(Node, node_id)
    shard = db.get(TestShard, task_id)
    if node is None or shard is None:
        raise HTTPException(status_code=404, detail="Node or test task not found")
    if (
        node.role != NodeRole.COMPUTE.value
        or node.enrollment_status != EnrollmentStatus.APPROVED.value
        or shard.node_id != node_id
    ):
        raise HTTPException(status_code=403, detail="Test task is not assigned to this compute node")
    run = db.scalar(
        select(TestRun)
        .where(TestRun.id == shard.run_id)
        .with_for_update()
    )
    if run is None or fail_expired_test_run(db, run) or run.status != "running":
        raise HTTPException(status_code=409, detail="Test run is no longer active")
    context = f"{task_id}:{result_request.item_count}:{result_request.sum_squares}"
    verify_and_consume_challenge(
        result_request.challenge_id,
        node.public_key,
        result_request.signature,
        "complete_test_task",
        node_id,
        context,
    )
    if shard.status != "claimed" or shard.claimed_at is None:
        raise HTTPException(status_code=409, detail="Test task is not claimed")

    expected_count = shard.end_exclusive - shard.start_inclusive
    expected_sum = (
        sum_squares_before(shard.end_exclusive)
        - sum_squares_before(shard.start_inclusive)
    )
    if result_request.item_count != expected_count or result_request.sum_squares != expected_sum:
        shard.status = "failed"
        run.status = "failed"
        db.commit()
        raise HTTPException(status_code=422, detail="Test result did not match the assigned range")

    now = datetime.now(timezone.utc)
    shard.status = "completed"
    shard.completed_at = now
    shard.result_sum = result_request.sum_squares
    shard.duration_ms = max(1, int((now - shard.claimed_at.replace(tzinfo=timezone.utc)).total_seconds() * 1000))
    completed_count = db.scalar(
        select(func.count()).select_from(TestShard).where(
            TestShard.run_id == run.id,
            TestShard.status == "completed",
        )
    )
    if completed_count == run.shard_count:
        run.status = "completed"
        run.completed_at = now
    db.commit()
    return TestTaskCompletionResponse(
        task_id=shard.id,
        status=shard.status,
        verified=True,
        duration_ms=shard.duration_ms,
    )