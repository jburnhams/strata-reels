"""The application server: `./app serve` starts the FastAPI backend and serves the built React app.

    ./app serve [--host 127.0.0.1] [--port 8360]

From strata-python: GET /api/health, GET and PUT /api/settings, GET /api/system, /api/docs, /api/openapi.json, the
error answers ({detail, payload}) and the web app's pages. This app's own routes:

    GET /api/sources                  every source, oldest first
    POST /api/sources {path}          add footage: 201 when new, 200 when it is already a source, 400 when it cannot be one;
                                      its tape's analysis is queued (`jobs.TAPE`, one job
                                      that runs every stage not yet done)
    GET /api/sources/{id}             one source (404 when there is none)
    GET /api/sources/{id}/scenes      its scenes with their pictures, speech, descriptions and faces, as far as the
                                      jobs have got (`browse.Scenes`; `waiting` says what for while there are none)
    GET /api/sources/{id}/transcript  its speech, segment by segment with word timings
    GET /api/sources/{id}/peaks?buckets=4000   its waveform, reduced to that many slices (404 before it is made)
    GET /api/sources/{id}/proxy       the browser copy, with range requests so the player can seek
    GET /api/sources/{id}/thumbnails/{name}, .../keyframes/{name}   a picture named in its result

From strata-python's `add_jobs`: GET /api/jobs (newest first, filtered by state, kind or domain), GET
/api/jobs/counts, GET /api/jobs/{id}, POST /api/jobs/{id}/cancel and POST /api/jobs/{id}/retry, which strata-react's
`JobsTable` reads.

From strata-python's `add_workflow`, the tape pipeline's steps (subject `source:<id>`), which strata-react's
`WorkflowSteps` draws:

    GET /api/workflows/tape/subjects                    every tape's run (`?state=failed` for the failed ones)
    GET /api/workflows/tape/subjects/{subject}          each stage's state and last record, and the job advancing it
    GET /api/workflows/tape/subjects/{subject}/stages/{stage}   that stage's history
    POST /api/workflows/tape/subjects/{subject}/run     queue the tape's job again (a retry after a failure)
    POST .../stages/{stage}/clear                       forget a stage and what follows it, then run

The server runs the job queue (`app.jobs.Runner`) while it is up: jobs start once the database is open.

Every other path serves the web app (`src/app/server/static/index.html` when built with `cd web && npm run build`,
else the plain stand-in `landing.html`), so a reload or a shared link to /settings?tab=appearance opens that screen.
The routes are typed (response models from `sources`), so `./app openapi` writes `web/openapi.json` and the web app's
types are generated from it."""

import contextlib
import os
from collections.abc import AsyncGenerator, Sequence
from typing import Annotated, Any, Literal

from fastapi import FastAPI, Query, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from strata_python import web
from strata_python.db.web import DbSession, add_database
from strata_python.tasks.web import add_jobs
from strata_python.workflow.store import DbStore
from strata_python.workflow.web import add_workflow

from app import __version__, browse, db, jobs, settings, sources, system

# A thumbnail or keyframe URL carries its result's key (`?v=`, browse.py), so a new recipe is a new URL and the browser
# may keep the old one for good. The proxy's URL is the tape's, whichever proxy is current, so it is checked each time.
IMMUTABLE = "public, max-age=31536000, immutable"
REVALIDATE = "no-cache"

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

    def one(s: Session, source_id: int) -> sources.Source:
        found = s.get(sources.Source, source_id)
        if found is None:
            raise KeyError(f"there is no source {source_id}")
        return found

    @app.get("/api/sources/{source_id}")
    def get_source(source_id: int, s: DbSession) -> sources.Described:
        return sources.Described.of(one(s, source_id))

    @app.get("/api/sources/{source_id}/scenes")
    def get_scenes(source_id: int, s: DbSession) -> browse.Scenes:
        return browse.scenes_of(s, one(s, source_id))

    @app.get("/api/sources/{source_id}/transcript")
    def get_transcript(source_id: int, s: DbSession) -> browse.Transcript:
        return browse.transcript_of(s, one(s, source_id))

    @app.get("/api/sources/{source_id}/peaks")
    def get_peaks(
        source_id: int,
        s: DbSession,
        buckets: Annotated[int, Query(ge=1, le=browse.MAX_BUCKETS)] = browse.PEAK_BUCKETS,
    ) -> browse.Peaks:
        return browse.peaks_of(s, one(s, source_id), buckets)

    @app.get("/api/sources/{source_id}/proxy", response_class=FileResponse)
    def get_proxy(source_id: int, s: DbSession) -> FileResponse:
        # Starlette answers Range requests from the file, which the <video> needs to seek.
        return FileResponse(
            browse.file_of(s, one(s, source_id), "proxy"), media_type="video/mp4", headers={"Cache-Control": REVALIDATE}
        )

    @app.get("/api/sources/{source_id}/thumbnails/{name}", response_class=FileResponse)
    def get_thumbnail(source_id: int, name: str, s: DbSession) -> FileResponse:
        path = browse.file_of(s, one(s, source_id), "thumbnails", name)
        return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": IMMUTABLE})

    @app.get("/api/sources/{source_id}/keyframes/{name}", response_class=FileResponse)
    def get_keyframe(source_id: int, name: str, s: DbSession) -> FileResponse:
        path = browse.file_of(s, one(s, source_id), "keyframes", name)
        return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": IMMUTABLE})

    add_jobs(app, lambda: jobs.queue(records.engine()))
    add_workflow(
        app,
        jobs.TAPE,
        store=lambda: DbStore(records.engine(), jobs.STAGES, jobs.TAPE),
        inputs=lambda subject: jobs.inputs_in(records.engine(), subject),
        queue=lambda: jobs.queue(records.engine()),
    )
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
