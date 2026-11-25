"""Command line. From strata-python: `doctor` (versions and imports), `set` (a setting), `serve` (the web server) and
`openapi` (the schema file the web app's types are generated from). The app's own: `add` adds footage as sources (and
queues each one's ingest: the content hash, then the proxy, thumbnails, peaks, shots and speech), `sources` lists
them, `scenes` lists a source's scenes (from its shots, once the jobs have run, with each one's description and
faces), `chapters` its chapters (the scenes grouped by occasion, each with its name or the person's title),
`fit-chapters` what the person's chapter corrections say the grouping's weights should be, `jobs` lists the job queue and `work` runs what is queued, here, until nothing is (the server runs the queue
itself while it is up)."""

import argparse
import json
import sys
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.orm import Session
from strata_python import cli as shared
from strata_python import mediatime

from app import __version__, chapters, db, describe, jobs, scenes, settings, shots, sources

if TYPE_CHECKING:
    from fastapi import FastAPI


def run_server(host: str, port: int) -> None:
    from app.server import app as server_app  # here, so the other commands do not import FastAPI's routes

    server_app.main(["--host", host, "--port", str(port)])


def create_app() -> "FastAPI":
    from app.server.app import create_app as create  # only when `openapi` runs

    return create()


def cmd_add(args: argparse.Namespace) -> int:
    with db.opened() as engine, Session(engine) as s:
        q = jobs.queue(engine)
        for path in args.paths:
            try:
                source, new = sources.add(s, path)
            except sources.SourceError as e:
                print(f"  refused: {e}", file=sys.stderr)
                return 1
            jobs.ingest_later(q, source, again=not new, by="cli")
            print(
                json.dumps(sources.as_json(source))
                if args.json
                else f"  {'added' if new else 'known'} #{source.id} {source.name}"
            )
    return 0


def cmd_sources(args: argparse.Namespace) -> int:
    with db.session() as s:
        items = [sources.as_json(x) for x in sources.all_sources(s)]
    if args.json:
        print(json.dumps(items, indent=1))
        return 0
    if not items:
        print("  (no sources yet: ./app add /path/to/tape.mkv)")
    with db.session() as s:
        hashes = {x.id: x.sha256 for x in sources.all_sources(s)}
    for x in items:
        known = (hashes.get(x["id"]) or "")[:12] or "(hashing)"
        print(f"  #{x['id']:<4} {x['name']:40} {x['duration'] or 0:9.1f} s  {known:12}  {x['path']}")
    return 0


def cmd_scenes(args: argparse.Namespace) -> int:
    with db.session() as s:
        source = s.get(sources.Source, args.source)
        if source is None:
            print(f"  there is no source {args.source} (./app sources lists them)", file=sys.stderr)
            return 1
        found = scenes.current(s, source)
        if found is None:
            print(f"  {source.name} has no scenes yet: ./app jobs shows what is queued, ./app work runs it")
            return 1
        grouped = found.payload
        told = describe.current_descriptions(s, source)
        faces = describe.current_faces(s, source)
        cut = shots.current(s, source)
    # each scene with what step 3 found about it, where it has run: its description and the most faces in a frame
    listed: list[dict[str, Any]] = []
    for sc in grouped["scenes"]:
        d = told.get(sc["start"])
        people = None if faces is None or cut is None else describe.people_in(faces.payload, cut.payload, sc)
        listed.append({**sc, "description": None if d is None else d.payload["description"], "faces": people})
    if args.json:
        print(json.dumps({**grouped, "scenes": listed}, indent=1))
        return 0
    for i, sc in enumerate(listed):
        start, end = float(mediatime.rational(sc["start"])), float(mediatime.rational(sc["end"]))
        sure = "" if sc["confidence"] is None else f"  (boundary {sc['confidence']:.0%})"
        faces_seen = "" if sc["faces"] is None else f"  {sc['faces']} faces"
        print(f"  {i + 1:>3}  {start:9.2f} to {end:9.2f} s  {len(sc['shots']):3} shots{sure}{faces_seen}")
        if sc["description"] is not None:
            print(f"       {sc['description'].get('summary', '')}")
    return 0


def cmd_chapters(args: argparse.Namespace) -> int:
    with db.session() as s:
        source = s.get(sources.Source, args.source)
        if source is None:
            print(f"  there is no source {args.source} (./app sources lists them)", file=sys.stderr)
            return 1
        found = chapters.current(s, source)
        if found is None:
            print(f"  {source.name} has no chapters yet: ./app jobs shows what is queued, ./app work runs it")
            return 1
        grouped = found.payload
        names = chapters.current_names(s, source)
        titles = chapters.titles(s, source)
    listed: list[dict[str, Any]] = [
        {
            **ch,
            "name": None if ch["start"] not in names else names[ch["start"]].payload["name"],
            "title": titles.get(ch["start"]),  # the person's, which wins over the name's
        }
        for ch in grouped["chapters"]
    ]
    if args.json:
        print(json.dumps({**grouped, "chapters": listed}, indent=1))
        return 0
    for i, ch in enumerate(listed):
        start, end = float(mediatime.rational(ch["start"])), float(mediatime.rational(ch["end"]))
        sure = "" if ch["confidence"] is None else f"  (boundary {ch['confidence']:.0%})"
        title = (
            f"  {ch['title']} (yours)"
            if ch["title"]
            else ""
            if ch["name"] is None
            else f"  {ch['name'].get('title', '')}"
        )
        print(f"  {i + 1:>3}  {start:9.2f} to {end:9.2f} s  {len(ch['scenes']):3} scenes{sure}{title}")
        if ch["name"] is not None:
            print(f"       {ch['name'].get('summary', '')}")
    return 0


