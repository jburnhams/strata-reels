"""Speech, faces and scene descriptions (app/speech.py, app/describe.py) over the test's migrated database, with
strata-models stood in for over real HTTP (`tape_models` in conftest.py: speech in both scenes, two faces in the first,
a description that says what it was given) and ffmpeg at strata-python's edge (`made`)."""

import pytest
from probes import SPEECH, TAPE, answer, describe_scene
from sqlalchemy import Engine
from sqlalchemy.orm import Session
from strata_python.clients import models as sm

from app import describe, jobs, results, scenes, shots, sources, speech
from app.models import Source


@pytest.fixture
def engine(db_env: str) -> Engine:
    return jobs._records()  # pyright: ignore[reportPrivateUsage]


def hashed(engine: Engine, tmp_path, raw=TAPE) -> Source:
    f = tmp_path / "tape.mkv"
    f.write_bytes(b"tape" * 500)
    with Session(engine, expire_on_commit=False) as s:
        source, _ = sources.add(s, str(f), probe=answer(raw))
        source.sha256 = "ab" * 32
        s.commit()
    return source


def grouped(s: Session, src: Source) -> None:
    shots.detect(s, src)
    shots.keyframes(s, src)
    scenes.find_scenes(s, src)


SILENT = {**TAPE, "streams": [TAPE["streams"][0]]}


def test_the_transcript_keeps_doubtful_segments_marked_and_in_time_order():
    p = speech.payload_of(SPEECH)
    assert [(x["start"], x["text"], x["doubtful"]) for x in p["segments"]] == [
        (1.0, "Thank you.", True), (6.0, "Say hello to Grandma!", False), (11.0, "Happy birthday to you", False),
    ]  # fmt: skip
    assert p["segments"][1]["words"] == [{"start": 6.0, "end": 6.3, "word": " Say", "probability": 0.9}]
    assert (p["language"], p["language_probability"], p["duration"]) == ("en", 0.97, 16.0)
    low = speech.payload_of({"segments": [{"start": 0, "end": 1, "text": "mm", "avg_logprob": -1.4}]})
    assert low["segments"][0]["doubtful"]
    assert low["language"] is None


def segment(text: str, start: float, end: float, probability: float = 0.9) -> dict:
    words = [{"start": start, "end": end, "word": w, "probability": probability} for w in text.split()]
    return {"start": start, "end": end, "text": text, "words": words, "avg_logprob": -0.3, "no_speech_prob": 0.1}


def doubted(*segments: dict) -> list[str]:
    return [s["text"] for s in speech.payload_of({"segments": list(segments)})["segments"] if s["doubtful"]]


def test_what_whisper_made_up_on_a_real_tape_is_doubtful():
    again = "Where are you going to now?"
    stuck = [segment(again, 10 + i, 11 + i) for i in range(5)]
    assert doubted(segment("Over here.", 8, 9), *stuck) == [again] * 4  # the first may have been said
    assert doubted(*(segment("Look at that.", i, i + 1) for i in range(3))) == []  # how people talk to a child
    loop = "Don't, don't, don't, don't, don't, don't, don't, don't, don't, don't."
    assert doubted(segment(loop, 0, 4), segment("Come on, come on, come on.", 5, 18)) == [loop]
    assert doubted(segment("Good.", 0, 1, probability=0.02)) == ["Good."]
    assert doubted(segment("Okay.", 5, 5.02), segment("Look.", 6, 6.3)) == ["Okay."]  # a word in a fiftieth of a second
    assert doubted({"start": 0, "end": 1, "text": "", "words": []}) == []


def test_a_scene_hears_only_its_own_speech_and_not_too_much():
    p = speech.payload_of(SPEECH)
    assert speech.said_in(p, 0, 10) == "Say hello to Grandma!"  # the doubtful "Thank you." is left out
    assert speech.said_in(p, 7, 12) == "Say hello to Grandma! Happy birthday to you"  # overlapping counts
    assert speech.said_in(p, 14, 16) == ""
    assert speech.said_in(None, 0, 10) == ""
    long = {"segments": [{"start": 0, "end": 1, "text": "word " * 1000, "doubtful": False}]}
    cut = speech.said_in(long, 0, 1)
    assert cut.endswith(" …")
    assert len(cut) <= speech.SAID_CHARS + 2


