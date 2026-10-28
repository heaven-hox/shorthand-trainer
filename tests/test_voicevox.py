import threading
from dataclasses import replace

import numpy as np
import pytest

from shorthand_trainer.questions import Question, QuestionOptions, generate_question
from shorthand_trainer.voicevox import (
    AudioSettings,
    Cancelled,
    VoicevoxClient,
    VoicevoxError,
    decode_wav,
    encode_wav,
    render_audio,
    speech_units,
)


@pytest.mark.parametrize("voice_type", ["talk", "streaming_talk"])
def test_http_client_reads_voices_and_bypasses_local_proxy(engine, monkeypatch, voice_type):
    engine.voice_type = voice_type
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:1")
    client = VoicevoxClient(engine.url)
    assert client.version() == "test-engine"
    assert client.voices() == [engine.voice]
    engine.empty_voices = True
    with pytest.raises(VoicevoxError, match="声が見つかりません"):
        client.voices()
    with pytest.raises(VoicevoxError, match="HTTP 404"):
        client._request("/missing")


@pytest.mark.parametrize(
    "url",
    [
        "file:///tmp/x",
        "http://u:password@localhost:1",
        "http://",
        "http://localhost/api",
        "http://localhost?q=x",
    ],
)
def test_invalid_engine_urls_are_rejected(url):
    with pytest.raises(ValueError):
        VoicevoxClient(url)


@pytest.mark.parametrize("speed", [0.5, 1.0, 2.0])
def test_manual_speed_preserves_engine_audio_and_intervals_and_caches_queries(engine, speed):
    q = generate_question(QuestionOptions(pattern="あか", count=3, duplicates=True, seed=1))
    settings = AudioSettings(speed_scale=speed, sentence_pause=2, pause_cpm=120)
    audio = render_audio(q, settings, engine.client, engine.voice)
    assert audio.speech_seconds == pytest.approx(0.72 / speed, abs=0.001)
    assert audio.pause_seconds == 2
    assert audio.duration == pytest.approx(0.72 / speed + 2, abs=0.001)
    assert audio.spoken_cpm == pytest.approx(6 * 60 / audio.speech_seconds)
    assert audio.overall_cpm == pytest.approx(6 * 60 / audio.duration)
    # Three spoken words, two one-second silent intervals, no trailing silence.
    spoken_frames = round(0.24 / speed * 24000)
    assert np.all(audio.samples[:spoken_frames] == 1000)
    assert np.all(audio.samples[spoken_frames : spoken_frames + 24000] == 0)
    assert audio.samples[-1] == 1000
    requests = [r for r in engine.requests if r[0] == "POST"]
    assert len([r for r in requests if r[1] == "/audio_query"]) == 1
    assert len([r for r in requests if r[1] == "/synthesis"]) == 1
    synthesis = next(r[2] for r in requests if r[1] == "/synthesis")
    assert synthesis["params"]["speaker"] == ["7"]
    assert synthesis["body"]["accent_phrases"][0]["pause_mora"] is None
    assert synthesis["body"]["prePhonemeLength"] == 0
    assert synthesis["body"]["speedScale"] == speed
    assert synthesis["body"]["accent_phrases"][0]["moras"][0]["vowel_length"] == 0.08
    assert audio.speed_scale == speed
    assert audio.metadata()["speed_mode"] == "manual"
    assert "timing_error_fraction" not in audio.metadata()


def test_article_reports_real_speed_without_recalibrating_or_stretching_audio(engine):
    engine.calibration_factor = 1.2
    question = Question("あか、さた。なか", (), 1, {"mode": "article"})
    audio = render_audio(question, AudioSettings(1, 0.5, 1), engine.client, engine.voice)
    assert audio.speech_seconds == pytest.approx(0.864, abs=0.001)
    assert audio.duration == pytest.approx(2.364, abs=0.001)
    assert audio.pause_seconds == 1.5
    assert audio.overall_cpm == pytest.approx(6 * 60 / 2.364)
    syntheses = [r for r in engine.requests if r[1] == "/synthesis"]
    assert len(syntheses) == 3  # Three distinct chunks, one synthesis each.
    assert all(r[2]["body"]["speedScale"] == 1.0 for r in syntheses)


def test_long_exercises_fail_before_synthesis(engine):
    question = Question("あ。か", (), 1, {"mode": "article"})
    with pytest.raises(ValueError, match="30分"):
        render_audio(question, AudioSettings(1, 0, 30, pause_cpm=1), engine.client, engine.voice)
    assert not engine.requests
    question = Question("あ" * 8000, (), 1, {"mode": "article"})
    with pytest.raises(ValueError, match="30分"):
        render_audio(question, AudioSettings(0.5, 0, 0), engine.client, engine.voice)
    assert not any(r[1] == "/synthesis" for r in engine.requests)


def test_actual_duration_limit_is_checked_before_allocating_pause_audio(engine):
    engine.calibration_factor = 2
    question = Question("あ。あ", (), 1, {"mode": "article"})
    with pytest.raises(VoicevoxError, match="30分"):
        render_audio(
            question, AudioSettings(2, 0, 29.9975, pause_cpm=1), engine.client, engine.voice
        )


def test_cancel_is_checked_between_api_requests(engine):
    q = generate_question(QuestionOptions(count=2, seed=1))
    cancel = threading.Event()

    def progress(_fraction, message):
        if "読みを準備" in message:
            cancel.set()

    with pytest.raises(Cancelled):
        render_audio(
            q, AudioSettings(), engine.client, engine.voice, cancel=cancel, progress=progress
        )
    assert not any(r[1] == "/synthesis" for r in engine.requests)


def test_chunking_preserves_countable_text_and_no_final_pause():
    q = Question("あ" * 400 + "、か。", (), 1, {"mode": "article"})
    units = speech_units(q, AudioSettings())
    assert [u.pause_characters for u in units] == [0, 0, 0.5, 0]
    assert sum(len(u.text.rstrip("、。")) for u in units) == 401


def test_wav_round_trip_and_truncated_data():
    samples = np.array([-32768, -1, 0, 1, 32767], dtype=np.int16)
    encoded = encode_wav(samples, 24000)
    decoded, rate = decode_wav(encoded)
    assert np.array_equal(decoded, samples)
    assert rate == 24000
    with pytest.raises(VoicevoxError, match="途中で切れ"):
        decode_wav(encoded[:-2])


@pytest.mark.parametrize(
    "settings",
    [
        AudioSettings(0),
        AudioSettings(0.19158259995522037),
        AudioSettings(0.49),
        AudioSettings(2.01),
        AudioSettings(float("nan")),
        AudioSettings(pause_cpm=0),
        AudioSettings(pause_cpm=float("inf")),
        AudioSettings(sentence_pause=-1),
        replace(AudioSettings(), comma_pause=float("inf")),
    ],
)
def test_invalid_audio_settings(settings):
    with pytest.raises(ValueError):
        settings.validate()
