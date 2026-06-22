from __future__ import annotations

import json
import os
import secrets
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field


DB_PATH = Path(os.environ.get("NISIA_DB", "/opt/nisia-agent-server/data/agent.db"))
PUBLIC_DIR = Path(os.environ.get("NISIA_PUBLIC_DIR", "/opt/nisia-agent-server/public"))
INSTALL_TOKEN = os.environ.get("NISIA_INSTALL_TOKEN", "")
ADMIN_TOKEN = os.environ.get("NISIA_ADMIN_TOKEN", "")
OPEN_REGISTRATION = os.environ.get("NISIA_OPEN_REGISTRATION", "0") == "1"
PROTOCOL_VERSION = "2026-06-22.1"
DEVICE_PENDING = "pending"
DEVICE_APPROVED = "approved"
DEVICE_REVOKED = "revoked"

app = FastAPI(title="NISIA OpenWrt Agent Backend", version=PROTOCOL_VERSION)


class RegisterRequest(BaseModel):
    install_token: str
    hostname: str = ""
    model: str = ""
    openwrt_version: str = ""
    agent_version: str = ""
    capabilities: list[str] = Field(default_factory=list)


class RegisterResponse(BaseModel):
    device_id: str
    device_token: str
    poll_interval: int = 30
    protocol_version: str = PROTOCOL_VERSION


class HeartbeatRequest(BaseModel):
    agent_version: str = ""
    openwrt_version: str = ""
    wan_ip: str = ""
    uptime: str = ""
    capabilities: list[str] = Field(default_factory=list)


class SnapshotRequest(BaseModel):
    snapshot: dict[str, Any]


class TaskCreateRequest(BaseModel):
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)


class TaskResultRequest(BaseModel):
    status: str
    output: dict[str, Any] = Field(default_factory=dict)
    error: str = ""


def now() -> int:
    return int(time.time())


