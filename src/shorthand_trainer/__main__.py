from __future__ import annotations

import argparse
import sys

from . import __version__


def main():
    parser = argparse.ArgumentParser(description="方式に依存しない日本語速記の練習アプリ")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument(
        "--check-voicevox", action="store_true", help="エンジン接続と話者一覧を確認"
    )
    parser.add_argument("--engine-url", default="http://127.0.0.1:50021")
    parser.add_argument("--list-devices", action="store_true", help="音声出力デバイスを一覧表示")
    args = parser.parse_args()
    try:
        if args.check_voicevox:
            from .voicevox import VoicevoxClient

            client = VoicevoxClient(args.engine_url, timeout=10)
            print(f"VOICEVOX ENGINE {client.version()}")
            for voice in client.voices():
                print(voice.label)
        elif args.list_devices:
            from .playback import output_devices

            devices = output_devices()
            if not devices:
                print("音声出力デバイスがありません。", file=sys.stderr)
                return 1
            for index, name in devices:
                print(f"{index}: {name}")
        else:
            from .gui import run

            run()
    except (ImportError, OSError, RuntimeError, ValueError) as exc:
        print(f"起動できません: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
