from __future__ import annotations

import copy
import json
import time
from datetime import datetime
from pathlib import Path

from .questions import Question
from .voicevox import RenderedAudio, encode_wav


def _now():
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


class PracticeSession:
    def __init__(
        self, directory: Path, question: Question, audio: RenderedAudio, *, clock=time.monotonic
    ):
        self.directory, self.question, self.audio = directory, question, audio
        self.clock = clock
        self.transcription_start = None
        self.record = {
            "schema_version": 2,
            "created_at": _now(),
            "state": "prepared",
            "outcome": None,
            "question": {
                "options": question.options,
                "seed": question.seed,
                "source_file": question.source_file,
                "characters": question.characters,
                "count_policy": "unicode_characters_excluding_whitespace_and_punctuation",
                "word_count": len(question.words) if question.is_words else None,
            },
            "audio": audio.metadata(),
            "events": [],
            "transcription_seconds": None,
            "playback_pauses": 0,
        }

    @classmethod
    def create(cls, root: Path, question: Question, audio: RenderedAudio):
        root = root.expanduser()
        root.mkdir(parents=True, exist_ok=True)
        for _ in range(10):
            name = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S.%f%z")
            directory = root / name
            try:
                directory.mkdir()
                break
            except FileExistsError:
                continue
        else:
            raise OSError("練習記録のディレクトリを作成できません。")
        session = cls(directory, question, audio)
        try:
            (directory / "problem.txt").write_text(question.text + "\n", encoding="utf-8")
            (directory / "audio.wav").write_bytes(encode_wav(audio.samples, audio.sample_rate))
            (directory / "credits.txt").write_text(
                f"{audio.voice.credit}\nスタイル: {audio.voice.style}\n"
                f"VOICEVOX ENGINE: {audio.engine_version}\n"
                "音声公開・再配布時は選択した話者の利用規約をご確認ください。\n"
                "https://github.com/VOICEVOX/voicevox_vvm#readme\n",
                encoding="utf-8",
            )
            session.save()
        except OSError:
            # Only remove files that this method created, never a pre-existing session.
            for filename in (
                "problem.txt",
                "audio.wav",
                "credits.txt",
                "session.json.tmp",
                "session.json",
            ):
                (directory / filename).unlink(missing_ok=True)
            directory.rmdir()
            raise
        return session

    @property
    def state(self):
        return self.record["state"]

    @property
    def transcription_seconds(self):
        if self.transcription_start is not None:
            return self.clock() - self.transcription_start
        return self.record["transcription_seconds"] or 0

    def save(self, record=None):
        temp = self.directory / "session.json.tmp"
        temp.write_text(
            json.dumps(self.record if record is None else record, ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )
        temp.replace(self.directory / "session.json")

    def _transition(self, expected, state, **updates):
        if self.state not in expected:
            raise ValueError(f"現在の状態 ({self.state}) ではこの操作はできません。")
        record = copy.deepcopy(self.record)
        record.update(state=state, **updates)
        record["events"].append({"state": state, "at": _now()})
        self.save(record)
        self.record = record

    def start_playback(self):
        self._transition(("prepared",), "playing")

    def pause(self):
        if self.state != "playing":
            raise ValueError("再生中ではありません。")
        self._transition(("playing",), "paused", playback_pauses=self.record["playback_pauses"] + 1)

    def resume(self):
        self._transition(("paused",), "playing")

    def finish_playback(self):
        if self.state != "playing":
            raise ValueError("再生中ではありません。")
        started = self.clock()
        self._transition(("playing",), "transcribing")
        self.transcription_start = started

    def reveal(self):
        if self.state not in ("transcribing", "aborted"):
            raise ValueError("原文は再生・反訳の終了後に表示できます。")
        updates = {}
        if self.state == "transcribing":
            updates = {"transcription_seconds": self.transcription_seconds, "outcome": "completed"}
        self._transition(("transcribing", "aborted"), "revealed", **updates)
        self.transcription_start = None
        return self.question.text

    def abort(self):
        if self.state in ("revealed", "aborted"):
            return
        updates = {"outcome": "aborted"}
        if self.transcription_start is not None:
            updates["transcription_seconds"] = self.transcription_seconds
        self._transition(("prepared", "playing", "paused", "transcribing"), "aborted", **updates)
        self.transcription_start = None
