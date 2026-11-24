"""Derived assets as results with files (app/derived.py) and their jobs, over the test's migrated database. The app's
makers run for real; ffmpeg is stood in for at strata-python's edge (the `made` fixture in conftest.py), and
tests/integration makes real proxies, thumbnails and peaks from a real clip."""

import hashlib
import json
import os

import pytest
from probes import TAPE, answer
from sqlalchemy import Engine
from sqlalchemy.orm import Session
from strata_python import derive

from app import derived, jobs, results, settings, sources
from app.models import Source

SILENT = {**TAPE, "streams": [TAPE["streams"][0]]}


@pytest.fixture
def engine(db_env: str) -> Engine:
    return jobs._records()  # pyright: ignore[reportPrivateUsage]


def hashed(engine: Engine, tmp_path, raw=TAPE, name="tape.mkv") -> Source:
    f = tmp_path / name
    f.write_bytes(name.encode() * 500)
    with Session(engine, expire_on_commit=False) as s:
        source, _ = sources.add(s, str(f), probe=answer(raw))
        source.sha256 = "ab" * 32
        s.commit()
    return source


def test_a_source_gets_a_proxy_and_thumbnails_and_peaks_where_there_is_sound(engine, tmp_path):
    assert derived.wanted(hashed(engine, tmp_path)) == ["proxy", "thumbnails", "peaks"]
    assert derived.wanted(hashed(engine, tmp_path, SILENT, "silent.mkv")) == ["proxy", "thumbnails"]


def test_parameters_carry_the_recipe_version():
    assert derived.parameters("proxy") == {**derived.PROXY, "recipe": derive.RECIPE["proxy"]}
    assert derived.parameters("peaks")["rate"] == 8000


def test_an_asset_is_a_result_with_provenance_made_once(engine, tmp_path, made):
    source = hashed(engine, tmp_path)
    with Session(engine) as s:
        src = s.get(Source, source.id)
        assert src is not None
        r, new = derived.make(s, src, "proxy", job_id=None)
        assert new
        assert (r.kind, r.role, r.model, r.inputs) == ("proxy", "ffmpeg", None, ["ab" * 32])
        assert r.parameters == derived.parameters("proxy")
        assert r.cache_key == derived.key_for(src, "proxy")
        assert r.file == os.path.join("derived", r.cache_key, "proxy.mp4")
        assert os.path.isfile(derived.path_of(r))
        assert derived.path_of(r).startswith(settings.SETTINGS.home())
        again, new = derived.make(s, src, "proxy")
        assert (again.id, new) == (r.id, False)
        assert made == [("tape.mkv", "proxy")]  # found, not made again


def test_a_deleted_file_is_made_again_under_the_same_row(engine, tmp_path, made):
    source = hashed(engine, tmp_path)
    with Session(engine) as s:
        src = s.get(Source, source.id)
        assert src is not None
        r, _ = derived.make(s, src, "peaks")
        os.unlink(derived.path_of(r))
        again, new = derived.make(s, src, "peaks")
        assert (again.id, new) == (r.id, True)
        assert os.path.isfile(derived.path_of(r))
        assert made == [("tape.mkv", "peaks"), ("tape.mkv", "peaks")]


def test_a_changed_recipe_supersedes_the_old_asset(engine, tmp_path, monkeypatch):
    source = hashed(engine, tmp_path)
    with Session(engine) as s:
        src = s.get(Source, source.id)
        assert src is not None
        old, _ = derived.make(s, src, "thumbnails")
        monkeypatch.setattr(derived, "THUMBNAILS", {**derived.THUMBNAILS, "every": "5"})
        new, made_now = derived.make(s, src, "thumbnails")
        assert made_now
        assert new.id != old.id
        assert [r.id for r in results.current(s, src, "thumbnails")] == [new.id]
        assert [r.id for r in results.history(s, src, "thumbnails")] == [old.id, new.id]


