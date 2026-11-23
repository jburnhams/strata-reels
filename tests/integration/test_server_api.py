"""The real server: the app under uvicorn on a loopback port (the plugin's `live_app`), answering real HTTP requests,
over the settings file and the SQLite fallback database in the test's home folder, which the CLI shares."""

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from typing import Any

import pytest
from fastapi import FastAPI
from strata_python.testing import CliResult

Cli = Callable[..., CliResult]  # the `cli` fixture (tests/integration/conftest.py)


@pytest.fixture
def server(live_app: Callable[[FastAPI], AbstractContextManager[str]]) -> Iterator[str]:
    from app.server.app import create_app

    with live_app(create_app()) as base:
        yield base


def call(base: str, method: str, path: str, body: object = None) -> tuple[int, Any]:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base + path, data=data, method=method, headers={"Content-Type": "application/json"})  # noqa: S310 - loopback
    try:
        with urllib.request.urlopen(req, timeout=10) as r:  # noqa: S310 - loopback
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_health_and_errors_answer_over_http(server: str, tmp_path):
    status, health = call(server, "GET", "/api/health")
    assert (status, health["ok"], health["version"]) == (200, True, "0.1.0")
    missing = tmp_path / "tape.mkv"  # a real absolute path on every OS; the server names it absolute
    status, body = call(server, "POST", "/api/sources", {"path": str(missing)})
    assert (status, body["detail"]) == (400, f"there is no file {os.path.abspath(missing)}")
    assert call(server, "PUT", "/api/settings", {"greeting": "Hi"})[0] == 400  # no such setting
    assert call(server, "GET", "/api/system")[1]["sources"] == 0


def test_a_reload_on_any_screen_gets_the_app(server: str):
    with urllib.request.urlopen(server + "/settings?tab=appearance", timeout=10) as r:  # noqa: S310 - loopback
        assert r.status == 200
        assert '<base href="./">' in r.read().decode()


def test_a_tape_added_over_http_is_listed_by_the_cli(server: str, cli: Cli, tmp_path):
    import subprocess

    tape = tmp_path / "tape.mp4"
    subprocess.run(
        [
            *("ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=d=1:s=64x48:r=25"),
            "-pix_fmt",
            "yuv420p",
            str(tape),
        ],
        check=True,
    )  # ffmpeg is installed for the integration tests (integration.yml); without it this fails, never skips
    status, body = call(server, "POST", "/api/sources", {"path": str(tape)})
    assert (status, body["video"]["rate"]) == (201, "25/1")
    assert call(server, "POST", "/api/sources", {"path": str(tape)})[0] == 200  # already a source
    assert [s["name"] for s in call(server, "GET", "/api/sources")[1]] == ["tape.mp4"]
    assert "tape.mp4" in cli("sources").stdout
    assert "known #1 tape.mp4" in cli("add", str(tape)).stdout


def test_the_server_hashes_a_new_tape_and_derives_its_assets_in_job_processes(server: str, cli: Cli, tmp_path):
    import hashlib
    import subprocess
    import time

    tape = tmp_path / "tape.mp4"
    subprocess.run(
        [
            *("ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=d=1:s=64x48:r=25"),
            *("-pix_fmt", "yuv420p", str(tape)),
        ],
        check=True,
    )
    assert call(server, "POST", "/api/sources", {"path": str(tape)})[0] == 201
    digest = hashlib.sha256(tape.read_bytes()).hexdigest()
    deadline = time.monotonic() + 60  # the first job process starts and imports the app
    while digest[:12] not in cli("sources").stdout and time.monotonic() < deadline:
        time.sleep(0.2)
    assert digest[:12] in cli("sources").stdout

    def states() -> list[tuple[str, str]]:
        return [(j["kind"], j["state"]) for j in reversed(json.loads(cli("jobs", "--json").stdout)["items"])]

    # then the derived assets the hash queued, in job processes too (the clip has no sound: no peaks)
    while any(state != "done" for _, state in states()) and time.monotonic() < deadline:
        time.sleep(0.2)
    assert states() == [("hash", "done"), ("proxy", "done"), ("thumbnails", "done")]
    assert json.loads(cli("jobs", "--json").stdout)["items"][-1]["domain"] == "source:1"
