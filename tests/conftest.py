from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import numpy as np
import pytest

from shorthand_trainer.questions import character_count
from shorthand_trainer.voicevox import Voice, VoicevoxClient, encode_wav


def pytest_addoption(parser):
    parser.addoption("--run-gui", action="store_true", help="実際のTkウィンドウを使用するテスト")


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--run-gui"):
        for item in items:
            if "gui" in item.keywords:
                item.add_marker(pytest.mark.skip(reason="--run-gui とディスプレイが必要です"))


@pytest.fixture
def engine():
    """A protocol fixture, not a real synthesizer. PCM is constant to identify silence."""
    state = SimpleNamespace(
        requests=[], calibration_factor=1.0, empty_voices=False, voice_type="talk"
    )

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def respond(self, obj, code=200):
            data = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            state.requests.append(("GET", self.path, None))
            if self.path == "/version":
                self.respond("test-engine")
            elif self.path == "/speakers":
                self.respond(
                    []
                    if state.empty_voices
                    else [
                        {
                            "name": "テスト話者",
                            "speaker_uuid": "test-uuid",
                            "styles": [
                                {"name": "ノーマル", "id": 7, "type": state.voice_type},
                                {"name": "歌", "id": 8, "type": "sing"},
                            ],
                        }
                    ]
                )
            else:
                self.respond({}, 404)

        def do_POST(self):
            parts = urlsplit(self.path)
            params = parse_qs(parts.query)
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            body = json.loads(body) if body else None
            state.requests.append(("POST", parts.path, {"params": params, "body": body}))
            if parts.path == "/audio_query":
                text = params["text"][0]
                self.respond(
                    {
                        "accent_phrases": [
                            {
                                "moras": [
                                    {"text": c, "consonant_length": 0.04, "vowel_length": 0.08}
                                    for c in text
                                    if character_count(c)
                                ],
                                "pause_mora": {"vowel_length": 0.5},
                            }
                        ],
                        "speedScale": 1,
                        "prePhonemeLength": 0.1,
                        "postPhonemeLength": 0.1,
                    }
                )
            elif parts.path == "/synthesis":
                duration = (
                    sum(
                        (m.get("consonant_length") or 0) + m["vowel_length"]
                        for phrase in body["accent_phrases"]
                        for m in phrase["moras"]
                    )
                    / body["speedScale"]
                    * state.calibration_factor
                )
                frames = max(1, round(duration * 24000))
                self.respond(encode_wav(np.full(frames, 1000, dtype=np.int16), 24000))
            else:
                self.respond({}, 404)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state.url = f"http://127.0.0.1:{server.server_port}"
    state.client = VoicevoxClient(state.url, timeout=3)
    state.voice = Voice(7, "テスト話者", "ノーマル", "test-uuid")
    yield state
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)