def cmd_fit_chapters(args: argparse.Namespace) -> int:
    """What the person's chapter corrections say the grouping's weights should be: printed, not applied (a release
    takes them into `chapters.PARAMETERS`, as the scenes' weights were taken from the first real tape)."""
    examples: list[chapters.Example] = []
    taught: list[str] = []
    with db.session() as s:
        st = jobs.store()
        for source in s.scalars(select(sources.Source).order_by(sources.Source.id)):
            found = chapters.current(s, source)
            if found is None or not chapters.follows_marks(s, source, found):
                continue  # not grouped by the marks that hold now: its labels would be ones taken back
            got = chapters.examples_of(found.payload, jobs.checked(st, source))
            if got:
                examples += got
                taught.append(source.name)
    jobs.close_records()
    fitted = chapters.fit(examples, prior=args.prior)
    now, then = chapters.agreement(examples, chapters.PARAMETERS), chapters.agreement(examples, fitted)
    if args.json:
        print(json.dumps({"tapes": taught, "boundaries": len(examples), "now": chapters.PARAMETERS,
                          "fitted": fitted, "agreement_now": now, "agreement_fitted": then}, indent=1))  # fmt: skip
        return 0
    if not examples:
        print("  nothing to learn from yet: mark chapters on a tape's page, or say a tape's chapters are right")
        return 1
    print(f"  {len(examples)} boundaries judged on {len(taught)} tapes ({', '.join(taught)})")
    print(f"  agree with you now {now:.0%}, with the fitted weights {then:.0%}")
    for k, v in fitted.items():
        print(f"  {k:14} {float(chapters.PARAMETERS[k]):8.4f} -> {v:8.4f}")
    return 0


def cmd_jobs(args: argparse.Namespace) -> int:
    with db.opened() as engine:
        page = jobs.queue(engine).page(limit=args.limit)
    if args.json:
        print(page.model_dump_json(indent=1))
        return 0
    if not page.items:
        print("  (no jobs)")
    for t in page.items:
        print(f"  #{t.id:<5} {t.kind:14} {t.state:10} {t.domain or '':16} {t.error or ''}")
    return 0


def cmd_work(args: argparse.Namespace) -> int:
    with db.opened() as engine:
        q = jobs.queue(engine)
        jobs.adopt(q)
        ran = jobs.work(q)
        shown = {j.id: jobs.stages_of(j) for j in ran}
    jobs.close_records()
    for j in ran:
        print(f"  #{j.id:<5} {j.kind:14} {j.state:10} {j.domain or '':16} {j.error or ''}")
        for name, state in shown[j.id].items():  # where the tape stands now, stage by stage
            print(f"         {name:10} {state}")
    if not ran:
        print("  (nothing queued)")
    return 1 if any(j.state == "failed" for j in ran) else 0


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="app")
    sub = p.add_subparsers(dest="cmd", required=True)
    shared.add_doctor(sub, "app", __version__, ("fastapi", "uvicorn", "pydantic", "sqlalchemy", "strata_python"))
    ad = sub.add_parser("add", help="add footage as sources (a file is read, never changed)")
    ad.add_argument("paths", nargs="+")
    ad.add_argument("--json", action="store_true")
    ad.set_defaults(fn=cmd_add)
    so = sub.add_parser("sources", help="list the sources")
    so.add_argument("--json", action="store_true")
    so.set_defaults(fn=cmd_sources)
    sc = sub.add_parser(
        "scenes", help="list a source's scenes: how sure each boundary is, what it shows, how many faces"
    )
    sc.add_argument("source", type=int)
    sc.add_argument("--json", action="store_true")
    sc.set_defaults(fn=cmd_scenes)
    ch = sub.add_parser("chapters", help="list a source's chapters: its scenes grouped by occasion, and their names")
    ch.add_argument("source", type=int)
    ch.add_argument("--json", action="store_true")
    ch.set_defaults(fn=cmd_chapters)
    fi = sub.add_parser(
        "fit-chapters", help="fit the chapter grouping's weights to your marks and checked tapes (prints them)"
    )
    fi.add_argument("--prior", type=float, default=1.0, help="how hard the weights are held to today's (default 1)")
    fi.add_argument("--json", action="store_true")
    fi.set_defaults(fn=cmd_fit_chapters)
    jo = sub.add_parser("jobs", help="list the job queue, newest first")
    jo.add_argument("--limit", type=int, default=50)
    jo.add_argument("--json", action="store_true")
    jo.set_defaults(fn=cmd_jobs)
    wo = sub.add_parser(
        "work", help="run the queued jobs here until none is left (the server does this while it is up)"
    )
    wo.set_defaults(fn=cmd_work)
    shared.add_set(sub, settings.SETTINGS)
    shared.add_serve(sub, run_server, default_port=8360)
    shared.add_openapi(sub, create_app)
    a = p.parse_args(argv)
    return a.fn(a)
