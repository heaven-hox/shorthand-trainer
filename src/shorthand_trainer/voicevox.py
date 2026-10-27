from __future__ import annotations

import copy
import io
import json
import math
import re
import threading
import wave
from collections import Counter
from dataclasses import asdict, dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import ProxyHandler, Request, build_opener

import numpy as np

from .questions import Question, character_count


class VoicevoxError(RuntimeError):
    pass


class Cancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class Voice:
    speaker_id: int
    name: str
    style: str
    uuid: str
    credit_text: str | None = None

    @property
    def label(self):
        return f"{self.name} / {self.style} (ID: {self.speaker_id})"

    @property
    def credit(self):
        return self.credit_text or f"VOICEVOX:{self.name}"


class VoicevoxClient:
    def __init__(self, url: str = "http://127.0.0.1:50021", timeout: float = 60):
        parts = urlsplit(url.strip())
        if (
            parts.scheme not in ("http", "https")
            or not parts.hostname
            or parts.username
            or parts.password
            or parts.query
            or parts.fragment
            or parts.path not in ("", "/")
        ):
            raise ValueError("エンジンURLは http(s)://ホスト:ポート の形式にしてください。")
        self.url = url.strip().rstrip("/")
        self.timeout = timeout
        # Local synthesis must not be routed through corporate / cloud HTTP proxies.
        self.opener = (
            build_opener(ProxyHandler({}))
            if parts.hostname in ("127.0.0.1", "localhost", "::1")
            else build_opener()
        )

    def _request(self, path, *, params=None, body=None, post=False, binary=False):
        url = self.url + path + ("?" + urlencode(params) if params else "")
        data = json.dumps(body).encode("utf-8") if body is not None else (b"" if post else None)
        req = Request(url, data=data, headers={"Content-Type": "application/json"})
        try:
            with self.opener.open(req, timeout=self.timeout) as response:
                payload = response.read(100 * 1024 * 1024 + 1)
            if len(payload) > 100 * 1024 * 1024:
                raise VoicevoxError("エンジンの応答が大きすぎます。問題を短くしてください。")
            return payload if binary else json.loads(payload)
        except HTTPError as exc:
            raise VoicevoxError(
                f"VOICEVOX API {path} が HTTP {exc.code} を返しました。"
                "声の選択・問題文・エンジンの状態を確認してください。"
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise VoicevoxError(
                f"VOICEVOX に接続できません ({self.url})。"
                "エンジンを起動し、URLとポートを確認してください。"
            ) from exc
        except (ValueError, UnicodeError) as exc:
            raise VoicevoxError("エンジンから正しい JSON 応答を取得できませんでした。") from exc

    def voices(self) -> list[Voice]:
        speakers = self._request("/speakers")
        try:
            voices = [
                Voice(int(style["id"]), speaker["name"], style["name"], speaker["speaker_uuid"])
                for speaker in speakers
                for style in speaker["styles"]
                if style.get("type", "talk") in ("talk", "streaming_talk")
            ]
        except (KeyError, TypeError, ValueError) as exc:
            raise VoicevoxError("エンジンの話者一覧を読み取れませんでした。") from exc
        if not voices:
            raise VoicevoxError("読み上げに使える声が見つかりません。")
        return voices

    def version(self) -> str:
        return str(self._request("/version"))

    def query(self, text: str, speaker_id: int) -> dict:
        result = self._request(
            "/audio_query", params={"text": text, "speaker": speaker_id}, post=True
        )
        if not isinstance(result, dict) or not result.get("accent_phrases"):
            raise VoicevoxError(f"問題の一部を音声に変換できませんでした: {text[:40]!r}")
        return result

    def synthesize(self, query: dict, speaker_id: int) -> bytes:
        return self._request(
            "/synthesis", params={"speaker": speaker_id}, body=query, post=True, binary=True
        )


@dataclass(frozen=True)
class AudioSettings:
    cpm: float = 60
    comma_pause: float = 0.5
    sentence_pause: float = 2.0

    def validate(self):
        if not math.isfinite(self.cpm) or not 1 <= self.cpm <= 600:
            raise ValueError("速度は1〜600字／分にしてください。")
        if any(
            not math.isfinite(p) or not 0 <= p <= 30
            for p in (self.comma_pause, self.sentence_pause)
        ):
            raise ValueError("休止は0〜30字分にしてください。")


@dataclass(frozen=True)
class SpeechUnit:
    text: str
    pause_characters: float


def speech_units(question: Question, settings: AudioSettings) -> list[SpeechUnit]:
    units = []
    for source in question.words if question.is_words else (question.text,):
        pieces = re.split(r"([、。！？\n]+)", source)
        for index in range(0, len(pieces), 2):
            text = pieces[index].strip()
            punctuation = pieces[index + 1] if index + 1 < len(pieces) else ""
            if not character_count(text):
                continue
            is_comma = punctuation and set(punctuation) == {"、"}
            pause = settings.comma_pause if is_comma else settings.sentence_pause
            terminator = punctuation.replace("\n", "。") or "。"
            # Bound audio_query URL sizes and CPU work per request.
            for offset in range(0, len(text), 180):
                chunk = text[offset : offset + 180]
                if character_count(chunk):
                    last = offset + 180 >= len(text)
                    units.append(
                        SpeechUnit(chunk + (terminator if last else ""), pause if last else 0)
                    )
    if not units:
        raise ValueError("読み上げる文字がありません。")
    # There is no exercise interval after the final word / sentence.
    units[-1] = SpeechUnit(units[-1].text, 0)
    return units


def decode_wav(data: bytes) -> tuple[np.ndarray, int]:
    try:
        with wave.open(io.BytesIO(data), "rb") as audio:
            if audio.getsampwidth() != 2 or audio.getcomptype() != "NONE":
                raise ValueError("16 bit PCM WAV が必要です。")
            channels, rate, frames = audio.getnchannels(), audio.getframerate(), audio.getnframes()
            if channels not in (1, 2) or rate <= 0 or frames == 0:
                raise ValueError("空または非対応の WAV です。")
            payload = audio.readframes(frames)
            if len(payload) != frames * channels * 2:
                raise ValueError("WAV のデータが途中で切れています。")
            samples = np.frombuffer(payload, dtype="<i2").reshape(-1, channels)
            if channels == 2:
                samples = samples.astype(np.int32).mean(axis=1).astype(np.int16)
            else:
                samples = samples[:, 0].copy()
        return samples, rate
    except (wave.Error, EOFError, ValueError) as exc:
        raise VoicevoxError(f"エンジンの音声を読み取れません: {exc}") from exc


def encode_wav(samples: np.ndarray, rate: int) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(samples.astype("<i2", copy=False).tobytes())
    return buffer.getvalue()


@dataclass
class RenderedAudio:
    samples: np.ndarray
    sample_rate: int
    speech_seconds: float
    pause_seconds: float
    characters: int
    speed_scale: float
    settings: AudioSettings
    voice: Voice
    engine_version: str
    engine_url: str
    timing_error: float

    @property
    def duration(self):
        return len(self.samples) / self.sample_rate

    @property
    def overall_cpm(self):
        return self.characters * 60 / self.duration

    @property
    def spoken_cpm(self):
        return self.characters * 60 / self.speech_seconds

    def metadata(self):
        return {
            "settings": asdict(self.settings),
            "voice": asdict(self.voice),
            "credit": self.voice.credit,
            "engine_version": self.engine_version,
            "engine_url": self.engine_url,
            "sample_rate": self.sample_rate,
            "duration_seconds": self.duration,
            "speech_seconds": self.speech_seconds,
            "pause_seconds": self.pause_seconds,
            "overall_cpm": self.overall_cpm,
            "spoken_cpm": self.spoken_cpm,
            "speed_scale": self.speed_scale,
            "timing_error_fraction": self.timing_error,
        }


def render_audio(
    question: Question,
    settings: AudioSettings,
    client: VoicevoxClient,
    voice: Voice,
    *,
    cancel: threading.Event | None = None,
    progress=None,
) -> RenderedAudio:
    settings.validate()
    cancel = cancel or threading.Event()

    def update(fraction, message):
        if cancel.is_set():
            raise Cancelled("音声生成をキャンセルしました。")
        if progress:
            progress(fraction, message)

    units = speech_units(question, settings)
    pause_seconds = sum(u.pause_characters for u in units) * 60 / settings.cpm
    target = question.characters * 60 / settings.cpm
    speech_target = target if question.is_words else target - pause_seconds
    duration_target = speech_target + pause_seconds
    if speech_target <= 0:
        raise ValueError("休止だけで目標時間を超えます。句読点の休止を短くしてください。")
    if duration_target > 30 * 60:
        raise ValueError("音声の上限は30分です。出題数を減らすか、速度を上げてください。")
    update(0, "エンジンを確認しています…")
    version = client.version()
    frequencies = Counter(u.text for u in units)
    queries = {}
    predicted_seconds = 0.0
    for i, (text, count) in enumerate(frequencies.items()):
        update(
            0.05 + 0.2 * i / len(frequencies), f"読みを準備しています ({i + 1}/{len(frequencies)})"
        )
        query = copy.deepcopy(client.query(text, voice.speaker_id))
        query.update(
            prePhonemeLength=0,
            postPhonemeLength=0,
            outputSamplingRate=24000,
            outputStereo=False,
            speedScale=1,
        )
        predicted = 0.0
        try:
            for phrase in query["accent_phrases"]:
                phrase["pause_mora"] = None
                for mora in phrase["moras"]:
                    predicted += (mora.get("consonant_length") or 0) + mora["vowel_length"]
        except (KeyError, TypeError) as exc:
            raise VoicevoxError("エンジンの読み上げ時間を取得できませんでした。") from exc
        if not math.isfinite(predicted) or predicted <= 0:
            raise VoicevoxError(f"音声の読みを作れませんでした: {text[:40]!r}")
        predicted_seconds += predicted * count
        queries[text] = query
    speed = min(10.0, max(0.1, predicted_seconds / speech_target))
    waves = {}
    rate = 24000
    speech_seconds = 0.0
    for attempt in range(3):
        for i, (text, query) in enumerate(queries.items()):
            update(
                0.25 + 0.7 * (attempt + i / len(queries)) / 3,
                f"音声を生成しています ({i + 1}/{len(queries)}、調整{attempt + 1}/3)",
            )
            query["speedScale"] = speed
            samples, actual_rate = decode_wav(client.synthesize(query, voice.speaker_id))
            if actual_rate != rate:
                raise VoicevoxError("エンジンが指定と異なるサンプルレートを返しました。")
            waves[text] = samples
        speech_seconds = sum(len(waves[t]) * n for t, n in frequencies.items()) / rate
        error = (speech_seconds - speech_target) / speech_target
        if abs(error) <= 0.03:
            break
        adjusted = min(10.0, max(0.1, speed * speech_seconds / speech_target))
        if attempt == 2 or abs(adjusted - speed) < 0.0001:
            break
        speed = adjusted
    update(0.96, "休止を挿入しています…")
    parts = []
    pause_frames = 0
    for unit in units:
        parts.append(waves[unit.text])
        frames = round(unit.pause_characters * 60 / settings.cpm * rate)
        if frames:
            parts.append(np.zeros(frames, dtype=np.int16))
            pause_frames += frames
    samples = np.concatenate(parts)
    if len(samples) / rate > 31 * 60:
        raise VoicevoxError("生成音声が長すぎます。問題を短くするか、速度を上げてください。")
    error = (
        (speech_seconds - speech_target) / speech_target
        if question.is_words
        else (len(samples) / rate - duration_target) / duration_target
    )
    update(1, "音声を準備しました。")
    return RenderedAudio(
        samples,
        rate,
        speech_seconds,
        pause_frames / rate,
        question.characters,
        speed,
        settings,
        voice,
        version,
        client.url,
        error,
    )
