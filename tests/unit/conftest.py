"""Unit-suite only: the hypothesis profile and ffmpeg's stand-in for derived assets (the shared fixtures are in
tests/conftest.py and the strata-python plugin)."""

import os
from fractions import Fraction

import pytest
from hypothesis import HealthCheck, settings
from probes import FACES, SHOTS, SPEECH, describe_scene, embeddings, name_chapter
from strata_python import derive, files, media, mediatime, render

settings.register_profile(
    "unit", deadline=None, max_examples=50, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
settings.load_profile("unit")


@pytest.fixture(autouse=True)
def made(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """ffmpeg, stood in for at strata-python's edge (`strata_python.derive`), since a unit test starts no processes.
    The app's own makers (`app.derived`) run for real on top; strata-python tests these functions against the real
    ffmpeg, and tests/integration runs the whole path on a real clip. Each stand-in writes what the real one would (an
    MP4 file, a folder of JPEGs, peaks) and records (file name, kind); the list is the fixture's value."""
    calls: list[tuple[str, str]] = []

    def interlacing(path, *, info=None, **_):
        return "tff"

    def proxy(src, out, *, info=None, progress=None, **_):
        calls.append((os.path.basename(src), "proxy"))
        if progress is not None:
            progress(1.0)
        os.makedirs(os.path.dirname(out), exist_ok=True)  # as files.atomic_path does
        with open(out, "wb") as f:
            f.write(b"mp4")
        return media.Probe.from_json(os.fspath(out), PROXY_PROBE)

    def thumbnails(src, folder, *, every=10, **_):
        calls.append((os.path.basename(src), "thumbnails"))
        os.makedirs(folder, exist_ok=True)
        step = mediatime.rational(every)
        out = []
        for i in range(2):
            path = os.path.join(folder, f"{i:06d}.jpg")
            with open(path, "wb") as f:
                f.write(b"jpg")
            out.append(derive.Thumb(i * step, path))
        return out

    def frames(src, folder, numbers, **_):
        calls.append((os.path.basename(src), "keyframes"))
        os.makedirs(folder, exist_ok=True)
        out = []
        for n in sorted(set(numbers)):
            path = os.path.join(folder, f"{n:06d}.jpg")
            with open(path, "wb") as f:
                f.write(b"jpg")
            out.append(derive.Frame(n, path))
        return out

    def peaks(src, *, per_second=50, **_):
        calls.append((os.path.basename(src), "peaks"))
        return derive.Peaks(per_second, [-0.5, -0.25], [0.5, 0.25])

    def rendered(cuts, out, *, output=None, progress=None, **_):
        # strata_python.render's stand-in: a file and the Rendered it would describe (strata-python tests the real one
        # against ffmpeg; tests/integration renders a real clip through the app).
        calls.append(("+".join(os.path.basename(c.path) for c in cuts), "render"))
        if progress is not None:
            progress(1.0)
        body = repr([(c.path, str(c.span.start), str(c.span.end), c.gain_db) for c in cuts]).encode()
        with open(out, "wb") as f:
            f.write(body)
        frames = sum(len(c.span.frames(mediatime.Rate.parse(25))) for c in cuts)
        made = render.Rendered(
            probe=media.Probe.from_json(os.fspath(out), PROXY_PROBE),
            frames=frames,
            duration=Fraction(frames, 25),
            rate=mediatime.Rate.parse(25),
            width=768,
            height=576,
            command=["-i", *[c.path for c in cuts], "-f", "mp4", os.fspath(out)],
            ffmpeg="test (x264 core 0)",
            sha256=files.sha256_hex(out),
            bytes=len(body),
            cuts=[{"path": c.path, "span": c.span.to_json()} for c in cuts],
            output=(output or render.Output()).to_json(),
        )
        return made

    monkeypatch.setattr(render, "render", rendered)
    for name, fn in [
        ("interlacing", interlacing),
        ("proxy", proxy),
        ("thumbnails", thumbnails),
        ("peaks", peaks),
        ("frames", frames),
    ]:
        monkeypatch.setattr(derive, name, fn)
    return calls


PROXY_PROBE = {
    "format": {"duration": "5400.04", "format_name": "mov,mp4,m4a,3gp,3g2,mj2"},
    "streams": [
        {"codec_type": "video", "codec_name": "h264", "width": 720, "height": 540},
        {"codec_type": "audio", "codec_name": "aac", "channels": 2},
    ],
}


@pytest.fixture
def tape_models(fake_models):
    """strata-models, stood in for over real HTTP (strata-python's `fake_models`), answering shots, vectors, speech,
    faces and descriptions for the tape. Its settings are set, so each result's provenance has a model and a settings
    hash."""
    fake_models.results.update(
        {
            "shot-boundaries": SHOTS,
            "image-embed": embeddings,
            "transcribe": SPEECH,
            "faces": FACES,
            "scene-describe": describe_scene,
            "chapter-name": name_chapter,
        }
    )
    fake_models.models.update(
        {
            "shot-boundaries": "ffmpeg-scene-score",
            "image-embed": "clip-vit-b-32",
            "transcribe": "whisper-large-v3-turbo",
            "faces": "buffalo_l",
            "scene-describe": "qwen3.5-9b-vl",
            "chapter-name": "qwen3.5-9b-vl",
        }
    )
    fake_models.options.update({"shot-boundaries": {"threshold": 0.3}, "image-embed": {"half": True}})
    return fake_models
