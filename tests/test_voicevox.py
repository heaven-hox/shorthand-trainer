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


def test_word_speed_and_intervals_are_independent_and_queries_are_cached(engine):
    q = generate_question(QuestionOptions(pattern="あか", count=3, duplicates=True, seed=1))
    audio = render_audio(q, AudioSettings(120, 0.5, 2), engine.client, engine.voice)
    assert audio.speech_seconds == pytest.approx(3, abs=0.01)
    assert audio.pause_seconds == 2
    assert audio.duration == pytest.approx(5, abs=0.01)
    assert audio.spoken_cpm == pytest.approx(120, rel=0.01)
    assert audio.overall_cpm == pytest.approx(72, rel=0.01)
    # Three spoken words, two one-second silent intervals, no trailing silence.
    assert np.all(audio.samples[:24000] == 1000)
    assert np.all(audio.samples[24000:48000] == 0)
    assert audio.samples[-1] == 1000
    requests = [r for r in engine.requests if r[0] == "POST"]
    assert len([r for r in requests if r[1] == "/audio_query"]) == 1
    assert len([r for r in requests if r[1] == "/synthesis"]) == 1
    synthesis = next(r[2] for r in requests if r[1] == "/synthesis")
    assert synthesis["params"]["speaker"] == ["7"]
    assert synthesis["body"]["accent_phrases"][0]["pause_mora"] is None
    assert synthesis["body"]["prePhonemeLength"] == 0


def test_article_targets_overall_speed_and_adjusts_measured_audio(engine):
    engine.calibration_factor = 1.2
    question = Question("あか、さた。なか", (), 1, {"mode": "article"})
    audio = render_audio(question, AudioSettings(60, 0.5, 1), engine.client, engine.voice)
    assert audio.duration == pytest.approx(6, abs=0.01)
    assert audio.pause_seconds == 1.5
    assert audio.overall_cpm == pytest.approx(60, rel=0.01)
    assert abs(audio.timing_error) < 0.01
    syntheses = [r for r in engine.requests if r[1] == "/synthesis"]
    assert len(syntheses) == 6  # Three distinct chunks, calibrated twice.


def test_excessive_article_pauses_and_long_exercises_fail_before_network(engine):
    question = Question("あ。か", (), 1, {"mode": "article"})
    with pytest.raises(ValueError, match="休止だけで"):
        render_audio(question, AudioSettings(60, 0, 3), engine.client, engine.voice)
    question = Question("あ" * 1801, (), 1, {"mode": "article"})
    with pytest.raises(ValueError, match="30分"):
        render_audio(question, AudioSettings(60, 0, 0), engine.client, engine.voice)
    assert not engine.requests


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
        AudioSettings(float("nan")),
        AudioSettings(sentence_pause=-1),
        replace(AudioSettings(), comma_pause=float("inf")),
    ],
)
def test_invalid_audio_settings(settings):
    with pytest.raises(ValueError):
        settings.validate()
