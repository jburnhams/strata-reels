"""Command line. From strata-python: `doctor` (versions and imports), `set` (a setting), `serve` (the web server) and
`openapi` (the schema file the web app's types are generated from). The app's own: `add` adds footage as sources and
`sources` lists them."""

import argparse
import json
import sys
from collections.abc import Sequence
from typing import TYPE_CHECKING

from strata_python import cli as shared

from app import __version__, db, settings, sources

if TYPE_CHECKING:
    from fastapi import FastAPI


def run_server(host: str, port: int) -> None:
    from app.server import app as server_app  # here, so the other commands do not import FastAPI's routes

    server_app.main(["--host", host, "--port", str(port)])


def create_app() -> "FastAPI":
    from app.server.app import create_app as create  # only when `openapi` runs

    return create()


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
