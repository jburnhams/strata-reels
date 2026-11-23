"""The application server: `./app serve` starts the FastAPI backend and serves the built React app.

    ./app serve [--host 127.0.0.1] [--port 8360]

From strata-python: GET /api/health, GET and PUT /api/settings, GET /api/system, /api/docs, /api/openapi.json, the
error answers ({detail, payload}) and the web app's pages. This app's own routes:

    GET /api/sources                  every source, oldest first
    POST /api/sources {path}          add footage: 201 when new, 200 when it is already a source, 400 when it cannot be one;
                                      a source is queued for ingest (its `hash`
                                      job, which queues the proxy, thumbnails and peaks)

The server runs the job queue (`app.jobs.Runner`) while it is up: jobs start once the database is open.

Every other path serves the web app (`src/app/server/static/index.html` when built with `cd web && npm run build`,
else the plain stand-in `landing.html`), so a reload or a shared link to /settings?tab=appearance opens that screen.
The routes are typed (response models from `sources`), so `./app openapi` writes `web/openapi.json` and the web app's
types are generated from it."""

import contextlib
import os
from collections.abc import AsyncGenerator, Sequence
from typing import Any, Literal

from fastapi import FastAPI, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session
from strata_python import web
from strata_python.db.web import DbSession, add_database

from app import __version__, db, jobs, settings, sources, system

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "static")
LANDING = os.path.join(HERE, "landing.html")
COMMIT = web.source_commit(__file__)  # None when installed from a wheel


class SourceIn(BaseModel):
    path: str


def create_app(*, run_jobs: Literal["processes", "threads", "off"] = "processes") -> FastAPI:
    """The server. Jobs run in job processes of their own; the unit tests run them on threads or not at all
    (`"off"` leaves them queued), since a unit test starts no processes."""
    app = web.base_app("strata-reels", __version__, settings.SETTINGS, commit=COMMIT)
    # A SourceError is a ValueError (400) and a missing record a KeyError (404): no try/except in the routes.
    web.add_error_handlers(app)
    # Opened and migrated on the first request that needs it, so a server nobody records on makes no database.
    records = add_database(app, db.PROJECT, default=db.default_url)
    if run_jobs != "off":
        _run_jobs(app, jobs.Runner(records, isolate=run_jobs == "processes"))

    def numbers() -> dict[str, object]:
        with Session(records.engine()) as s:
            n = sources.count(s)
        return system.AppNumbers(sources=n, home_bytes=system.folder_bytes(settings.SETTINGS.home())).model_dump()

    web.add_system(app, __version__, commit=COMMIT, folders={"home": settings.SETTINGS.home}, extra=numbers)

    @app.get("/api/sources")
    def get_sources(s: DbSession) -> list[sources.Described]:
        return [sources.Described.of(x) for x in sources.all_sources(s)]

    @app.post("/api/sources", status_code=201)
    def post_source(b: SourceIn, s: DbSession, response: Response) -> sources.Described:
        source, new = sources.add(s, b.path)  # a SourceError is a ValueError: 400 with its sentence
        jobs.ingest_later(jobs.queue(records.engine()), source)
        if not new:
            response.status_code = 200
        return sources.Described.of(source)

    web.add_web(app, STATIC, LANDING)  # last: its catch-all serves the web app at every other path
    return app


def _run_jobs(app: FastAPI, runner: jobs.Runner) -> None:
    """Start the runner with the server and stop it before the database is closed (add_database's lifespan is inside
    this one)."""
    inner = app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def lifespan(a: Any) -> AsyncGenerator[Any]:
        async with inner(a) as state:
            runner.start()
            try:
                yield state
            finally:
                runner.stop()

    app.router.lifespan_context = lifespan


def main(argv: Sequence[str] | None = None) -> None:
    web.serve(create_app(), argv, default_port=8360, banner=f"settings and records: {settings.SETTINGS.home()}")
