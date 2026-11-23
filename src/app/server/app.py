"""The application server: `./app serve` starts the FastAPI backend and serves the built React app.

    ./app serve [--host 127.0.0.1] [--port 8360]

From strata-python: GET /api/health, GET and PUT /api/settings, GET /api/system, /api/docs, /api/openapi.json, the
error answers ({detail, payload}) and the web app's pages. This app's own routes:

    GET /api/hello?name=&greeting=    format a greeting without keeping it
    GET /api/messages                 every kept greeting, newest first
    GET /api/messages/page?after=&limit=   one page of them (a Page: items, next)
    POST /api/messages {name, greeting?}   send and keep one
    DELETE /api/messages              remove them all
    DELETE /api/messages/{id}         remove one
    GET /api/sources                  every source, oldest first
    POST /api/sources {path}          add footage: 201 when new, 200 when it is already a source, 400 when it cannot be one

Every other path serves the web app (`src/app/server/static/index.html` when built with `cd web && npm run build`,
else the plain stand-in `landing.html`), so a reload or a shared link to /settings?tab=appearance opens that screen.
The routes are typed (response models from `greet`), so `./app openapi` writes `web/openapi.json` and the web app's
types are generated from it."""

import os
from collections.abc import Sequence
from typing import Annotated

from fastapi import FastAPI, Query, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session
from strata_python import web
from strata_python.contracts import Page
from strata_python.db.web import DbSession, add_database

from app import __version__, db, greet, settings, sources, system

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "static")
LANDING = os.path.join(HERE, "landing.html")
COMMIT = web.source_commit(__file__)  # None when installed from a wheel


class SourceIn(BaseModel):
    path: str


class MessageIn(BaseModel):
    name: str = "World"
    greeting: str | None = None


def create_app() -> FastAPI:
    app = web.base_app("strata-reels", __version__, settings.SETTINGS, commit=COMMIT)
    # GreetingError is a ValueError (400) and a missing greeting a KeyError (404): no try/except in the routes.
    web.add_error_handlers(app)
    # Opened and migrated on the first request that needs it, so a server nobody records on makes no database.
    records = add_database(app, db.PROJECT, default=db.default_url)

    def numbers() -> dict[str, object]:
        with Session(records.engine()) as s:
            n = greet.count(s)
        return system.AppNumbers(greetings=n, home_bytes=system.folder_bytes(settings.SETTINGS.home())).model_dump()

    web.add_system(app, __version__, commit=COMMIT, folders={"home": settings.SETTINGS.home}, extra=numbers)

    @app.get("/api/hello")
    def get_hello(name: str = "World", greeting: str | None = None) -> greet.Greeting:
        return greet.format_greeting(name, greeting)

    @app.get("/api/messages")
    def get_messages(s: DbSession) -> list[greet.Sent]:
        return greet.everything(s)

    @app.get("/api/messages/page")
    def get_messages_page(
        s: DbSession, after: int | None = None, limit: Annotated[int, Query(ge=1, le=200)] = 20
    ) -> Page[greet.Sent]:
        return greet.page(s, after, limit)

    @app.post("/api/messages")
    def post_message(b: MessageIn, s: DbSession) -> greet.Sent:
        return greet.send(s, b.name, b.greeting)

    @app.delete("/api/messages")
    def delete_messages(s: DbSession) -> greet.Cleared:
        return greet.clear(s)

    @app.delete("/api/messages/{message_id}")
    def delete_message(message_id: int, s: DbSession) -> greet.Sent:
        return greet.remove(s, message_id)

    @app.get("/api/sources")
    def get_sources(s: DbSession) -> list[sources.Described]:
        return [sources.Described.of(x) for x in sources.all_sources(s)]

    @app.post("/api/sources", status_code=201)
    def post_source(b: SourceIn, s: DbSession, response: Response) -> sources.Described:
        source, new = sources.add(s, b.path)  # a SourceError is a ValueError: 400 with its sentence
        if not new:
            response.status_code = 200
        return sources.Described.of(source)

    web.add_web(app, STATIC, LANDING)  # last: its catch-all serves the web app at every other path
    return app


def main(argv: Sequence[str] | None = None) -> None:
    web.serve(create_app(), argv, default_port=8360, banner=f"settings and records: {settings.SETTINGS.home()}")
