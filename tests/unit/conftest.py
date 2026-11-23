"""Unit-suite only: the hypothesis profile and ffmpeg's stand-in for derived assets (the shared fixtures are in
tests/conftest.py and the strata-python plugin)."""

import os

import pytest
from hypothesis import HealthCheck, settings

from app import derived
from app import settings as app_settings

settings.register_profile(
    "unit", deadline=None, max_examples=50, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
settings.load_profile("unit")


@pytest.fixture(autouse=True)
def made(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, str]]:
    """ffmpeg's part of each derived asset, stood in for (a unit test starts no processes; tests/integration makes the
    real ones from real footage). Each stand-in writes a small file where the real one would and records
    (source id, kind); the list is the fixture's value."""
    calls: list[tuple[int, str]] = []

    def maker(kind: str, name: str):
        def make(source, key):
            calls.append((source.id, kind))
            rel = os.path.join(derived.folder(key), name)
            path = os.path.join(app_settings.SETTINGS.home(), rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(kind)
            return derived.Made(rel, {"kind": kind})

        return make

    monkeypatch.setattr(
        derived,
        "MAKERS",
        {
            "proxy": maker("proxy", "proxy.mp4"),
            "thumbnails": maker("thumbnails", "thumbnails"),
            "peaks": maker("peaks", "peaks.json"),
        },
    )
    return calls
