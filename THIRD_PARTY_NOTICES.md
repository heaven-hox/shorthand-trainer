# Third-party software and voice resources

This repository distributes the trainer's source and its own small example texts.
It does not bundle VOICEVOX, voice models, external dictionaries, or generated speech.
The trainer's MIT license does not replace third-party licenses or voice terms.

Main runtime dependencies (exact Python package versions are in `uv.lock`):

| Component | License / terms | Upstream |
| --- | --- | --- |
| Python | PSF and included component licenses | https://docs.python.org/3/license.html |
| Tcl/Tk, used through Tkinter | Tcl/Tk license terms | https://www.tcl-lang.org/software/tcltk/license.html |
| NumPy | BSD-3-Clause; binary distributions include additional notices | https://github.com/numpy/numpy/blob/main/LICENSE.txt |
| sounddevice | MIT | https://github.com/spatialaudio/python-sounddevice/blob/master/LICENSE |
| PortAudio, used by sounddevice | MIT-style license | https://www.portaudio.com/license.html |
| CFFI | MIT | https://cffi.readthedocs.io/en/latest/installation.html#license |
| pycparser, where required by CFFI | BSD-3-Clause | https://github.com/eliben/pycparser/blob/main/LICENSE |

Preserve the license files included in installed distributions. In particular,
NumPy and sounddevice wheels may contain native libraries with their own notices.
If executable bundles are added later, collect notices for the actual bundled
artifacts rather than assuming this list covers every component.

External speech service:

- [VOICEVOX ENGINE](https://github.com/VOICEVOX/voicevox_engine#ライセンス):
  LGPL v3 / alternative license. This application communicates with a separately
  installed engine over HTTP.
- [VOICEVOX CORE](https://github.com/VOICEVOX/voicevox_core#ライセンス):
  current source and build artifacts use MIT; pre-0.16 artifacts have different terms.
- [VOICEVOX voice models and individual voice terms](https://github.com/VOICEVOX/voicevox_vvm#readme):
  separate terms apply to models and generated audio. Select a voice whose terms
  permit the intended use, and set its required credit in the application.
  Credits are saved with each session; some voices require additional conditions.

pyopenjtalk is not used by this version. Its MIT package license would not by itself
cover its voice models, dictionaries, and other native components.
