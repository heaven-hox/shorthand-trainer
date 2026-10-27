import sys
from types import SimpleNamespace

import numpy as np
import pytest

from shorthand_trainer.playback import AudioPlayer


def test_audio_callback_pauses_without_consuming_samples_and_stop_suppresses_finish(monkeypatch):
    class CallbackStop(Exception):
        pass

    class Stream:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)
            self.closed = False

        def start(self):
            pass

        def abort(self):
            self.finished_callback()

        def close(self):
            self.closed = True

    sd = SimpleNamespace(OutputStream=Stream, CallbackStop=CallbackStop, CallbackAbort=RuntimeError)
    monkeypatch.setitem(sys.modules, "sounddevice", sd)
    finished, errors = [], []
    player = AudioPlayer(
        np.arange(6, dtype=np.int16), 24000, None, lambda: finished.append(True), errors.append
    )
    player.start()
    stream = player.stream
    block = np.empty((4, 1), dtype=np.int16)
    stream.callback(block, 4, None, None)
    assert list(block[:, 0]) == [0, 1, 2, 3]
    player.paused = True
    stream.callback(block, 4, None, None)
    assert not block.any()
    assert player.position == 4
    player.paused = False
    with pytest.raises(CallbackStop):
        stream.callback(block, 4, None, None)
    assert list(block[:, 0]) == [4, 5, 0, 0]
    stream.finished_callback()
    assert finished == [True]
    player.stop()
    assert stream.closed
    assert finished == [True]
    assert not errors
