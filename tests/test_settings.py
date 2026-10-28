import json
from pathlib import Path

import pytest

from shorthand_trainer.settings import SettingsError, SettingsStore, default_settings_path


def test_settings_round_trip_handles_unicode_booleans_and_empty_values(tmp_path):
    path = tmp_path / "日本語 の 設定" / "settings.json"
    store = SettingsStore(path)
    assert store.load() == {}
    assert not path.parent.exists()
    values = {
        "pattern": "[あいう]{2}",
        "word_path": "C:/資料/単語.txt",
        "duplicates": True,
        "count": "12",
        "seed": "",
    }
    store.save(values)
    assert SettingsStore(path).load() == values
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 1
    assert list(path.parent.iterdir()) == [path]


@pytest.mark.parametrize(
    "data",
    [
        b"broken",
        b"\xff",
        b"[]",
        b'{"schema_version": 99}',
        b'{"schema_version": 1, "values": []}',
        b" " * 65537,
        b"[" * 2000 + b"]" * 2000,
    ],
)
def test_unusable_settings_are_reported_without_changing_the_file(tmp_path, data):
    path = tmp_path / "settings.json"
    path.write_bytes(data)
    with pytest.raises(SettingsError):
        SettingsStore(path).load()
    assert path.read_bytes() == data


def test_failed_replacement_keeps_previous_settings_and_cleans_temporary_file(
    tmp_path, monkeypatch
):
    path = tmp_path / "settings.json"
    store = SettingsStore(path)
    store.save({"pattern": "あ"})

    def fail(_source, _target):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(OSError, match="disk full"):
        store.save({"pattern": "い"})
    assert store.load() == {"pattern": "あ"}
    assert list(tmp_path.iterdir()) == [path]


def test_windows_uses_roaming_appdata_and_linux_respects_xdg(monkeypatch, tmp_path):
    from shorthand_trainer import settings

    monkeypatch.setattr(settings.sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert default_settings_path() == tmp_path / "ShorthandTrainer" / "settings.json"
    monkeypatch.setattr(settings.sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert default_settings_path() == tmp_path / "shorthand-trainer" / "settings.json"