def db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS devices (
                id TEXT PRIMARY KEY,
                token TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                hostname TEXT NOT NULL DEFAULT '',
                model TEXT NOT NULL DEFAULT '',
                openwrt_version TEXT NOT NULL DEFAULT '',
                agent_version TEXT NOT NULL DEFAULT '',
                capabilities TEXT NOT NULL DEFAULT '[]',
                first_seen INTEGER NOT NULL,
                last_seen INTEGER NOT NULL,
                last_ip TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
                created_at INTEGER NOT NULL,
                snapshot TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
                type TEXT NOT NULL,
                payload TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'pending',
                created_at INTEGER NOT NULL,
                leased_at INTEGER,
                completed_at INTEGER,
                result TEXT NOT NULL DEFAULT '{}',
                error TEXT NOT NULL DEFAULT ''
            );
            """
        )
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(devices)").fetchall()}
        if "status" not in columns:
            conn.execute("ALTER TABLE devices ADD COLUMN status TEXT NOT NULL DEFAULT 'pending'")


@app.on_event("startup")
def startup() -> None:
    init_db()


def require_admin(x_admin_token: str | None) -> None:
    if not ADMIN_TOKEN:
        raise HTTPException(status_code=500, detail="admin token is not configured")
    if not x_admin_token or not secrets.compare_digest(x_admin_token, ADMIN_TOKEN):
        raise HTTPException(status_code=401, detail="invalid admin token")


def bearer_token(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    return authorization.split(" ", 1)[1].strip()


def require_device(device_id: str, authorization: str | None) -> sqlite3.Row:
    token = bearer_token(authorization)
    with db() as conn:
        row = conn.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()
    if not row or not secrets.compare_digest(row["token"], token):
        raise HTTPException(status_code=401, detail="invalid device token")
    return row


def row_to_device(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "status": row["status"],
        "hostname": row["hostname"],
        "model": row["model"],
        "openwrt_version": row["openwrt_version"],
        "agent_version": row["agent_version"],
        "capabilities": json.loads(row["capabilities"] or "[]"),
        "first_seen": row["first_seen"],
        "last_seen": row["last_seen"],
        "last_ip": row["last_ip"],
    }


def row_to_task(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "device_id": row["device_id"],
        "type": row["type"],
        "payload": json.loads(row["payload"] or "{}"),
        "status": row["status"],
        "created_at": row["created_at"],
        "leased_at": row["leased_at"],
        "completed_at": row["completed_at"],
        "result": json.loads(row["result"] or "{}"),
        "error": row["error"],
    }


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return {"ok": True, "protocol_version": PROTOCOL_VERSION}


@app.get("/download/nisia-agent")
def download_agent() -> FileResponse:
    path = PUBLIC_DIR / "nisia-agent"
    if not path.exists():
        raise HTTPException(status_code=404, detail="agent script is not published")
    return FileResponse(path, media_type="text/plain", filename="nisia-agent")


@app.get("/download/nisia-agent-init")
def download_agent_init() -> FileResponse:
    path = PUBLIC_DIR / "nisia-agent-init"
    if not path.exists():
        raise HTTPException(status_code=404, detail="agent init script is not published")
    return FileResponse(path, media_type="text/plain", filename="nisia-agent-init")


@app.get("/install.sh")
def download_install_script() -> FileResponse:
    path = PUBLIC_DIR / "install.sh"
    if not path.exists():
        raise HTTPException(status_code=404, detail="install script is not published")
    return FileResponse(path, media_type="text/x-shellscript", filename="install.sh")


@app.post("/api/v1/register", response_model=RegisterResponse)
async def register(payload: RegisterRequest, request: Request) -> RegisterResponse:
    if not OPEN_REGISTRATION and not INSTALL_TOKEN:
        raise HTTPException(status_code=500, detail="install token is not configured")
    if not OPEN_REGISTRATION and not secrets.compare_digest(payload.install_token, INSTALL_TOKEN):
        raise HTTPException(status_code=401, detail="invalid install token")

    device_id = str(uuid.uuid4())
    device_token = secrets.token_urlsafe(32)
    ts = now()
    with db() as conn:
        conn.execute(
            """
            INSERT INTO devices
                (id, token, status, hostname, model, openwrt_version, agent_version, capabilities, first_seen, last_seen, last_ip)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                device_id,
                device_token,
                DEVICE_PENDING,
                payload.hostname,
                payload.model,
                payload.openwrt_version,
                payload.agent_version,
                json.dumps(payload.capabilities, ensure_ascii=False),
                ts,
                ts,
                request.client.host if request.client else "",
            ),
        )
    return RegisterResponse(device_id=device_id, device_token=device_token)


