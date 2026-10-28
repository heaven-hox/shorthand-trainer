"""練習記録とは別に、前回終了時の入力設定を保存する。"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path


class SettingsError(ValueError):
    pass


def default_settings_path() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
        return base / "ShorthandTrainer" / "settings.json"
    if sys.platform == "darwin":
        return (
            Path.home() / "Library" / "Application Support" / "ShorthandTrainer" / "settings.json"
        )
    configured = os.environ.get("XDG_CONFIG_HOME", "")
    base = (
        Path(configured)
        if configured and Path(configured).is_absolute()
        else Path.home() / ".config"
    )
    return base / "shorthand-trainer" / "settings.json"


class SettingsStore:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> dict:
        try:
            with self.path.open("rb") as source:
                payload = source.read(64 * 1024 + 1)
        except FileNotFoundError:
            return {}
        if len(payload) > 64 * 1024:
            raise SettingsError("設定ファイルが大きすぎます。")
        try:
            data = json.loads(payload)
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise SettingsError("設定ファイルのJSONを読み取れません。") from exc
        if not isinstance(data, dict) or data.get("schema_version") != 1:
            raise SettingsError("対応していない設定ファイルの形式です。")
        if not isinstance(data.get("values"), dict):
            raise SettingsError("設定ファイルの入力値を読み取れません。")
        return data["values"]

    def save(self, values: dict):
        payload = (
            json.dumps({"schema_version": 1, "values": values}, ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8")
        if len(payload) > 64 * 1024:
            raise SettingsError("入力設定が大きすぎるため保存できません。")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=self.path.parent, prefix=self.path.name + ".", suffix=".tmp", delete=False
            ) as output:
                temporary = Path(output.name)
                output.write(payload)
            temporary.replace(self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
