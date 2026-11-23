import importlib
import json

import pytest

from app import cli


def test_doctor_success_and_missing_module(capsys, monkeypatch):
    assert cli.main(["doctor"]) == 0
    assert "fastapi" in capsys.readouterr().out

    real_import = importlib.import_module

    def fake_import(name):
        if name == "uvicorn":
            raise ImportError("no uvicorn")
        return real_import(name)

    monkeypatch.setattr(importlib, "import_module", fake_import)
    assert cli.main(["doctor"]) == 1
    assert "MISSING  uvicorn" in capsys.readouterr().out


def test_hello_and_messages_commands(capsys):
    assert cli.main(["hello"]) == 0
    assert capsys.readouterr().out.strip() == "Hello, World!"

    assert cli.main(["hello", "Alice", "--greeting", "Hi", "--save", "--json"]) == 0
    rec = json.loads(capsys.readouterr().out)
    assert rec["message"] == "Hi, Alice!"
    assert rec["id"] == 1

    assert cli.main(["messages"]) == 0
    assert "Hi, Alice!" in capsys.readouterr().out

    assert cli.main(["messages", "--json"]) == 0
    assert len(json.loads(capsys.readouterr().out)) == 1

    assert cli.main(["messages", "--clear"]) == 0
    assert "cleared 1 message(s)" in capsys.readouterr().out

    assert cli.main(["messages"]) == 0
    assert "no messages yet" in capsys.readouterr().out

    assert cli.main(["messages", "--clear", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"cleared": 0}

    with pytest.raises(SystemExit, match="name is too long"):
        cli.main(["hello", "x" * 121])


def test_set_and_serve_commands(capsys, monkeypatch):
    assert cli.main(["set", "greeting"]) == 0
    assert "greeting = Hello" in capsys.readouterr().out

    assert cli.main(["set", "greeting", "Howdy"]) == 0
    assert "greeting = Howdy" in capsys.readouterr().out

    assert cli.main(["set", "greeting", "default"]) == 0
    assert "greeting = Hello" in capsys.readouterr().out

    with pytest.raises(SystemExit, match="unknown setting"):
        cli.main(["set", "bad_key", "val"])

    from app.server import app as server_app

    called = []
    monkeypatch.setattr(server_app, "main", lambda argv: called.append(argv))
    assert cli.main(["serve", "--host", "0.0.0.0", "--port", "9000"]) == 0
    assert called == [["--host", "0.0.0.0", "--port", "9000"]]


def test_add_and_sources_commands(capsys, tmp_path):
    from strata_python import media

    from app import sources

    assert cli.main(["sources"]) == 0
    assert "no sources yet" in capsys.readouterr().out
    assert cli.main(["add", str(tmp_path / "missing.mkv")]) == 1
    assert "refused: there is no file" in capsys.readouterr().err
    tape = tmp_path / "tape.mkv"
    tape.write_bytes(b"\0" * 100)
    raw = {"format": {"duration": "12.5"}, "streams": [{"codec_type": "video", "r_frame_rate": "25/1"}]}
    with (
        cli._open_db() as db
    ):  # the CLI's own database (in the test's settings folder), given a source as ffprobe would describe it
        sources.add(db, str(tape), probe=lambda p: media.Probe.from_json(p, raw))
    assert cli.main(["add", str(tape)]) == 0  # known by its content: ffprobe is not run
    assert "known #1 tape.mkv" in capsys.readouterr().out
    assert cli.main(["add", "--json", str(tape)]) == 0
    assert json.loads(capsys.readouterr().out)["video"]["rate"] == "25/1"
    assert cli.main(["sources"]) == 0
    assert "tape.mkv" in capsys.readouterr().out
    assert cli.main(["sources", "--json"]) == 0
    assert [s["name"] for s in json.loads(capsys.readouterr().out)] == ["tape.mkv"]