@app.post("/api/v1/devices/{device_id}/heartbeat")
async def heartbeat(
    device_id: str,
    payload: HeartbeatRequest,
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    require_device(device_id, authorization)
    with db() as conn:
        conn.execute(
            """
            UPDATE devices
            SET last_seen = ?, last_ip = ?, agent_version = ?, openwrt_version = ?, capabilities = ?
            WHERE id = ?
            """,
            (
                now(),
                request.client.host if request.client else "",
                payload.agent_version,
                payload.openwrt_version,
                json.dumps(payload.capabilities, ensure_ascii=False),
                device_id,
            ),
        )
    return {"ok": True, "server_time": now()}


@app.post("/api/v1/devices/{device_id}/snapshot")
async def snapshot(
    device_id: str,
    payload: SnapshotRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    require_device(device_id, authorization)
    with db() as conn:
        conn.execute(
            "INSERT INTO snapshots (device_id, created_at, snapshot) VALUES (?, ?, ?)",
            (device_id, now(), json.dumps(payload.snapshot, ensure_ascii=False)),
        )
        conn.execute("UPDATE devices SET last_seen = ? WHERE id = ?", (now(), device_id))
    return {"ok": True}


@app.get("/api/v1/devices/{device_id}/tasks/next")
async def next_task(device_id: str, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    device = require_device(device_id, authorization)
    if device["status"] != DEVICE_APPROVED:
        return {"task": None, "device_status": device["status"]}

    with db() as conn:
        row = conn.execute(
            """
            SELECT * FROM tasks
            WHERE device_id = ? AND status = 'pending'
            ORDER BY created_at ASC
            LIMIT 1
            """,
            (device_id,),
        ).fetchone()
        if not row:
            return {"task": None}
        conn.execute(
            "UPDATE tasks SET status = 'leased', leased_at = ? WHERE id = ?",
            (now(), row["id"]),
        )
    task = row_to_task(row)
    task["status"] = "leased"
    task["leased_at"] = now()
    return {"task": task}


@app.post("/api/v1/devices/{device_id}/tasks/{task_id}/result")
async def task_result(
    device_id: str,
    task_id: str,
    payload: TaskResultRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    require_device(device_id, authorization)
    status = payload.status if payload.status in {"done", "failed"} else "failed"
    with db() as conn:
        conn.execute(
            """
            UPDATE tasks
            SET status = ?, completed_at = ?, result = ?, error = ?
            WHERE id = ? AND device_id = ?
            """,
            (
                status,
                now(),
                json.dumps(payload.output, ensure_ascii=False),
                payload.error,
                task_id,
                device_id,
            ),
        )
    return {"ok": True}


@app.get("/api/v1/admin/devices")
async def admin_devices(x_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    require_admin(x_admin_token)
    with db() as conn:
        rows = conn.execute("SELECT * FROM devices ORDER BY last_seen DESC").fetchall()
    return {"devices": [row_to_device(row) for row in rows]}


@app.post("/api/v1/admin/devices/{device_id}/approve")
async def admin_approve_device(device_id: str, x_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    require_admin(x_admin_token)
    with db() as conn:
        cur = conn.execute(
            "UPDATE devices SET status = ? WHERE id = ?",
            (DEVICE_APPROVED, device_id),
        )
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="device not found")
    return {"ok": True, "device_id": device_id, "status": DEVICE_APPROVED}


@app.post("/api/v1/admin/devices/{device_id}/revoke")
async def admin_revoke_device(device_id: str, x_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    require_admin(x_admin_token)
    with db() as conn:
        cur = conn.execute(
            "UPDATE devices SET status = ? WHERE id = ?",
            (DEVICE_REVOKED, device_id),
        )
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="device not found")
    return {"ok": True, "device_id": device_id, "status": DEVICE_REVOKED}


@app.get("/api/v1/admin/devices/{device_id}")
async def admin_device(device_id: str, x_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    require_admin(x_admin_token)
    with db() as conn:
        device = conn.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()
        latest_snapshot = conn.execute(
            "SELECT * FROM snapshots WHERE device_id = ? ORDER BY created_at DESC LIMIT 1",
            (device_id,),
        ).fetchone()
    if not device:
        raise HTTPException(status_code=404, detail="device not found")
    return {
        "device": row_to_device(device),
        "latest_snapshot": {
            "created_at": latest_snapshot["created_at"],
            "snapshot": json.loads(latest_snapshot["snapshot"]),
        }
        if latest_snapshot
        else None,
    }


@app.get("/api/v1/admin/devices/{device_id}/tasks")
async def admin_tasks(device_id: str, x_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    require_admin(x_admin_token)
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE device_id = ? ORDER BY created_at DESC",
            (device_id,),
        ).fetchall()
    return {"tasks": [row_to_task(row) for row in rows]}


@app.post("/api/v1/admin/devices/{device_id}/tasks")
async def admin_create_task(
    device_id: str,
    payload: TaskCreateRequest,
    x_admin_token: str | None = Header(default=None),
) -> dict[str, Any]:
    require_admin(x_admin_token)
    task_id = str(uuid.uuid4())
    with db() as conn:
        device = conn.execute("SELECT id, status FROM devices WHERE id = ?", (device_id,)).fetchone()
        if not device:
            raise HTTPException(status_code=404, detail="device not found")
        if device["status"] != DEVICE_APPROVED:
            raise HTTPException(status_code=409, detail=f"device is {device['status']}; approve it before assigning tasks")
        conn.execute(
            """
            INSERT INTO tasks (id, device_id, type, payload, status, created_at)
            VALUES (?, ?, ?, ?, 'pending', ?)
            """,
            (task_id, device_id, payload.type, json.dumps(payload.payload, ensure_ascii=False), now()),
        )
    return {"ok": True, "task_id": task_id}
