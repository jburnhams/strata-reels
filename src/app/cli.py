"""Command line. From strata-python: `doctor` (versions and imports), `set` (a setting), `serve` (the web server) and
`openapi` (the schema file the web app's types are generated from). The app's own: `hello` prints or keeps a greeting,
`messages` lists or clears the kept ones; `add` adds footage as sources and `sources` lists them."""

import argparse
import json
import sys
from collections.abc import Sequence
from typing import TYPE_CHECKING

from strata_python import cli as shared

from app import __version__, db, greet, settings, sources

if TYPE_CHECKING:
    from fastapi import FastAPI


def run_server(host: str, port: int) -> None:
    from app.server import app as server_app  # here, so the other commands do not import FastAPI's routes

    server_app.main(["--host", host, "--port", str(port)])


def create_app() -> "FastAPI":
    from app.server.app import create_app as create  # only when `openapi` runs

    return create()


def cmd_hello(args: argparse.Namespace) -> int:
    try:
        if args.save:
            with db.session() as s:
                rec = greet.send(s, args.name, args.greeting).model_dump(mode="json")
        else:
            rec = greet.format_greeting(args.name, args.greeting).model_dump(mode="json")
    except greet.GreetingError as e:
        sys.exit(str(e))
    print(json.dumps(rec) if args.json else rec["message"])
    return 0


def cmd_messages(args: argparse.Namespace) -> int:
    with db.session() as s:
        if args.clear:
            n = greet.clear(s).cleared
            print(json.dumps({"cleared": n}) if args.json else f"cleared {n} greeting(s)")
            return 0
        items = greet.everything(s)
    if args.json:
        print(json.dumps([m.model_dump(mode="json") for m in items], indent=1))
    elif not items:
        print("  (no greetings kept yet: ./app hello World --save)")
    for m in [] if args.json else items:
        print(f"  #{m.id:<4} {m.message:30} ({m.created.isoformat()})")
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    with db.session() as s:
        for path in args.paths:
            try:
                source, new = sources.add(s, path)
            except sources.SourceError as e:
                print(f"  refused: {e}", file=sys.stderr)
                return 1
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
    for x in items:
        print(f"  #{x['id']:<4} {x['name']:40} {x['duration'] or 0:9.1f} s  {x['path']}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="app")
    sub = p.add_subparsers(dest="cmd", required=True)
    shared.add_doctor(sub, "app", __version__, ("fastapi", "uvicorn", "pydantic", "sqlalchemy", "strata_python"))
    h = sub.add_parser("hello", help="print a greeting (and optionally keep it)")
    h.add_argument("name", nargs="?", default="World")
    h.add_argument("--greeting", help="override the default greeting word")
    h.add_argument("--save", action="store_true", help="keep the greeting in the database")
    h.add_argument("--json", action="store_true")
    h.set_defaults(fn=cmd_hello)
    m = sub.add_parser("messages", help="list or clear the kept greetings")
    m.add_argument("--clear", action="store_true")
    m.add_argument("--json", action="store_true")
    m.set_defaults(fn=cmd_messages)
    ad = sub.add_parser("add", help="add footage as sources (a file is read, never changed)")
    ad.add_argument("paths", nargs="+")
    ad.add_argument("--json", action="store_true")
    ad.set_defaults(fn=cmd_add)
    so = sub.add_parser("sources", help="list the sources")
    so.add_argument("--json", action="store_true")
    so.set_defaults(fn=cmd_sources)
    shared.add_set(sub, settings.SETTINGS)
    shared.add_serve(sub, run_server, default_port=8360)
    shared.add_openapi(sub, create_app)
    a = p.parse_args(argv)
    return a.fn(a)
