import json
from collections.abc import Callable
from pathlib import Path

from strata_python.testing import CliResult

from app import settings

Cli = Callable[..., CliResult]  # the `cli` fixture (tests/integration/conftest.py)


def test_doctor_reports_the_environment(cli: Cli):
    r = cli("doctor", check=False)
    assert "app 0.1.0" in r.stdout
    assert "sqlalchemy" in r.stdout
    assert r.code == 0


def test_sources_live_in_the_fallback_database_in_the_app_home(cli: Cli):
    assert "no sources yet" in cli("sources").stdout
    assert json.loads(cli("sources", "--json").stdout) == []
    # no database configured: SQLite in the app's home, migrated on first use
    assert (Path(settings.SETTINGS.home()) / "reels.db").is_file()
    refused = cli("add", "/no/such/tape.mkv", check=False)
    assert (refused.code, "refused: there is no file" in refused.stderr) == (1, True)
    assert cli("set", "nope", "x", check=False).code != 0


def test_the_committed_schema_check_passes(cli: Cli):
    # What CI runs on the Python side, so a stale web/openapi.json fails without Node.
    assert cli("openapi", "--check").code == 0


def test_the_command_line_runs_as_a_real_process(cli_subprocess: Cli):
    assert "app 0.1.0" in cli_subprocess("doctor").stdout
    assert "no sources yet" in cli_subprocess("sources").stdout


def test_added_footage_is_hashed_by_work(cli: Cli, tmp_path):
    import hashlib
    import subprocess

    tape = tmp_path / "tape.mp4"
    subprocess.run(
        [
            *("ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=d=1:s=64x48:r=25"),
            *("-pix_fmt", "yuv420p", str(tape)),
        ],
        check=True,
    )
    assert "added #1 tape.mp4" in cli("add", str(tape)).stdout
    assert "(hashing)" in cli("sources").stdout
    assert "hash       queued" in cli("jobs").stdout
    assert "hash       done" in cli("work").stdout
    assert "(nothing queued)" in cli("work").stdout
    assert hashlib.sha256(tape.read_bytes()).hexdigest()[:12] in cli("sources").stdout
    assert len(json.loads(cli("jobs", "--json").stdout)["items"]) == 1
