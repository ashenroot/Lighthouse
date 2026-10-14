"""HTTP API. The relay serves JSON and accepts pushes; it renders nothing."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from . import __version__
from .config import Config
from .relay import Relay

_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def authorize(request: Request, slot: str, relay: Relay) -> str:
    """Return the matching token's name, or raise 401 (no or bad token) or 403 (wrong slot)."""
    scheme, _, value = request.headers.get("authorization", "").partition(" ")
    value = value.strip()
    if scheme.lower() != "bearer" or not value:
        raise HTTPException(401, "bearer token required", headers={"WWW-Authenticate": "Bearer"})
    matched = relay.tokens.verify(value)
    if matched is None:
        raise HTTPException(401, "invalid token", headers={"WWW-Authenticate": "Bearer"})
    if "*" not in matched["slots"] and slot not in matched["slots"]:
        raise HTTPException(403, "token is not allowed to write this slot")
    return matched["name"]


def _etag(body: dict) -> str:
    stable = {k: v for k, v in body.items() if k != "generated_at"}
    digest = hashlib.sha1(json.dumps(stable, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return f'"{digest[:16]}"'


def create_app(config: Config, relay: Relay | None = None, start_background: bool = True) -> FastAPI:
    relay = relay or Relay(config)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if start_background:
            relay.start()
        yield
        await relay.stop()

    app = FastAPI(title="Fresnel", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.relay = relay

    if config.server.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=config.server.cors_origins,
            allow_methods=["GET"],
            allow_headers=["If-None-Match"],  # the board revalidates snapshots with its ETag
            expose_headers=["ETag"],
        )

    board_dir = Path(config.server.board_dir).expanduser() if config.server.board_dir else None
    if board_dir is not None and not (board_dir / "index.html").is_file():
        raise ValueError(f"server.board_dir has no index.html: {board_dir}")

    if board_dir is None:

        @app.get("/")
        def index():
            return {"name": "Fresnel", "version": __version__, "snapshot": "/v1/snapshot", "health": "/v1/health"}
    else:

        @app.middleware("http")
        async def board_headers(request: Request, call_next):
            response = await call_next(request)
            response.headers.setdefault(
                "Content-Security-Policy",
                "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'",
            )
            response.headers.setdefault("X-Content-Type-Options", "nosniff")
            return response

    @app.get("/v1/snapshot")
    def snapshot(request: Request):
        body = relay.snapshot()
        etag = _etag(body)
        headers = {"ETag": etag, "Cache-Control": "no-cache"}
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=headers)
        return JSONResponse(body, headers=headers)

    @app.get("/v1/sources/{source_id}")
    def source(source_id: str):
        if source_id not in relay.sources:
            raise HTTPException(404, "unknown source")
        return relay.envelope(source_id)

    @app.get("/v1/health")
    def health():
        return relay.health()

    @app.get("/v1/brief")
    def brief():
        return relay.push_envelope("brief")

    @app.get("/v1/brief/{day}")
    def brief_day(day: str):
        env = relay.archived_envelope("brief", day) if _DAY.match(day) else None
        if env is None:
            raise HTTPException(404, "no brief for that day")
        return env

    @app.get("/v1/push/{slot}")
    def get_push(slot: str):
        if slot not in config.push:
            raise HTTPException(404, "unknown slot")
        return relay.push_envelope(slot)

    @app.put("/v1/push/{slot}")
    async def put_push(slot: str, request: Request):
        if slot not in config.push:
            raise HTTPException(404, "unknown slot")
        if not relay.tokens.any():
            raise HTTPException(503, "push is disabled: no tokens exist yet (run: fresnel token add)")
        name = authorize(request, slot, relay)

        limit = config.push[slot].max_bytes
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > limit:
            raise HTTPException(413, f"body exceeds {limit} bytes")
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > limit:
                raise HTTPException(413, f"body exceeds {limit} bytes")
            chunks.append(chunk)
        try:
            raw = json.loads(b"".join(chunks))
        except ValueError:
            raise HTTPException(400, "body is not valid JSON")

        try:
            return relay.push(slot, raw, name)
        except ValidationError as exc:
            problems = [{"at": ".".join(str(p) for p in e["loc"]), "problem": e["msg"]} for e in exc.errors()]
            raise HTTPException(422, problems[:20])

    if board_dir is not None:  # mounted last so the /v1 routes above take precedence
        app.mount("/", StaticFiles(directory=board_dir, html=True), name="board")

    return app
