"""Adding footage as sources (app/sources.py) against a real database (the plugin's db_session), with ffprobe's answer given instead of run:
tests/integration/test_sources.py runs the real ffprobe."""

import pytest
from sqlalchemy.orm import Session
from strata_python import media

from app import sources

TAPE = {
    "format": {"duration": "5400.04", "format_name": "matroska,webm"},
    "streams": [
        {
            "index": 0,
            "codec_type": "video",
            "codec_name": "h264",
            "width": 720,
            "height": 576,
            "r_frame_rate": "25/1",
            "field_order": "tt",
        },
        {"index": 1, "codec_type": "audio", "codec_name": "pcm_s16le", "sample_rate": "48000", "channels": 2},
    ],
}


def answer(raw):
    def probe(path):
        if raw is None:
            raise media.MediaError("Invalid data found when processing input")
        return media.Probe.from_json(path, raw)

    return probe


def test_a_tape_is_added_once_and_found_again_after_a_move(db_session: Session, tmp_path):
    tape = tmp_path / "tape03.mkv"
    tape.write_bytes(b"\0" * 4096)
    source, new = sources.add(db_session, str(tape), probe=answer(TAPE))
    assert new
    j = sources.as_json(source)
    assert j["name"] == "tape03.mkv"
    assert j["size"] == 4096
    assert j["duration"] == 5400.04
    assert j["video"] == {"codec": "h264", "width": 720, "height": 576, "rate": "25/1", "field_order": "tt"}
    assert j["added"].startswith("20")
    again, new = sources.add(
        db_session, str(tape), probe=answer(None)
    )  # known by its content: ffprobe is not asked again
    assert (again.id, new) == (source.id, False)
    moved = tape.rename(tmp_path / "moved.mkv")
    found, new = sources.add(db_session, str(moved), probe=answer(None))
    assert (found.id, new, found.path, found.name) == (source.id, False, str(moved), "moved.mkv")
    assert [s.id for s in sources.all_sources(db_session)] == [source.id]


def test_files_that_cannot_be_sources_are_refused_before_anything_is_stored(db_session: Session, tmp_path):
    sound = tmp_path / "sound.m4a"
    sound.write_bytes(b"a")
    with pytest.raises(sources.SourceError, match="has no video stream"):
        sources.add(db_session, str(sound), probe=answer({"format": {}, "streams": [{"codec_type": "audio"}]}))
    junk = tmp_path / "junk.mp4"
    junk.write_bytes(b"j")
    with pytest.raises(sources.SourceError, match="not footage ffprobe can read: Invalid data"):
        sources.add(db_session, str(junk), probe=answer(None))
    with pytest.raises(sources.SourceError, match="there is no file"):
        sources.add(db_session, str(tmp_path / "missing.mp4"))
    assert sources.all_sources(db_session) == []
    assert (
        sources.as_json(sources.Source(fingerprint="f", path="p", name="n", size=1, duration=None, probe={}))["video"]
        is None
    )
