"""A revision of a reel rendered to a file (plan step 7, spec S8): ffmpeg's frame-exact cut of the clips from the tape as
it was added, joined with no gap (`strata_python.render`), and a manifest of exactly what made it.

- **The tape is never changed.** The render reads each clip's source file (checked against the fingerprint it was added
  with first, as the tape's stages are) and writes a new file under the app's home, `renders/<key>/reel.mp4`, with
  `manifest.json` beside it. Restoration (`video-enhance`) comes later; until then the render reads the raw tape and the
  manifest says which file it read, by SHA-256.
- **A render is a row** (`models.Render`), made when it is asked for and stamped once when its job ends: the
  revision it is of, its key, the job that makes it, then its file, SHA-256 and manifest. Its key is
  hash(the revision's clips, each source's SHA-256, strata-python's render recipe, the output settings), so asking
  again for a revision already rendered (or one with the same clips) finds that file instead of making it again, and
  a changed recipe or setting is a new render.
- **The manifest** is strata-python's (the ffmpeg and x264 builds, the command, each cut's frames and edit time, the
  file's size, frame count and SHA-256) plus this app's part: the reel, the revision and its key, and each clip's id
  and source. The same revision on the same ffmpeg build gives the same bytes (`render.render`'s bitexact settings),
  which the manifest's SHA-256 lets anyone check.
- **It runs on the job queue** (kind `render`, one job per render, deduped, holding the `disk` resource like a tape's
  analysis), so a render of a long reel does not hold a request and the jobs page shows its progress."""

import copy
import datetime
import hashlib
import json
import os
from fractions import Fraction
from typing import Any, Literal

from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from strata_python import files, mediatime, render
from strata_python.contracts import ApiModel
from strata_python.tasks import report_progress
from strata_python.tasks.queue import Job, Queue

from app import settings
from app.models import Reel, Render, Revision, Source

KIND = "render"

# What a reel is rendered as: the tape's own size and rate (never scaled up), H.264 at a quality meant to be watched and
# kept, AAC stereo. Changing a value is a new render (it is in the key).
OUTPUT = render.Output(crf=18, preset="medium")

State = Literal["queued", "running", "done", "failed", "cancelled"]


class RenderOut(ApiModel):
    """A render of a revision: its job's state while it is made, then its file. `url` downloads it once `done`."""

    id: int
    reel_id: int
    revision_id: int
    job_id: int | None
    state: State
    error: str | None = Field(description="why the job failed, in its words")
    created: datetime.datetime
    finished: datetime.datetime | None
    url: str | None = Field(description="the file, relative to the app's base; None until it is made")
    sha256: str | None
    bytes: int | None
    duration_seconds: float | None
    manifest: dict[str, Any] | None = Field(description="what made the file (spec S8); None until it is made")


def _utc(t: datetime.datetime) -> datetime.datetime:
    # SQLite gives back a naive time; it was written in UTC.
    return t if t.tzinfo is not None else t.replace(tzinfo=datetime.UTC)


def key_of(db: Session, rev: Revision) -> str:
    """hash(clips, sources' SHA-256, recipe, output): two revisions with the same clips share a render."""
    shas: dict[str, str] = {}
    for c in rev.clips:
        source = db.get(Source, int(c["source_id"]))
        if source is None or source.sha256 is None:
            raise ValueError(
                f"a clip's tape (source {c['source_id']}) is not hashed yet: render it after the hash step"
            )
        shas[str(source.id)] = source.sha256
    cuts = [[c["source_id"], c["start"], c["end"], c.get("gain_db", 0.0)] for c in rev.clips]
    body = {"cuts": cuts, "sources": shas, "recipe": render.RECIPE, "output": OUTPUT.to_json()}
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def folder(key: str) -> str:
    return os.path.join("renders", key)


def path_of(r: Render) -> str:
    """The rendered file's absolute path. KeyError before it is made."""
    if r.file is None:
        raise KeyError(f"render {r.id} has no file yet")
    return os.path.join(settings.SETTINGS.home(), r.file)


def request(db: Session, q: Queue, reel: Reel, rev: Revision) -> Render:
    """The render of `rev`: the one already made from the same clips (its file still there) or being made, else a new row
    and its queued job. ValueError for a revision with no clips or a tape not hashed yet."""
    if not rev.clips:
        raise ValueError("this revision has no clips: there is nothing to render")
    key = key_of(db, rev)
    for r in db.scalars(select(Render).where(Render.key == key).order_by(Render.id.desc())):
        if r.file is not None and os.path.isfile(path_of(r)):
            if r.revision_id == rev.id:
                return r
            break  # another revision with the same clips: give this one its own row, pointing at the same file
        if r.file is None and r.revision_id == rev.id and r.job_id is not None:
            job = q.get(r.job_id)
            if job is not None and job.state in ("queued", "running"):
                return r
    made = next(
        (
            r
            for r in db.scalars(select(Render).where(Render.key == key, Render.file.is_not(None)))
            if os.path.isfile(path_of(r))
        ),
        None,
    )
    row = Render(reel_id=reel.id, revision_id=rev.id, key=key)
    if made is not None:
        # The same clips were rendered for another revision: the same file, no job. The manifest's app part is this
        # revision's, and says which render made the file (whose manifest.json is the one beside it).
        manifest = copy.deepcopy(made.manifest) if made.manifest is not None else None
        if manifest is not None:
            manifest["app"] = {
                **manifest["app"],
                "reel": reel.id,
                "revision": rev.id,
                "revision_key": rev.key,
                "job": None,
                "made_by": {"render": made.id, "reel": made.reel_id, "revision": made.revision_id},
            }
        row.file, row.sha256, row.bytes, row.manifest = made.file, made.sha256, made.bytes, manifest
        row.finished = datetime.datetime.now(datetime.UTC)
        db.add(row)
        db.commit()
        return row
    # The row first, then its job: the queue writes on its own connection, which must not wait on this session's. A
    # crash between the two leaves a row with no job, which reads as failed and is asked for again.
    db.add(row)
    db.commit()
    row.job_id = q.enqueue(KIND, {"render": row.id}, domain=f"render:{row.id}", resources=["disk"], dedupe=True)
    db.commit()
    return row