def test_the_transcript_records_what_made_it_and_is_asked_once(engine, tmp_path, tape_models):
    source = hashed(engine, tmp_path)
    with Session(engine) as s:
        src = s.get(Source, source.id)
        assert src is not None
        assert speech.current(s, src) is None
        r, new = speech.transcribe(s, src)
        assert new
        assert (r.kind, r.role, r.model, r.parameters) == (
            "transcript", "transcribe", "whisper-large-v3-turbo", speech.PARAMETERS,
        )  # fmt: skip
        job = tape_models.jobs[0]
        assert (job["params"]["word_timestamps"], job["params"]["vad_filter"], job["priority"]) == (True, True, "low")
        assert speech.transcribe(s, src) == (r, False)
        assert len(tape_models.jobs) == 1
        assert speech.current(s, src) == r


def test_the_transcript_needs_the_hash_and_sound(engine, tmp_path):
    with Session(engine) as s:
        f = tmp_path / "t.mkv"
        f.write_bytes(b"t" * 100)
        src, _ = sources.add(s, str(f), probe=answer(TAPE))
        with pytest.raises(ValueError, match="no content hash"):
            speech.transcribe(s, src)
        src.sha256 = "cd" * 32
        src.probe = SILENT
        with pytest.raises(ValueError, match="no sound"):
            speech.transcribe(s, src)
        with pytest.raises(ValueError, match="no content hash"):
            describe.find_faces(s, Source(fingerprint="f", path="/x", name="x", size=1, duration=1.0, probe=TAPE))


def test_frames_are_spread_through_a_scene():
    assert describe.pick([1, 2]) == [1, 2]
    assert describe.pick([1, 2, 3, 4, 5, 6, 7], 3) == [1, 4, 7]
    assert len(describe.pick(list(range(30)))) == describe.FRAMES
    assert describe.pick([1, 2, 3, 4], 1) == [3]
    found = {"shots": [{"start_frame": 0, "end_frame": 10}, {"start_frame": 10, "end_frame": 20}]}
    assert describe.scene_frames(found, {"shots": [0, 1]}, {4, 14}) == [4, 14]
    assert describe.scene_frames(found, {"shots": [0, 1]}, {14}) == [14]  # a shot with no keyframe has none
    assert describe.scene_frames(found, {"shots": [1]}, {2, 8, 12, 18}) == [12, 18]  # every keyframe of its own shots


def test_each_scene_is_described_from_its_frames_and_its_speech(engine, tmp_path, tape_models):
    source = hashed(engine, tmp_path)
    with Session(engine) as s:
        src = s.get(Source, source.id)
        assert src is not None
        with pytest.raises(KeyError, match="no scenes yet"):
            describe.describe(s, src)
        grouped(s, src)
        said, _ = speech.transcribe(s, src)
        made = describe.describe(s, src)
        assert [new for _, new in made] == [True, True]
        first, second = (r for r, _ in made)
        assert (first.span_start, first.span_end, second.span_start, second.span_end) == ("0", "10", "10", "16")
        assert (first.role, first.model, first.kind) == ("scene-describe", "qwen3.5-9b-vl", "description")
        assert first.payload["frames"] == [49, 174]
        assert first.payload["speech"] == "Say hello to Grandma!"
        assert first.payload["description"]["inferred"] == [{"guess": "a birthday", "because": "a cake"}]
        assert first.parameters["transcript"] == said.cache_key
        assert first.parameters["preset"] == describe.PRESET
        asked = [j["params"] for j in tape_models.jobs if j["role"] == "scene-describe"]
        assert [a["context"] for a in asked] == [
            {"seconds": 10.0, "shots": 2, "speech": "Say hello to Grandma!"},
            {"seconds": 6.0, "shots": 1, "speech": "Happy birthday to you"},
        ]
        assert all(a["preset"] == "scene-describe-v2" for a in asked)
        assert [a["images"][0].endswith("000049.jpg") for a in asked] == [True, False]
        assert describe.describe(s, src) == [(first, False), (second, False)]  # stored: not asked again
        assert len(tape_models.jobs) == 5  # shots, vectors, speech and the two scenes
        assert describe.current_descriptions(s, src) == {"0": first, "10": second}


