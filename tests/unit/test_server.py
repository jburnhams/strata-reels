from pathlib import Path

import pytest
import uvicorn
from fastapi.testclient import TestClient
from strata_python.testing import assert_openapi_current

from app import __version__
from app.server import app as server_app
from app.server.app import create_app

OPENAPI = Path(__file__).parents[2] / "web" / "openapi.json"


def test_the_committed_schema_is_what_the_server_answers():
    # The web app's types are generated from this file (web/src/api.generated.ts); a route change without
    # `./app openapi` (then `npm run api-types` in web/) fails here, before the types drift from the server.
    assert_openapi_current(create_app, OPENAPI)


def test_landing_page_and_built_static_index(tmp_path, monkeypatch: pytest.MonkeyPatch, db_env: str):
    monkeypatch.setattr(server_app, "STATIC", str(tmp_path / "no_static"))
    with TestClient(create_app(run_jobs="off")) as c:
        assert "Add footage" in c.get("/").text
        assert c.get("/api/health").json()["ok"] is True

    static = tmp_path / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<h1>built react app</h1>", encoding="utf8")
    (static / "assets" / "main.js").write_text("console.log(1)", encoding="utf8")
    monkeypatch.setattr(server_app, "STATIC", str(static))
    with TestClient(create_app(run_jobs="off")) as c:
        assert c.get("/").text == '<base href="./"><h1>built react app</h1>'
        assert "console.log(1)" in c.get("/assets/main.js").text
        assert '<base href="../">' in c.get("/log/2026").text  # one level down: one step back up
        assert c.get("/api/nope").json() == {"detail": "no such endpoint"}


def test_no_settings_yet_the_system_page_and_server_main(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    assert client.get("/api/settings").json() == {}
    assert client.put("/api/settings", json={}).json() == {}
    s = client.get("/api/system").json()
    assert (s["version"], s["sources"]) == (__version__, 0)
    (home,) = s["folders"]
    assert home["name"] == "home"
    assert home["total_bytes"] >= home["free_bytes"] > 0
    assert s["home_bytes"] >= 0

    runs: list[tuple[str, int, str]] = []
    monkeypatch.setattr(uvicorn, "run", lambda app, host, port, log_level: runs.append((host, port, log_level)))
    server_app.main(["--host", "127.0.0.1", "--port", "8360"])
    assert runs == [("127.0.0.1", 8360, "warning")]


def test_sources_api_lists_adds_and_refuses(client: TestClient, tmp_path):
    from strata_python import media

    from app import sources

    assert client.get("/api/sources").json() == []
    r = client.post("/api/sources", json={"path": "/no/such/tape.mkv"})
    assert r.status_code == 400  # a SourceError is a ValueError: the shared handler answers its sentence
    assert "there is no file" in r.json()["detail"]
    tape = tmp_path / "tape.mkv"
    tape.write_bytes(b"\0" * 100)
    raw = {"format": {"duration": "12.5"}, "streams": [{"codec_type": "video", "r_frame_rate": "25/1"}]}
    from sqlalchemy.orm import Session
    from strata_python.db import Project  # the same database the server opened (the test's migrated copy)

    with Session(Project.load(sources.__file__).open()) as db:
        sources.add(db, str(tape), probe=lambda p: media.Probe.from_json(p, raw))  # as ffprobe would describe it
    again = client.post("/api/sources", json={"path": str(tape)})
    assert again.status_code == 200  # already a source, known by its content: ffprobe is not run
    assert again.json()["video"]["rate"] == "25/1"
    assert [s["name"] for s in client.get("/api/sources").json()] == ["tape.mkv"]
