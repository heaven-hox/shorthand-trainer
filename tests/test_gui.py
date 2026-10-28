import json
import time

import pytest

pytestmark = pytest.mark.gui


def test_gui_full_practice_with_http_engine_and_test_audio(engine, tmp_path, monkeypatch):
    import tkinter as tk

    from shorthand_trainer import gui

    class Player:
        def __init__(self, samples, rate, device, on_finished, on_error):
            self.samples, self.rate = samples, rate
            self.on_finished = on_finished
            self.position, self.paused = 0, False

        def start(self):
            pass

        def stop(self):
            pass

    errors = []
    monkeypatch.setattr(gui, "AudioPlayer", Player)
    monkeypatch.setattr(gui, "output_devices", lambda: [(4, "テスト出力")])
    monkeypatch.setattr(gui.messagebox, "showerror", lambda *_a, **_k: errors.append(_a))
    root = tk.Tk()
    root.withdraw()
    app = gui.Application(root)

    def until(predicate):
        deadline = time.monotonic() + 10
        while not predicate() and time.monotonic() < deadline:
            root.update()
            time.sleep(0.005)
        assert predicate(), (app.status.get(), errors)

    try:
        app.engine_url.set(engine.url)
        app.log_root.set(str(tmp_path / "日本語 の 記録"))
        app.count.set("3")
        app.seed.set("45")
        app.connect_button.invoke()
        until(lambda: not app.busy)
        assert app.voice_name.get() == engine.voice.label
        assert app.regenerate_button.instate(["disabled"])
        app.generate_button.invoke()
        until(lambda: not app.busy)
        assert app.session is not None
        assert app.session.state == "prepared"
        original_question = app.session.question
        original_directory = app.session.directory
        original_audio = app.session.audio
        assert "VOICEVOX話速 1倍" in app.summary.get()
        # Reuse exactly the same random question even if source settings are changed.
        app.pattern.set("not a valid pattern")
        app.seed.set("not an integer")
        app.speed_scale.set("0.5")
        app.regenerate_button.invoke()
        until(lambda: not app.busy)
        assert app.session.directory != original_directory
        assert app.session.question == original_question
        assert app.session.audio.speed_scale == 0.5
        assert app.session.audio.speech_seconds == pytest.approx(original_audio.speech_seconds * 2)
        assert app.session.audio.pause_seconds == original_audio.pause_seconds
        assert "VOICEVOX話速 0.5倍" in app.summary.get()
        previous = json.loads((original_directory / "session.json").read_text(encoding="utf-8"))
        assert previous["outcome"] == "aborted"
        assert "反訳終了後" in app.answer.get("1.0", "end")
        assert app.reveal_button.instate(["disabled"])
        app.play_button.invoke()
        assert app.session.state == "playing"
        assert app.regenerate_button.instate(["disabled"])
        app.pause_button.invoke()
        assert app.player.paused
        app.pause_button.invoke()
        assert not app.player.paused
        app.player.on_finished()
        until(lambda: app.session.state == "transcribing")
        assert "反訳終了後" in app.answer.get("1.0", "end")
        app.reveal_button.invoke()
        assert app.answer.get("1.0", "end").strip() == app.session.question.text
        assert app.session.state == "revealed"
        record = json.loads((app.session.directory / "session.json").read_text(encoding="utf-8"))
        assert record["outcome"] == "completed"
        assert record["playback_pauses"] == 1
        assert not errors
    finally:
        app.close()
