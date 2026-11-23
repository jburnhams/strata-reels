import os

import pytest

from app import settings


def test_the_home_is_named_reels_and_moves_with_its_variable(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setenv("REELS_HOME", str(tmp_path / "elsewhere"))
    assert settings.SETTINGS.home() == str(tmp_path / "elsewhere")


def test_there_are_no_settings_yet_and_an_unknown_one_is_refused():
    assert settings.SETTINGS.load() == {}
    assert settings.SETTINGS.save({}) == {}
    assert os.path.isfile(os.path.join(settings.SETTINGS.home(), "settings.json"))
    with pytest.raises(KeyError, match="greeting"):
        settings.SETTINGS.save({"greeting": "Hi"})  # the template's sample setting went with the sample
