"""HTTP API and the web page. Run with: agentheon serve (or uvicorn agentheon.api:app)."""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, text

from . import __version__
from .app import Platform, build
from .db import runs
from .watches import WatchError

log = logging.getLogger("agentheon.api")
STATIC = Path(__file__).parent / "static"


# ---------------------------------------------------------------- request models


class QueryIn(BaseModel):
    question: str = Field(min_length=1)
    skills: list[str] | None = None
    private: bool = False


class WatchIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    question: str = Field(min_length=1)
    skills: list[str] | None = None
    interval_minutes: int = Field(default=1440, ge=5)
    threshold: str = "notable"
    history_window: int = Field(default=5, ge=1, le=50)
    max_runs: int | None = Field(default=None, ge=1)


@dataclass
class User:
    id: str
    role: str


def create_app(platform: Platform | None = None) -> FastAPI:
    platform = platform or build()
    settings = platform.settings
    background: set[asyncio.Task] = set()

    def spawn(coro) -> None:
        task = asyncio.create_task(coro)
        background.add(task)
        task.add_done_callback(background.discard)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        scheduler = None
        if settings.run_scheduler:
            scheduler = asyncio.create_task(
                platform.watches.loop(settings.watch_poll_seconds, purge=platform.knowledge.purge_expired))
        yield
        if scheduler:
            scheduler.cancel()

    app = FastAPI(title="Agentheon", version=__version__, lifespan=lifespan)
    app.state.platform = platform

    # ------------------------------------------------------------ auth

    def current_user(request: Request) -> User:
        header = request.headers.get("authorization", "")
        if header.lower().startswith("bearer "):
            token = header[7:].strip()
            for key, (user_id, role) in settings.api_keys.items():
                if hmac.compare_digest(token, key):
                    return User(user_id, role)
            raise HTTPException(401, "Invalid API key")
        if settings.auth_dev_mode:
            return User("dev", "admin")
        raise HTTPException(401, "Missing API key. Send 'Authorization: Bearer <key>'.")

    def admin_user(user: User = Depends(current_user)) -> User:
        if user.role != "admin":
            raise HTTPException(403, "Admin only")
        return user

    def own_watch(watch_id: str, user: User) -> dict:
        watch = platform.watches.get(watch_id, None if user.role == "admin" else user.id)
        if not watch:
            raise HTTPException(404, "Watch not found")
        return watch

    # ------------------------------------------------------------ pages and health

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/v1/ready")
    def ready():
        try:
            with platform.db.connect() as conn:
                conn.execute(text("SELECT 1"))
            database = "ok"
        except Exception as exc:  # report, don't crash, so orchestrators see the reason
            database = f"error: {exc.__class__.__name__}"
        ok = database == "ok" and bool(platform.skills.all())
        body = {
            "status": "ok" if ok else "degraded",
            "version": __version__,
            "database": database,
            "model": settings.llm_model,
            "skills": len(platform.skills.all()),
            "tools": len(platform.tools.names()),
            "governance_rules": len(platform.policy.rules),
            "scheduler": settings.run_scheduler,
            "problems": platform.problems,
        }
        if not ok:
            raise HTTPException(503, body)
        return body

    @app.get("/api/v1/me")
    def me(user: User = Depends(current_user)):
        return {"id": user.id, "role": user.role}

    @app.get("/api/v1/skills")
    def list_skills(_: User = Depends(current_user)):
        return [{**s.card(), "version": s.version, "tools": s.tools} for s in platform.skills.all()]

    # ------------------------------------------------------------ queries

    @app.post("/api/v1/query")
    async def query(body: QueryIn, user: User = Depends(current_user)):
        return await platform.engine.run(body.question, user_id=user.id, skills=body.skills, private=body.private)

    @app.post("/api/v1/query/stream")
    async def query_stream(body: QueryIn, user: User = Depends(current_user)):
        queue: asyncio.Queue = asyncio.Queue()
        run_id = str(uuid.uuid4())
        platform.engine.record_start(run_id, user.id, body.question)

        async def emit(event: str, data: dict) -> None:
            await queue.put((event, data))

        # The run is a background task: if the client disconnects it still finishes and is saved,
        # so the result can be fetched later from /api/v1/query/result/{run_id}.
        spawn(platform.engine.run(body.question, user_id=user.id, skills=body.skills,
                                  private=body.private, emit=emit, run_id=run_id))

        async def events():
            yield _sse("started", {"run_id": run_id})
            while True:
                event, data = await queue.get()
                yield _sse(event, data)
                if event == "result":
                    break

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/api/v1/query/submit", status_code=202)
    async def query_submit(body: QueryIn, user: User = Depends(current_user)):
        run_id = str(uuid.uuid4())
        platform.engine.record_start(run_id, user.id, body.question)
        spawn(platform.engine.run(body.question, user_id=user.id, skills=body.skills,
                                  private=body.private, run_id=run_id))
        return {"run_id": run_id, "status": "running"}

    @app.get("/api/v1/query/result/{run_id}")
    def query_result(run_id: str, user: User = Depends(current_user)):
        with platform.db.connect() as conn:
            row = conn.execute(select(runs).where(runs.c.id == run_id)).mappings().first()
        if not row or (row["user_id"] != user.id and user.role != "admin"):
            raise HTTPException(404, "Run not found")
        if row["status"] == "running" or not row["result_json"]:
            return {"run_id": run_id, "status": "running"}
        return json.loads(row["result_json"])

    @app.get("/api/v1/runs")
    def list_runs(limit: int = 20, user: User = Depends(current_user)):
        query = select(runs.c.id, runs.c.question, runs.c.status, runs.c.created_at, runs.c.user_id)
        if user.role != "admin":
            query = query.where(runs.c.user_id == user.id)
        with platform.db.connect() as conn:
            rows = conn.execute(query.order_by(runs.c.created_at.desc()).limit(min(limit, 200))).mappings().all()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------ watches

    @app.post("/api/v1/watches", status_code=201)
    def create_watch(body: WatchIn, user: User = Depends(current_user)):
        try:
            return platform.watches.create(owner=user.id, **body.model_dump())
        except WatchError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/v1/watches")
    def list_watches(user: User = Depends(current_user)):
        return platform.watches.list(user.id)

    @app.get("/api/v1/watches/{watch_id}")
    def get_watch(watch_id: str, user: User = Depends(current_user)):
        return {**own_watch(watch_id, user), "runs": platform.watches.runs(watch_id)}

    @app.post("/api/v1/watches/{watch_id}/run-now")
    async def run_watch_now(watch_id: str, user: User = Depends(current_user)):
        own_watch(watch_id, user)
        return await platform.watches.run_once(watch_id)

    @app.post("/api/v1/watches/{watch_id}/pause")
    def pause_watch(watch_id: str, user: User = Depends(current_user)):
        own_watch(watch_id, user)
        platform.watches.set_active(watch_id, False)
        return {"id": watch_id, "active": False}

    @app.post("/api/v1/watches/{watch_id}/resume")
    def resume_watch(watch_id: str, user: User = Depends(current_user)):
        own_watch(watch_id, user)
        platform.watches.set_active(watch_id, True)
        return {"id": watch_id, "active": True}

    @app.delete("/api/v1/watches/{watch_id}", status_code=204)
    def delete_watch(watch_id: str, user: User = Depends(current_user)):
        own_watch(watch_id, user)
        platform.watches.delete(watch_id)

    # ------------------------------------------------------------ admin

    @app.post("/api/v1/admin/knowledge/purge")
    def purge_knowledge(_: User = Depends(admin_user)):
        return {"deleted": platform.knowledge.purge_expired()}

    return app


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def __getattr__(name: str):
    """`uvicorn agentheon.api:app` builds the app lazily, so importing this module has no side effects."""
    if name == "app":
        logging.basicConfig(level=logging.INFO)
        return create_app()
    raise AttributeError(name)
