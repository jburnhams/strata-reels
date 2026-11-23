"""Adding footage as sources (app/sources.py) on the real ffprobe and a real database (the strata-python plugin's db_session: SQLite
migrated from migrations/, or Postgres with --pg). ffmpeg is installed in CI for this (integration.yml), which sets REELS_TEST_FFMPEG=1 so a
missing one fails there instead of skipping."""

import os
import shutil
import subprocess

import pytest
from sqlalchemy.orm import Session

from app import sources

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None and not os.environ.get("REELS_TEST_FFMPEG"),
    reason="ffmpeg is not installed (CI sets REELS_TEST_FFMPEG=1 so it fails there instead)",
)


def make(path, *inputs):
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-y"]
    for i in inputs:
        cmd += ["-f", "lavfi", "-i", i]
    subprocess.run([*cmd, "-shortest", "-pix_fmt", "yuv420p", str(path)], check=True)
    return str(path)


@needs_ffmpeg
def test_a_file_is_added_once_and_found_again_after_a_move(db_session: Session, tmp_path):
    clip = make(tmp_path / "tape.mp4", "testsrc=d=2:s=96x72:r=25", "sine=d=2")
    source, new = sources.add(db_session, clip)
    assert new
    j = sources.as_json(source)
    assert j["name"] == "tape.mp4"
    assert j["duration"] == pytest.approx(2.0, abs=0.05)
    assert j["video"]["rate"] == "25/1"
    assert j["video"]["width"] == 96
    assert j["size"] == os.path.getsize(clip)
    again, new = sources.add(db_session, clip)
    assert (again.id, new) == (source.id, False)
    moved = shutil.move(clip, tmp_path / "moved.mp4")
    found, new = sources.add(db_session, str(moved))
    assert (found.id, new, found.name) == (source.id, False, "moved.mp4")
    assert [s.id for s in sources.all_sources(db_session)] == [source.id]


@needs_ffmpeg
def test_sound_only_and_unreadable_files_are_refused(db_session: Session, tmp_path):
    with pytest.raises(sources.SourceError, match="no video stream"):
        sources.add(db_session, make(tmp_path / "sound.m4a", "sine=d=1"))
    junk = tmp_path / "junk.mp4"
    junk.write_text("not a video")
    with pytest.raises(sources.SourceError, match="not footage ffprobe can read"):
        sources.add(db_session, str(junk))
    with pytest.raises(sources.SourceError, match="there is no file"):
        sources.add(db_session, str(tmp_path / "missing.mp4"))
    assert sources.all_sources(db_session) == []