def test_a_description_that_stops_part_way_keeps_the_scenes_done(engine, tmp_path, tape_models):
    calls: list[int] = []

    def flaky(params):
        calls.append(1)
        return describe_scene(params) if len(calls) != 2 else {"text": "a birthday, I think"}  # no JSON: refused

    tape_models.results["scene-describe"] = flaky
    source = hashed(engine, tmp_path, SILENT)
    with Session(engine) as s:
        src = s.get(Source, source.id)
        assert src is not None
        grouped(s, src)
        with pytest.raises(sm.JobFailed, match="did not answer in its schema for the scene at 10"):
            describe.describe(s, src)
        assert len(results.current(s, src, "description")) == 1
        made = describe.describe(s, src)  # the retry asks only for the scene that failed
        assert [new for _, new in made] == [False, True]
        assert made[0][0].parameters["transcript"] is None  # no sound: no transcript to wait for
        assert made[0][0].payload["speech"] == ""
        assert len(calls) == 3


def test_faces_are_counted_on_the_keyframes_without_recognising_anyone(engine, tmp_path, tape_models):
    source = hashed(engine, tmp_path)
    with Session(engine) as s:
        src = s.get(Source, source.id)
        assert src is not None
        with pytest.raises(KeyError, match="no shots yet"):
            describe.find_faces(s, src)
        assert describe.current_faces(s, src) is None
        shots.detect(s, src)
        assert describe.current_faces(s, src) is None  # no keyframes yet either
        with pytest.raises(KeyError, match="no keyframes yet"):
            describe.find_faces(s, src)
        shots.keyframes(s, src)
        r, new = describe.find_faces(s, src)
        assert new
        assert (r.kind, r.role, r.model) == ("faces", "faces", "buffalo_l")
        (job,) = [j for j in tape_models.jobs if j["role"] == "faces"]
        assert job["params"]["fields"] == ["bbox", "det_score"]  # no embedding is asked for
        assert [len(f["faces"]) for f in r.payload["frames"]] == [2, 2, 0]
        assert r.payload["frames"][0]["faces"][0] == {"box": [10.0, 20.0, 60.0, 80.0], "score": 0.91}
        assert describe.find_faces(s, src) == (r, False)
        assert describe.current_faces(s, src) == r
        g, _ = scenes.find_scenes(s, src)
        found = shots.current(s, src)
        assert found is not None
        assert [describe.people_in(r.payload, found.payload, sc) for sc in g.payload["scenes"]] == [2, 0]
        tape_models.results["faces"] = {"images": []}
        tape_models.options["faces"] = {"det_size": 320}  # new settings: asked again, and checked
        with pytest.raises(sm.JobFailed, match="in 0 of 3 keyframes"):
            describe.find_faces(s, src)


def test_no_scene_is_described_before_the_speech_of_a_tape_with_sound(engine, tmp_path, tape_models):
    # The describe stage needs the transcript stage, so a transcript that fails leaves the scenes undescribed (the
    # stage blocked) rather than described without what is said; the retry describes them once it is there.
    source = hashed(engine, tmp_path)
    tape_models.fail("the transcriber ran out of memory", role="transcribe")
    q = jobs.queue(engine)
    jobs.ingest_later(q, source)
    (first,) = jobs.work(q)
    assert first.state == "queued"  # an outside failure: tried again later
    run = jobs.TAPE.status(jobs.store(), jobs.subject_of(source), jobs.inputs_of(jobs.subject_of(source)))
    assert (run.states["scenes"], run.states["transcript"], run.states["describe"]) == ("done", "failed", "blocked")
    assert "scene-describe" not in [j["role"] for j in tape_models.jobs]
    q.enqueue(jobs.TAPE.kind, {"subject": jobs.subject_of(source)})  # the retry, without waiting for its backoff
    jobs.work(q)
    records = jobs.store().load(jobs.subject_of(source))
    assert (records["transcript"].status, records["describe"].metrics) == ("done", {"new": 2})
