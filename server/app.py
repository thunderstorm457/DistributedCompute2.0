"""Minimal provisioning API for the DistributedCompute network foundation.

This prototype registers nodes, assigns the compute role, and accepts authenticated
heartbeats. It deliberately does not execute workloads or grant infrastructure roles.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field


class HardwareReport(BaseModel):
    """Hardware details reported by a node during registration."""

    model_config = ConfigDict(extra="forbid")

    cpu_cores: int | None = Field(default=None, ge=1, le=1024)
    memory_gb: float | None = Field(default=None, gt=0, le=65536)
    accelerators: list[str] = Field(default_factory=list, max_length=32)


class NodeRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    hardware: HardwareReport = Field(default_factory=HardwareReport)


class NodeRegistrationResponse(BaseModel):
    node_id: str
    role: str
    access_token: str
    created_at: datetime


class NodeResponse(BaseModel):
    node_id: str
    name: str
    role: str
    hardware: HardwareReport
    status: str
    created_at: datetime
    last_seen: datetime


class NodeStore:
    def __init__(self, database_path: str) -> None:
        self.database_path = database_path
        if database_path != ":memory:":
            Path(database_path).parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS nodes (
                    node_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    role TEXT NOT NULL,
                    hardware_json TEXT NOT NULL,
                    token_hash TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    last_seen TEXT NOT NULL
                )"""
            )

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def allocate_role(_hardware: HardwareReport) -> str:
    """Assign the only role currently supported by the MVP.

    Infrastructure roles must not be granted automatically until authorization,
    operational safeguards, and an administrator workflow are implemented.
    """

    return "compute"


def create_app(database_path: str | None = None) -> FastAPI:
    path = database_path or os.environ.get("DISTRIBUTEDCOMPUTE_DB", "distributedcompute.sqlite3")
    store = NodeStore(path)
    app = FastAPI(
        title="DistributedCompute Provisioning API",
        version="0.1.0",
        description="Prototype node registration and heartbeat service.",
    )
    bearer = HTTPBearer(auto_error=False)

    def authenticated_node(
        node_id: str,
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    ) -> sqlite3.Row:
        if credentials is None or credentials.scheme.lower() != "bearer":
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bearer token required")
        with store.connection() as connection:
            row = connection.execute("SELECT * FROM nodes WHERE node_id = ?", (node_id,)).fetchone()
        if row is None or not hmac.compare_digest(row["token_hash"], token_digest(credentials.credentials)):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid node credentials")
        return row

    @app.get("/healthz", tags=["health"])
    def health_check() -> dict[str, str]:
        return {"status": "ok"}

    @app.post(
        "/api/v1/nodes",
        response_model=NodeRegistrationResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["nodes"],
    )
    def register_node(registration: NodeRegistration) -> NodeRegistrationResponse:
        now = utc_now()
        node_id = str(uuid.uuid4())
        access_token = secrets.token_urlsafe(32)
        role = allocate_role(registration.hardware)
        with store.connection() as connection:
            connection.execute(
                """INSERT INTO nodes
                   (node_id, name, role, hardware_json, token_hash, status, created_at, last_seen)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    node_id,
                    registration.name,
                    role,
                    registration.hardware.model_dump_json(),
                    token_digest(access_token),
                    "online",
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
        return NodeRegistrationResponse(
            node_id=node_id,
            role=role,
            access_token=access_token,
            created_at=now,
        )

    @app.get("/api/v1/nodes/{node_id}", response_model=NodeResponse, tags=["nodes"])
    def get_node(node_id: str, row: sqlite3.Row = Depends(authenticated_node)) -> NodeResponse:
        from pydantic import TypeAdapter

        hardware = TypeAdapter(HardwareReport).validate_json(row["hardware_json"])
        return NodeResponse(
            node_id=row["node_id"],
            name=row["name"],
            role=row["role"],
            hardware=hardware,
            status=row["status"],
            created_at=datetime.fromisoformat(row["created_at"]),
            last_seen=datetime.fromisoformat(row["last_seen"]),
        )

    @app.post("/api/v1/nodes/{node_id}/heartbeat", tags=["nodes"])
    def heartbeat(node_id: str, _row: sqlite3.Row = Depends(authenticated_node)) -> dict[str, str]:
        now = utc_now().isoformat()
        with store.connection() as connection:
            connection.execute(
                "UPDATE nodes SET status = ?, last_seen = ? WHERE node_id = ?",
                ("online", now, node_id),
            )
        return {"node_id": node_id, "status": "online", "last_seen": now}

    return app


app = create_app()
