"""Analysis results (app/results.py) on a real migrated database: written once, found again by cache key, superseded by a
different request for the same slot, never overwritten."""

import pytest
from probes import TAPE, answer
from sqlalchemy.orm import Session
from strata_python import mediatime

from app import results, sources
from app.models import Result, Source

TAPE_HASH = "a" * 64
RESTORED_HASH = "b" * 64
SERVED = {"model": "ffmpeg-scene-score", "options": {"threshold": 0.3}, "settings_hash": "1f2e3d4c5b6a", "job_id": 41}


@pytest.fixture
def tape(db_session: Session, tmp_path) -> Source:
    f = tmp_path / "tape.mkv"
    f.write_bytes(b"\0" * 4096)
    source, _ = sources.add(db_session, str(f), probe=answer(TAPE))
    return source


def test_provenance_from_strata_models_answer_and_the_cache_key():
    p = results.Provenance.of("shot-boundaries", SERVED)
    assert (p.role, p.model, p.settings_hash, p.options, p.model_job) == (
        "shot-boundaries",
        "ffmpeg-scene-score",
        "1f2e3d4c5b6a",
        {"threshold": 0.3},
        41,
    )
    assert results.Provenance.of("x", {}).model_job is None
    key = results.cache_key([TAPE_HASH, RESTORED_HASH], p, {"step": 2})
    assert len(key) == 64
    # the order inputs are named in is not part of the question, and the strata-models job id is not either
    again = results.Provenance.of("shot-boundaries", {**SERVED, "job_id": 99})
    assert results.cache_key([RESTORED_HASH, TAPE_HASH], again, {"step": 2}) == key
    # everything else is: a changed setting under the same model key, another model, other parameters, other bytes
    assert (
        results.cache_key(
            [TAPE_HASH, RESTORED_HASH],
            results.Provenance.of("shot-boundaries", {**SERVED, "settings_hash": "000000000000"}),
            {"step": 2},
        )
        != key
    )
    assert (
        results.cache_key(
            [TAPE_HASH, RESTORED_HASH],
            results.Provenance.of("shot-boundaries", {**SERVED, "model": "transnet"}),
            {"step": 2},
        )
        != key
    )
    assert results.cache_key([TAPE_HASH, RESTORED_HASH], p, {"step": 3}) != key
    assert results.cache_key([TAPE_HASH], p, {"step": 2}) != key


def test_a_result_keeps_its_provenance_and_the_same_request_is_stored_once(db_session: Session, tape: Source):
    p = results.Provenance.of("shot-boundaries", SERVED)
    cuts = {"cuts": ["1001/25", "5000/25"]}
    r, new = results.record(
        db_session,
        tape,
        "shots",
        p,
        inputs=[TAPE_HASH],
        payload=cuts,
        confidence=0.8,
        flags=["low-light"],
        parameters={"min_s": 1},
    )
    assert new
    assert (r.role, r.model, r.settings_hash, r.options, r.model_job) == (
        "shot-boundaries",
        "ffmpeg-scene-score",
        "1f2e3d4c5b6a",
        {"threshold": 0.3},
        41,
    )
    assert (r.inputs, r.parameters, r.payload, r.confidence, r.flags) == (
        [TAPE_HASH],
        {"min_s": 1},
        cuts,
        0.8,
        ["low-light"],
    )
    assert r.cache_key == results.cache_key([TAPE_HASH], p, {"min_s": 1})
    assert results.span(r) is None
    same, new = results.record(
        db_session, tape, "shots", p, inputs=[TAPE_HASH], payload={"cuts": []}, parameters={"min_s": 1}
    )
    assert (same.id, new, same.payload) == (r.id, False, cuts)  # found by its key: nothing written over it
    assert results.find(db_session, r.cache_key) is r
    assert results.find(db_session, "0" * 64) is None


def test_another_model_supersedes_and_the_old_answer_stays(db_session: Session, tape: Source):
    old, _ = results.record(
        db_session,
        tape,
        "shots",
        results.Provenance.of("shot-boundaries", SERVED),
        inputs=[TAPE_HASH],
        payload={"cuts": ["1"]},
    )
    newer = results.Provenance.of(
        "shot-boundaries", {**SERVED, "model": "transnet", "settings_hash": "9" * 12, "job_id": 42}
    )
    new, is_new = results.record(db_session, tape, "shots", newer, inputs=[TAPE_HASH], payload={"cuts": ["1", "2"]})
    assert is_new
    assert old.superseded_by == new.id
    assert old.payload == {"cuts": ["1"]}  # superseded, not overwritten
    assert results.current(db_session, tape, "shots") == [new]
    assert results.history(db_session, tape, "shots") == [old, new]
    assert results.find(db_session, old.cache_key) is None  # only a current result answers for its key
    # the old request asked again is a new row again: a superseded answer does not come back by itself
    back, is_new = results.record(
        db_session,
        tape,
        "shots",
        results.Provenance.of("shot-boundaries", SERVED),
        inputs=[TAPE_HASH],
        payload={"cuts": ["1"]},
    )
    assert is_new
    assert back.id not in (old.id, new.id)
    assert new.superseded_by == back.id


def test_results_for_different_spans_live_side_by_side_in_time_order(db_session: Session, tape: Source):
    vl = results.Provenance("vision-language", "qwen-vl", "abc123abc123")
    late = mediatime.Span.of("1001/25", 50)
    early = mediatime.Span.of(0, "12.04")
    a, _ = results.record(
        db_session, tape, "description", vl, inputs=[TAPE_HASH], payload={"observed": "two people"}, span=late
    )
    b, _ = results.record(
        db_session, tape, "description", vl, inputs=[TAPE_HASH], payload={"observed": "a cake"}, span=early
    )
    whole, _ = results.record(db_session, tape, "description", vl, inputs=[TAPE_HASH], payload={"observed": "a party"})
    assert (a.span_start, a.span_end, b.span_end) == ("1001/25", "50", "301/25")  # exact: 12.04 s is 301/25
    assert results.span(a) == late
    assert results.current(db_session, tape, "description") == [whole, b, a]
    # another kind about the same source is not a rival: a measured result needs no model
    probe, _ = results.record(
        db_session, tape, "loudness", results.Provenance("ffmpeg-ebur128"), inputs=[TAPE_HASH], file="ab/abcdef.npy"
    )
    assert (probe.model, probe.payload, probe.file) == (None, None, "ab/abcdef.npy")
    assert db_session.query(Result).filter(Result.superseded_by.is_not(None)).count() == 0


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"inputs": [TAPE_HASH]}, "needs a payload or a file"),
        ({"inputs": [], "payload": 1}, "must name the files it read"),
        ({"inputs": [TAPE_HASH], "payload": 1, "confidence": 1.5}, "between 0 and 1"),
    ],
)
def test_a_result_without_what_it_needs_is_refused(db_session: Session, tape: Source, kwargs, message):
    with pytest.raises(ValueError, match=message):
        results.record(db_session, tape, "shots", results.Provenance("shot-boundaries"), **kwargs)
    assert results.history(db_session, tape, "shots") == []
