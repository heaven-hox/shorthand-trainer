import json
from pathlib import Path

import pytest

from shorthand_trainer.questions import QuestionOptions, generate_question
from shorthand_trainer.sessions import PracticeSession
from shorthand_trainer.voicevox import AudioSettings, decode_wav, render_audio


def test_practice_lifecycle_keeps_answer_hidden_until_transcription(engine, tmp_path):
    question = generate_question(QuestionOptions(pattern="あか", count=1, seed=3))
    audio = render_audio(question, AudioSettings(), engine.client, engine.voice)
    session = PracticeSession.create(tmp_path, question, audio)
    other = PracticeSession.create(tmp_path, question, audio)
    assert session.directory != other.directory
    assert ":" not in session.directory.name
    with pytest.raises(ValueError, match="再生・反訳"):
        session.reveal()
    session.start_playback()
    session.pause()
    session.resume()
    now = [100.0]
    session.clock = lambda: now[0]
    session.finish_playback()
    now[0] += 13.5
    assert session.reveal() == question.text
    record = json.loads((session.directory / "session.json").read_text(encoding="utf-8"))
    assert record["outcome"] == "completed"
    assert record["state"] == "revealed"
    assert record["transcription_seconds"] == 13.5
    assert record["playback_pauses"] == 1
    assert record["question"]["seed"] == 3
    assert record["audio"]["credit"] == "VOICEVOX:テスト話者"
    assert [e["state"] for e in record["events"]] == [
        "playing",
        "paused",
        "playing",
        "transcribing",
        "revealed",
    ]
    saved_audio, rate = decode_wav((session.directory / "audio.wav").read_bytes())
    assert rate == audio.sample_rate
    assert len(saved_audio) == len(audio.samples)
    assert (session.directory / "problem.txt").read_text(encoding="utf-8").strip() == question.text
    assert not (session.directory / "session.json.tmp").exists()


def test_aborted_practice_is_not_reported_as_completed(engine, tmp_path):
    q = generate_question(QuestionOptions(count=1, seed=5))
    audio = render_audio(q, AudioSettings(), engine.client, engine.voice)
    session = PracticeSession.create(tmp_path, q, audio)
    session.start_playback()
    session.abort()
    assert session.reveal() == q.text
    assert session.record["outcome"] == "aborted"
    assert session.record["transcription_seconds"] is None


def test_failed_creation_cleans_up_only_its_own_directory(engine, tmp_path, monkeypatch):
    q = generate_question(QuestionOptions(count=1, seed=5))
    audio = render_audio(q, AudioSettings(), engine.client, engine.voice)
    old = tmp_path / "existing"
    old.mkdir()
    (old / "keep.txt").write_text("keep", encoding="utf-8")
    original = Path.write_bytes

    def fail(self, data):
        if self.name == "audio.wav":
            raise OSError("disk full")
        return original(self, data)

    monkeypatch.setattr(Path, "write_bytes", fail)
    with pytest.raises(OSError, match="disk full"):
        PracticeSession.create(tmp_path, q, audio)
    assert list(tmp_path.iterdir()) == [old]


def test_failed_transition_can_be_retried_without_claiming_completion(
    engine, tmp_path, monkeypatch
):
    question = generate_question(QuestionOptions(count=1, seed=5))
    audio = render_audio(question, AudioSettings(), engine.client, engine.voice)
    session = PracticeSession.create(tmp_path, question, audio)
    session.start_playback()
    session.finish_playback()
    original_save = session.save

    def fail(_record=None):
        raise OSError("disk full")

    monkeypatch.setattr(session, "save", fail)
    with pytest.raises(OSError, match="disk full"):
        session.reveal()
    assert session.state == "transcribing"
    assert session.record["outcome"] is None
    assert session.transcription_start is not None
    monkeypatch.setattr(session, "save", original_save)
    assert session.reveal() == question.text
    assert session.record["outcome"] == "completed"
