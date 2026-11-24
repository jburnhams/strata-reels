import json
import os
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
    assert "hash       done" in cli("work").stdout  # and the proxy and thumbnails it queued (no sound, no peaks)
    assert "(nothing queued)" in cli("work").stdout
    assert hashlib.sha256(tape.read_bytes()).hexdigest()[:12] in cli("sources").stdout
    # and the shots, which wait for strata-models (not running here: the job is queued to try again)
    assert len(json.loads(cli("jobs", "--json").stdout)["items"]) == 4


def test_work_makes_the_proxy_thumbnails_and_peaks_from_the_raw_tape(cli: Cli, tmp_path):
    """Ingest end to end with the real ffmpeg: a PAL-shaped interlaced clip that does not say it is interlaced (as the
    digitised VHS does not), with sound, becomes a browser proxy, thumbnails and peaks, each stored as a result."""
    import subprocess

    from app import db, derived, results
    from app.models import Source

    tape = tmp_path / "tape.mkv"
    subprocess.run(
        [
            *("ffmpeg", "-nostdin", "-v", "error", "-y"),
            *("-f", "lavfi", "-i", "testsrc2=size=720x576:rate=50:duration=12"),
            *("-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=12"),
            *("-vf", "tinterlace=mode=interleave_top,setfield=prog,setsar=16/15"),
            *("-c:v", "libx264", "-crf", "35", "-c:a", "pcm_s16le", str(tape)),
        ],
        check=True,
    )
    cli("add", str(tape))
    out = cli("work").stdout
    for kind in ("hash", "proxy", "thumbnails", "peaks"):
        assert f"{kind:10} done" in out
    with db.session() as s:
        source = s.get(Source, 1)
        assert source is not None
        proxy, thumbs, peaks = (results.current(s, source, k) for k in derived.KINDS)
        assert proxy[0].payload["interlace"] == "tff"
        assert (proxy[0].payload["width"], proxy[0].payload["height"]) == (720, 540)
        assert os.path.getsize(derived.path_of(proxy[0])) == proxy[0].payload["bytes"]
        assert [t["time"] for t in thumbs[0].payload["thumbnails"]] == ["0", "10"]  # the last 2 s have theirs
        assert sorted(os.listdir(derived.path_of(thumbs[0]))) == ["000000.jpg", "000001.jpg"]
        assert peaks[0].payload["slices"] == 600
        with open(derived.path_of(peaks[0])) as f:
            assert max(json.load(f)["max"]) > 0.1
        assert all(r[0].inputs == [source.sha256] for r in (proxy, thumbs, peaks))
    assert "(nothing queued)" in cli("work").stdout


def _colour(path: str) -> list[float]:
    """A keyframe's average colour (Y, U and V, centred), read by the real ffmpeg shrinking it to one pixel: the
    stand-in embedding below. Not a lavfi `movie=` source, which a Windows path's colon breaks."""
    import subprocess

    out = subprocess.run(
        [
            *("ffmpeg", "-v", "error", "-i", path, "-vf", "scale=1:1:flags=area,format=yuv444p"),
            *("-f", "rawvideo", "-"),
        ],
        capture_output=True,
        check=True,
    ).stdout
    y, u, v = out[0], out[1], out[2]
    return [y / 255, (u - 128) / 128, (v - 128) / 128]


def test_work_splits_a_tape_into_shots_and_groups_them_into_scenes(cli: Cli, tmp_path, fake_models):
    """Step 2 end to end with the real ffmpeg: red for 4 s, dark red for 3 s, black for 1 s, blue for 4 s. strata-models
    is stood in for (it is another service) and answers the cuts at their known frames; the keyframes are cut from the
    real file, and their stand-in vectors are their real average colours, so the grouping sees what the pictures show:
    red and dark red are one scene, the black is the gap, blue is another."""
    import subprocess

    from app import db, results, scenes, shots
    from app.models import Source

    tape = tmp_path / "tape.mkv"
    parts = [("red", 4), ("darkred", 3), ("black", 1), ("blue", 4)]
    inputs = [a for c, d in parts for a in ("-f", "lavfi", "-i", f"color=c={c}:size=320x240:rate=25:duration={d}")]
    joined = "".join(f"[{i}:v]" for i in range(len(parts))) + f"concat=n={len(parts)}:v=1:a=0"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", *inputs, "-filter_complex", joined, "-c:v", "ffv1", str(tape)],
        check=True,
    )
    fake_models.results["shot-boundaries"] = {
        "cuts": [{"time": 4.0, "frame": 100, "score": 0.4}, {"time": 7.0, "frame": 175, "score": 0.9},
                 {"time": 8.0, "frame": 200, "score": 1.0}],
        "black": [{"start": 7.0, "end": 8.0}], "fps": 25.0, "frames": 300, "threshold": 0.3, "min_shot_s": 0.6,
    }  # fmt: skip
    fake_models.results["image-embed"] = lambda p: {"images": [_colour(x) for x in p["paths"]], "dim": 3}
    cli("add", str(tape))
    out = cli("work").stdout
    for kind in ("shots", "keyframes", "scenes"):
        assert f"{kind:10} done" in out
    with db.session() as s:
        source = s.get(Source, 1)
        assert source is not None
        found = shots.current(s, source)
        assert found is not None
        assert [(x["start"], x["end"], x["black"]) for x in found.payload["shots"]] == [
            ("0", "4", False), ("4", "7", False), ("7", "8", True), ("8", "12", False),
        ]  # fmt: skip
        (keys,) = results.current(s, source, "keyframes")
        assert [f["frame"] for f in keys.payload["frames"]] == [49, 137, 249]  # the middle of each shot shown
        grouped = scenes.current(s, source)
        assert grouped is not None
        levels = [(b["at"], b["level"]) for b in grouped.payload["boundaries"]]
        assert levels == [("4", "shot"), ("8", "scene")]
        assert [(sc["start"], sc["end"], sc["shots"]) for sc in grouped.payload["scenes"]] == [
            ("0", "7", [0, 1]), ("8", "12", [3]),
        ]  # fmt: skip
    listed = cli("scenes", "1").stdout
    assert "0.00 to      7.00 s    2 shots" in listed
    assert "8.00 to     12.00 s    1 shots  (boundary 100%)" in listed