def of_reel(db: Session, reel: Reel) -> list[Render]:
    return list(db.scalars(select(Render).where(Render.reel_id == reel.id).order_by(Render.id.desc())))


def get(db: Session, reel: Reel, render_id: int) -> Render:
    r = db.get(Render, render_id)
    if r is None or r.reel_id != reel.id:
        raise KeyError(f"reel {reel.id} has no render {render_id}")
    return r


def file_of(db: Session, reel: Reel, render_id: int) -> str:
    """The rendered file's path; KeyError before it is made or once it is gone."""
    path = path_of(get(db, reel, render_id))
    if not os.path.isfile(path):
        raise KeyError(f"render {render_id}'s file is gone: render the revision again")
    return path


def out(db: Session, q: Queue, r: Render) -> RenderOut:
    job = None if r.job_id is None else q.get(r.job_id)
    state: State
    if r.file is not None:
        state = "done"
    elif job is None:
        state = "failed"
    else:
        state = job.state if job.state in ("queued", "running", "cancelled") else "failed"  # type: ignore[assignment]
    m = r.manifest
    return RenderOut(
        id=r.id,
        reel_id=r.reel_id,
        revision_id=r.revision_id,
        job_id=r.job_id,
        state=state,
        error=None if job is None or state != "failed" else (job.error or "The render stopped."),
        created=_utc(r.created),
        finished=None if r.finished is None else _utc(r.finished),
        url=None if r.file is None else f"api/reels/{r.reel_id}/renders/{r.id}/file",
        sha256=r.sha256,
        bytes=r.bytes,
        duration_seconds=None if m is None else float(mediatime.rational(m["file"]["duration"])),
        manifest=m,
    )


# --- the job ---------------------------------------------------------------------------------------------------------


def make(db: Session, r: Render, *, job_id: int | None = None) -> Render:
    """Render `r`'s revision into its folder and stamp the row with the file and its manifest."""
    rev = db.get(Revision, r.revision_id)
    if rev is None:
        raise KeyError(f"render {r.id}'s revision {r.revision_id} is gone")
    from app import jobs  # the job module imports this one for its handler

    sources: dict[int, Source] = {}
    for c in rev.clips:
        sid = int(c["source_id"])
        if sid not in sources:
            s = db.get(Source, sid)
            if s is None:
                raise KeyError(f"source {sid} is gone")
            jobs.same_footage(s.path, s.fingerprint)
            sources[sid] = s
    cuts = [
        render.Cut(
            sources[int(c["source_id"])].path,
            mediatime.Span.of(c["start"], c["end"]),
            gain_db=float(c.get("gain_db", 0.0)),
        )
        for c in rev.clips
    ]
    total = float(sum((mediatime.rational(c["end"]) - mediatime.rational(c["start"]) for c in rev.clips), Fraction(0)))
    rel = os.path.join(folder(r.key), "reel.mp4")
    home = settings.SETTINGS.home()
    target = os.path.join(home, rel)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    made = render.render(
        cuts,
        target,
        output=OUTPUT,
        progress=lambda s: report_progress(int(s), int(total) or None, f"render: {int(s)} s of {int(total)} s"),
    )
    manifest: dict[str, Any] = {
        "app": {
            "reel": r.reel_id,
            "revision": rev.id,
            "revision_key": rev.key,
            "key": r.key,
            "job": job_id,
            "clips": [
                {"id": c["id"], "source_id": c["source_id"], "start": c["start"], "end": c["end"]} for c in rev.clips
            ],
            "sources": [
                {"id": s.id, "name": s.name, "sha256": s.sha256, "fingerprint": s.fingerprint} for s in sources.values()
            ],
        },
        **made.manifest(),
    }
    # Paths on this machine stay out of what is shown: the command names each source by its id.
    manifest["command"] = [_shown(a, sources, target) for a in manifest["command"]]
    for cut in manifest["cuts"]:
        cut["path"] = _shown(cut["path"], sources, target)
    files.write_json_atomic(os.path.join(home, folder(r.key), "manifest.json"), manifest)
    r.file, r.sha256, r.bytes, r.manifest = rel, made.sha256, made.bytes, manifest
    r.finished = datetime.datetime.now(datetime.UTC)
    db.commit()
    return r


def _shown(arg: str, sources: dict[int, Source], target: str) -> str:
    if arg == target:
        return "reel.mp4"
    for s in sources.values():
        if arg == s.path:
            return f"source:{s.id}"
    return arg


def run(job: Job) -> dict[str, Any]:
    """The `render` job: make the render its options name."""
    from app import jobs

    with Session(jobs.records()) as db:
        r = db.get(Render, int(job.options["render"]))
        if r is None:
            raise KeyError(f"there is no render {job.options['render']}")
        if r.file is None:
            make(db, r, job_id=job.id)
        return {"render": r.id, "file": r.file, "sha256": r.sha256}
