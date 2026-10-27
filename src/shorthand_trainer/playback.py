from __future__ import annotations

import threading


def output_devices():
    import sounddevice as sd

    return [
        (index, dev["name"])
        for index, dev in enumerate(sd.query_devices())
        if dev["max_output_channels"] > 0
    ]


class AudioPlayer:
    def __init__(self, samples, rate, device, on_finished, on_error):
        self.samples, self.rate, self.device = samples, rate, device
        self.on_finished, self.on_error = on_finished, on_error
        self.position = 0
        self.paused = False
        self.stopped = threading.Event()
        self.stream = None

    def start(self):
        import sounddevice as sd

        def callback(outdata, frames, _time, status):
            try:
                outdata.fill(0)
                if self.stopped.is_set():
                    raise sd.CallbackStop
                if self.paused:
                    return
                count = min(frames, len(self.samples) - self.position)
                outdata[:count, 0] = self.samples[self.position : self.position + count]
                self.position += count
                if self.position >= len(self.samples):
                    raise sd.CallbackStop
            except sd.CallbackStop:
                raise
            except Exception as exc:
                self.stopped.set()
                self.on_error(str(exc))
                raise sd.CallbackAbort from exc

        def finished():
            if not self.stopped.is_set():
                self.on_finished()

        self.stream = sd.OutputStream(
            samplerate=self.rate,
            channels=1,
            dtype="int16",
            device=self.device,
            callback=callback,
            finished_callback=finished,
        )
        try:
            self.stream.start()
        except Exception:
            self.stream.close()
            self.stream = None
            raise

    def stop(self):
        self.stopped.set()
        if self.stream is not None:
            self.stream.abort()
            self.stream.close()
            self.stream = None