def test_nothing_is_made_before_the_hash_or_of_an_unknown_kind(engine, tmp_path):
    f = tmp_path / "raw.mkv"
    f.write_bytes(b"raw" * 500)
    with Session(engine) as s:
        src, _ = sources.add(s, str(f), probe=answer(TAPE))
        with pytest.raises(ValueError, match="no content hash yet"):
            derived.make(s, src, "proxy")
        with pytest.raises(ValueError, match="makes no 'restored'"):
            derived.make(s, src, "restored")
        assert jobs.derive_later(jobs.queue(engine), src) == []


def test_path_of_needs_a_file(engine, tmp_path):
    source = hashed(engine, tmp_path)
    with Session(engine) as s:
        src = s.get(Source, source.id)
        assert src is not None
        r, _ = results.record(s, src, "note", results.Provenance("app"), inputs=["ab" * 32], payload={"x": 1})
        with pytest.raises(KeyError, match="has no file"):
            derived.path_of(r)


def test_a_tape_added_again_queues_its_assets_at_once(engine, tmp_path, made, tape_models):
    source = hashed(engine, tmp_path)
    q = jobs.queue(engine)
    ids = jobs.ingest_later(q, source)
    assert len(ids) == 5  # the three assets, the shots and the speech
    assert jobs.ingest_later(q, source) == ids  # while queued: the same jobs
    ran = jobs.work(q)
    assert [(j.kind, j.state) for j in ran] == [
        ("proxy", "done"), ("thumbnails", "done"), ("peaks", "done"), ("shots", "done"), ("transcript", "done"),
        ("keyframes", "done"), ("scenes", "done"), ("faces", "done"), ("describe", "done"),
    ]  # fmt: skip
    assert ran[0].result is not None
    assert ran[0].result["new"] is True
    jobs.ingest_later(q, source)  # done once: the jobs run again and find what is stored
    assert all(j.result is not None and j.result["new"] is False for j in jobs.work(q))
    assert len(made) == 4
    # asked once each (a description per scene)
    assert [j["role"] for j in tape_models.jobs] == [
        "shot-boundaries", "transcribe", "image-embed", "faces", "scene-describe", "scene-describe",
    ]  # fmt: skip


def test_an_asset_job_refuses_a_changed_file_and_a_missing_source(engine, tmp_path, made):
    source = hashed(engine, tmp_path)
    (tmp_path / "tape.mkv").write_bytes(b"other footage" * 500)
    q = jobs.queue(engine)
    jobs.derive_later(q, source)
    q.enqueue("proxy", {"source": 404})
    ran = jobs.work(q)
    assert "tape.mkv has changed since it was added" in (ran[0].error or "")
    assert "there is no source 404" in (ran[-1].error or "")
    assert made == []


def test_each_asset_says_what_its_file_is(engine, tmp_path, made):
    source = hashed(engine, tmp_path)
    with Session(engine) as s:
        src = s.get(Source, source.id)
        assert src is not None
        proxy, _ = derived.make(s, src, "proxy")
        thumbs, _ = derived.make(s, src, "thumbnails")
        peaks, _ = derived.make(s, src, "peaks")
        home = settings.SETTINGS.home()
        assert proxy.payload == {
            "sha256": hashlib.sha256(b"mp4").hexdigest(),
            "bytes": 3,
            "width": 720,
            "height": 540,
            "interlace": "tff",
            "audio": True,
        }
        assert thumbs.file == os.path.join("derived", thumbs.cache_key, "thumbnails")
        assert thumbs.payload == {
            "thumbnails": [{"time": "0", "name": "000000.jpg"}, {"time": "10", "name": "000001.jpg"}]
        }
        assert sorted(os.listdir(os.path.join(home, thumbs.file))) == ["000000.jpg", "000001.jpg"]
        assert (peaks.payload["per_second"], peaks.payload["slices"], peaks.payload["seconds"]) == (50, 2, "1/25")
        with open(derived.path_of(peaks)) as f:
            assert json.load(f) == {"per_second": 50, "min": [-0.5, -0.25], "max": [0.5, 0.25]}
