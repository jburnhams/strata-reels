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
    PUT /api/sources/{id}/scenes/{n}/chapter-mark {start}   the person's word on whether a chapter starts at scene n
                                      (true, false, or null to take it back); queues the tape's job, which groups the
                                      chapters again; answers the scenes as they are now (`regrouping` true meanwhile)
    PUT /api/sources/{id}/scenes/{n}/chapter-title {title}  the person's title for the chapter that starts at scene n
                                      (null: the namer's again; 400 when none starts there now)
    GET /api/sources/{id}/transcript  its speech, segment by segment with word timings
    GET /api/sources/{id}/peaks?buckets=4000   its waveform, reduced to that many slices (404 before it is made)
    GET /api/sources/{id}/proxy       the browser copy, with range requests so the player can seek
    GET /api/sources/{id}/thumbnails/{name}, .../keyframes/{name}   a picture named in its result

Reels (`reels`, plan step 5): a tape's highlight reel as immutable revisions, proposed by the planner (`planner`)
and accepted by the person; pins, bans and locks are overrides on the reel that every plan keeps:

    GET /api/reels[?source=id]        every reel, with its current revision's length
    POST /api/reels {source_id, target_seconds, name?}   a new reel and its first proposal (404 before the scenes)
    GET /api/reels/{reel}             the reel, its current revision, open proposals and constraints
    GET /api/reels/{reel}/revisions/{rev}   one revision's clips (exact source ranges, with seconds for the page)
    POST /api/reels/{reel}/plan {seed?, target_seconds?}   a new proposal on the current revision
    POST /api/reels/{reel}/revisions/{rev}/accept   make it current: 409 when the reel moved on since it was made
    POST /api/reels/{reel}/revisions/{rev}/reject
    POST /api/reels/{reel}/constraints {kind: pin|ban, scene} or {kind: lock, clip}
    DELETE /api/reels/{reel}/constraints/{id}

Renders (`renders`, spec S8): a revision rendered from the tape by ffmpeg on the job queue, frame exact, with a manifest:

    POST /api/reels/{reel}/revisions/{rev}/render   queue its render (or find the one made from the same clips): 201
    GET /api/reels/{reel}/renders                   every render of the reel, newest first, with its job's state
    GET /api/reels/{reel}/renders/{id}              one, with its manifest once made
    GET /api/reels/{reel}/renders/{id}/file         the file, as a download named after the reel

From strata-python's `add_jobs`: GET /api/jobs (newest first, filtered by state, kind or domain), GET
/api/jobs/counts, GET /api/jobs/{id}, POST /api/jobs/{id}/cancel and POST /api/jobs/{id}/retry, which strata-react's
`JobsTable` reads.

From strata-python's `add_workflow`, the tape pipeline's steps (subject `source:<id>`), which strata-react's
`WorkflowSteps` draws:

    GET /api/workflows/tape/subjects                    every tape's run (`?state=failed` for the failed ones)
    GET /api/workflows/tape/subjects/{subject}          each stage's state and last record, and the job advancing it
    GET /api/workflows/tape/subjects/{subject}/stages/{stage}   that stage's history
    POST /api/workflows/tape/subjects/{subject}/run     queue the tape's job again (a retry after a failure)
    POST .../stages/check_chapters/approve              the person says the chapters are right (the gate)
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
from strata_python.client import ServiceClient
from strata_python.clients import models
from strata_python.db.web import DbSession, add_database
from strata_python.tasks.web import add_jobs
from strata_python.workflow.store import DbStore
from strata_python.workflow.web import add_workflow

from app import __version__, browse, chapters, db, jobs, planner, reels, renders, settings, sources, system

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


class MarkIn(BaseModel):
    """Whether a chapter starts at the scene: True starts one, False keeps it with the chapter before, None leaves it
    to the grouping (the mark taken back)."""

    start: bool | None


class TitleIn(BaseModel):
    """The person's title for a chapter; None goes back to the name the namer gave it."""

    title: str | None


class ReelIn(BaseModel):
    """A new reel of a tape: its length in seconds, and a name (else the tape's and the length)."""

    source_id: int
    target_seconds: float
    name: str | None = None


class PlanIn(BaseModel):
    """Plan the reel again: another seed for another version, or another length (which becomes the reel's)."""

    seed: int = 0
    target_seconds: float | None = None


class ConstraintIn(BaseModel):
    """A pin or ban names a scene (from 0, the tape's scenes now), a lock a clip of one of the reel's revisions."""

    kind: planner.Kind
    scene: int | None = None
    clip: str | None = None


WHO = "person"  # the apps have no logins yet: the same word the shared workflow routes record


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

    # Annotated with its model, so the schema names the page's fields (`StrataReelsSystemInfo`) and the web app's
    # type is generated, not written beside it.
    def numbers() -> system.AppNumbers:
        with Session(records.engine()) as s:
            n = sources.count(s)
        return system.AppNumbers(sources=n, home_bytes=system.folder_bytes(settings.SETTINGS.home()))

    # strata-models does every analysis step, so the System page says whether it is up (its health, probed per read).
    web.add_system(
        app,
        __version__,
        commit=COMMIT,
        folders={"home": settings.SETTINGS.home},
        services={"strata-models": ServiceClient(models.url, app="strata-reels")},
        extra=numbers,
    )

    @app.get("/api/sources")
    def get_sources(s: DbSession) -> list[sources.Described]:
        return [sources.Described.of(x) for x in sources.all_sources(s)]

    @app.post("/api/sources", status_code=201)
    def post_source(b: SourceIn, s: DbSession, response: Response) -> sources.Described:
        source, new = sources.add(s, b.path)  # a SourceError is a ValueError: 400 with its sentence
        jobs.ingest_later(jobs.queue(records.engine()), source, again=not new, by="person")
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

    @app.put("/api/sources/{source_id}/scenes/{scene}/chapter-mark")
    def put_chapter_mark(source_id: int, scene: int, b: MarkIn, s: DbSession) -> browse.Scenes:
        # An override, and one of the tape's inputs: queueing the tape's job groups its chapters again by it.
        source = one(s, source_id)
        chapters.mark(s, source, scene, b.start, author=WHO)
        jobs.ingest_later(jobs.queue(records.engine()), source, by=WHO)
        return browse.scenes_of(s, source)

    @app.put("/api/sources/{source_id}/scenes/{scene}/chapter-title")
    def put_chapter_title(source_id: int, scene: int, b: TitleIn, s: DbSession) -> browse.Scenes:
        # The chapter is named by its first scene, which a regroup does not renumber. Laid over the namer's when the
        # chapters are read: nothing to run again.
        source = one(s, source_id)
        chapters.rename(s, source, scene, b.title, author=WHO)
        return browse.scenes_of(s, source)

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

    # Reels (plan step 5, `reels`): planning is a pure function of stored results, quick enough to answer in the request.
    @app.get("/api/reels")
    def get_reels(s: DbSession, source: int | None = None) -> list[reels.ReelOut]:
        return [reels.reel_out(s, r) for r in reels.all_reels(s, None if source is None else one(s, source))]

    @app.post("/api/reels", status_code=201)
    def post_reel(b: ReelIn, s: DbSession) -> reels.ReelDetail:
        reel, _ = reels.create(s, one(s, b.source_id), reels.length(b.target_seconds), name=b.name, author=WHO)
        return reels.detail(s, reel)

    @app.get("/api/reels/{reel_id}")
    def get_reel(reel_id: int, s: DbSession) -> reels.ReelDetail:
        return reels.detail(s, reels.get(s, reel_id))

    @app.get("/api/reels/{reel_id}/revisions/{revision_id}")
    def get_revision(reel_id: int, revision_id: int, s: DbSession) -> reels.RevisionOut:
        return reels.revision_out(s, reels.revision(s, reels.get(s, reel_id), revision_id))

    @app.post("/api/reels/{reel_id}/plan", status_code=201)
    def post_plan(reel_id: int, b: PlanIn, s: DbSession) -> reels.RevisionOut:
        target = None if b.target_seconds is None else reels.length(b.target_seconds)
        rev = reels.propose(s, reels.get(s, reel_id), seed=b.seed, target=target, author=WHO)
        return reels.revision_out(s, rev)

    @app.post("/api/reels/{reel_id}/revisions/{revision_id}/accept")
    def post_accept(reel_id: int, revision_id: int, s: DbSession) -> reels.RevisionOut:
        # Compare-and-swap on the reel's current revision: a stale proposal is a 409 (strata_python.tasks.Conflict).
        return reels.revision_out(s, reels.accept(s, reel_id, revision_id, author=WHO))

    @app.post("/api/reels/{reel_id}/revisions/{revision_id}/reject")
    def post_reject(reel_id: int, revision_id: int, s: DbSession) -> reels.RevisionOut:
        return reels.revision_out(s, reels.reject(s, reels.get(s, reel_id), revision_id))

    @app.post("/api/reels/{reel_id}/constraints", status_code=201)
    def post_constraint(reel_id: int, b: ConstraintIn, s: DbSession) -> reels.ReelDetail:
        reel = reels.get(s, reel_id)
        reels.constrain(s, reel, b.kind, scene=b.scene, clip=b.clip, author=WHO)
        return reels.detail(s, reel)

    @app.delete("/api/reels/{reel_id}/constraints/{override_id}")
    def delete_constraint(reel_id: int, override_id: int, s: DbSession) -> reels.ReelDetail:
        reel = reels.get(s, reel_id)
        reels.unconstrain(s, reel, override_id)
        return reels.detail(s, reel)

    # Renders (spec S8, `renders`): a revision rendered by ffmpeg on the job queue, with its manifest.
    @app.post("/api/reels/{reel_id}/revisions/{revision_id}/render", status_code=201)
    def post_render(reel_id: int, revision_id: int, s: DbSession) -> renders.RenderOut:
        reel = reels.get(s, reel_id)
        q = jobs.queue(records.engine())
        return renders.out(s, q, renders.request(s, q, reel, reels.revision(s, reel, revision_id)))

    @app.get("/api/reels/{reel_id}/renders")
    def get_renders(reel_id: int, s: DbSession) -> list[renders.RenderOut]:
        q = jobs.queue(records.engine())
        return [renders.out(s, q, r) for r in renders.of_reel(s, reels.get(s, reel_id))]

    @app.get("/api/reels/{reel_id}/renders/{render_id}")
    def get_render(reel_id: int, render_id: int, s: DbSession) -> renders.RenderOut:
        return renders.out(s, jobs.queue(records.engine()), renders.get(s, reels.get(s, reel_id), render_id))

    @app.get("/api/reels/{reel_id}/renders/{render_id}/file", response_class=FileResponse)
    def get_render_file(reel_id: int, render_id: int, s: DbSession) -> FileResponse:
        reel = reels.get(s, reel_id)
        # A render's file never changes (a new one is a new render), so the browser may keep it.
        return FileResponse(
            renders.file_of(s, reel, render_id),
            media_type="video/mp4",
            filename=f"{reel.name}.mp4",
            headers={"Cache-Control": IMMUTABLE},
        )

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
