"""Derived assets of a source: the browser proxy, thumbnails and audio peaks (R1 step 1, overview §6).

Made from the raw footage as it was added. Restoring the picture (strata-models' `video-enhance`) is a later step in
the pipeline and makes its own asset; the proxy here is for watching and seeking in the browser, deinterlaced for the
screen and nothing more. How each is made is strata-python's (`strata_python.derive`); this module says what reels
keeps and where.

- **Each is a result with a file** (`results.record`): kind `proxy`, `thumbnails` or `peaks`, role `ffmpeg`, inputs the
  source's full SHA-256, parameters the recipe's own (its height, step or rate, and strata-python's `RECIPE` version).
  So the cache key changes when any of them does, the new asset supersedes the old, and a job that runs twice finds
  the stored one and makes nothing. The payload says what the file is (its SHA-256 and size, the picture's size, the
  field order found, each thumbnail's exact time).
- **Files live in the app's home**, `derived/<cache key>/`, and the row names that path relative to the home, so a
  moved home (REELS_HOME) keeps its assets. Each is written into place atomically by strata-python; a folder whose row
  was never written (a crash between the two) is made again on the next run, over itself, and a file deleted under a
  row is made again at its path.
- **Nothing runs before the content hash**: the inputs are the hash, so `make` refuses a source that has none."""

import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session
from strata_python import derive, files, media, mediatime
from strata_python.tasks import report_progress

from app import results, settings
from app.models import Result, Source

KINDS = ("proxy", "thumbnails", "peaks")
PRODUCER = results.Provenance(role="ffmpeg")

# What each kind is made with. Changing a value here (or strata-python's recipe) is a new request: a new asset.
PROXY = {"height": 540, "gop": "1/2", "crf": 23}
THUMBNAILS = {"every": "10", "height": 180}
PEAKS = {"per_second": 50, "rate": 8000}


def parameters(kind: str) -> dict[str, Any]:
    own = {"proxy": PROXY, "thumbnails": THUMBNAILS, "peaks": PEAKS}[kind]
    return {**own, "recipe": derive.RECIPE[kind]}


def wanted(source: Source) -> list[str]:
    """The kinds a source gets: peaks only where there is sound."""
    has_audio = media.Probe.from_json(source.path, source.probe).audio is not None
    return [k for k in KINDS if k != "peaks" or has_audio]


def folder(key: str) -> str:
    return os.path.join("derived", key)


def path_of(r: Result) -> str:
    """The absolute path of a derived asset's file (or folder, for thumbnails)."""
    if r.file is None:
        raise KeyError(f"result {r.id} has no file")
    return os.path.join(settings.SETTINGS.home(), r.file)


@dataclass(frozen=True)
class Made:
    file: str  # relative to the app's home
    payload: dict[str, Any]


def _progress(source: Source, what: str) -> Callable[[float], None]:
    total = int(source.duration or 0) or None

    def report(seconds: float) -> None:
        report_progress(int(seconds), total, f"{what}: {int(seconds)} s")

    return report


def _info(source: Source) -> media.Probe:
    return media.Probe.from_json(source.path, source.probe)


def make_proxy(source: Source, key: str) -> Made:
    p = PROXY
    info = _info(source)
    fields = derive.interlacing(source.path, info=info)
    rel = os.path.join(folder(key), "proxy.mp4")
    out = os.path.join(settings.SETTINGS.home(), rel)
    made = derive.proxy(
        source.path,
        out,
        height=int(p["height"]),
        gop=mediatime.rational(str(p["gop"])),
        crf=int(p["crf"]),
        interlace=fields,
        info=info,
        progress=_progress(source, "proxy"),
    )
    v = made.video
    return Made(
        rel,
        {
            "sha256": files.sha256_hex(out),
            "bytes": os.path.getsize(out),
            "width": None if v is None else v.width,
            "height": None if v is None else v.height,
            "interlace": fields,
            "audio": made.audio is not None,
        },
    )


def make_thumbnails(source: Source, key: str) -> Made:
    p = THUMBNAILS
    rel = os.path.join(folder(key), "thumbnails")
    target = os.path.join(settings.SETTINGS.home(), rel)
    thumbs = derive.thumbnails(
        source.path,
        target,
        every=mediatime.rational(str(p["every"])),
        height=int(p["height"]),
        info=_info(source),
        progress=_progress(source, "thumbnails"),
    )
    return Made(
        rel,
        {"thumbnails": [{"time": mediatime.text(t.time), "name": os.path.basename(t.path)} for t in thumbs]},
    )


def make_peaks(source: Source, key: str) -> Made:
    p = PEAKS
    rel = os.path.join(folder(key), "peaks.json")
    out = os.path.join(settings.SETTINGS.home(), rel)
    found = derive.peaks(
        source.path, per_second=int(p["per_second"]), rate=int(p["rate"]), progress=_progress(source, "peaks")
    )
    files.write_json_atomic(out, found.to_json(), indent=None)
    return Made(
        rel,
        {
            "sha256": files.sha256_hex(out),
            "per_second": found.per_second,
            "slices": len(found.min),
            "seconds": mediatime.text(found.seconds),
        },
    )


# Module level, so a unit test can stand in for ffmpeg (tests/unit/test_derived.py); the integration tests run these.
MAKERS: dict[str, Callable[[Source, str], Made]] = {
    "proxy": make_proxy,
    "thumbnails": make_thumbnails,
    "peaks": make_peaks,
}


def key_for(source: Source, kind: str) -> str:
    if source.sha256 is None:
        raise ValueError(f"{source.name} has no content hash yet: its {kind} waits for the hash job")
    return results.cache_key(kind, [source.sha256], PRODUCER, parameters(kind))


def make(db: Session, source: Source, kind: str, *, job_id: int | None = None) -> tuple[Result, bool]:
    """The current `kind` asset of `source`, made if it is not there (or its file has gone), and whether it is new.
    ValueError for a source with no content hash or a kind reels does not make."""
    if kind not in MAKERS:
        raise ValueError(f"reels makes no {kind!r}: it makes {', '.join(KINDS)}")
    key = key_for(source, kind)
    found = results.find(db, key)
    if found is not None and os.path.exists(path_of(found)):
        return found, False
    assert source.sha256 is not None  # noqa: S101 - key_for refused it otherwise
    made = MAKERS[kind](source, key)
    if found is not None:
        # The row is there but its file was deleted (a cleared cache): the same request, made again at the same path.
        # The row is not rewritten (results are append-only), so its payload describes the first file made.
        return found, True
    return results.record(
        db,
        source,
        kind,
        PRODUCER,
        inputs=[source.sha256],
        parameters=parameters(kind),
        payload=made.payload,
        file=made.file,
        job_id=job_id,
    )
