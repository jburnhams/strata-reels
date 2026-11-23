import importlib
import json
from pathlib import Path

import pytest

from app import cli
from app.server import app as server_app


def test_doctor_success_and_missing_module(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch):
    assert cli.main(["doctor"]) == 0
    assert "sqlalchemy" in capsys.readouterr().out

    real_import = importlib.import_module

    def fake_import(name: str):
        if name == "uvicorn":
            raise ImportError("no uvicorn")
        return real_import(name)

    monkeypatch.setattr(importlib, "import_module", fake_import)
    assert cli.main(["doctor"]) == 1
    assert "MISSING  uvicorn" in capsys.readouterr().out


def test_set_serve_and_openapi_commands(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    with pytest.raises(SystemExit, match="unknown setting"):
        cli.main(["set", "bad_key", "val"])

    called: list[list[str]] = []
    monkeypatch.setattr(server_app, "main", lambda argv: called.append(argv))
    assert cli.main(["serve", "--host", "0.0.0.0", "--port", "9000"]) == 0
    assert called == [["--host", "0.0.0.0", "--port", "9000"]]

    out = tmp_path / "openapi.json"
    assert cli.main(["openapi", "-o", str(out)]) == 0
    assert '"/api/sources"' in out.read_text(encoding="utf8")


def test_add_and_sources_commands(capsys: pytest.CaptureFixture[str], db_env: str, tmp_path: Path):
    from strata_python import media

    from app import db, sources

    assert cli.main(["sources"]) == 0
    assert "no sources yet" in capsys.readouterr().out
    assert cli.main(["add", str(tmp_path / "missing.mkv")]) == 1
    assert "refused: there is no file" in capsys.readouterr().err
    tape = tmp_path / "tape.mkv"
    tape.write_bytes(b"\0" * 100)
    raw = {"format": {"duration": "12.5"}, "streams": [{"codec_type": "video", "r_frame_rate": "25/1"}]}
    with db.session() as s:  # the CLI's own database, given a source as ffprobe would describe it
        sources.add(s, str(tape), probe=lambda p: media.Probe.from_json(p, raw))
    assert cli.main(["add", str(tape)]) == 0  # known by its content: ffprobe is not run
    assert "known #1 tape.mkv" in capsys.readouterr().out
    assert cli.main(["add", "--json", str(tape)]) == 0
    assert json.loads(capsys.readouterr().out)["video"]["rate"] == "25/1"
    assert cli.main(["sources"]) == 0
    assert "tape.mkv" in capsys.readouterr().out
    assert cli.main(["sources", "--json"]) == 0
    assert [s["name"] for s in json.loads(capsys.readouterr().out)] == ["tape.mkv"]
