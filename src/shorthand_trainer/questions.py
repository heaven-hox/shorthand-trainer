from __future__ import annotations

import random
import re
import secrets
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path

from .patterns import KanaPattern


def character_count(text: str) -> int:
    """原文の Unicode 文字数。空白・Unicode 句読記号は除外、小書きかなは1字。"""
    return sum(not c.isspace() and not unicodedata.category(c).startswith("P") for c in text)


def read_text(path: Path, encoding: str) -> str:
    if encoding not in ("utf-8-sig", "cp932"):
        raise ValueError("文字コードは UTF-8（BOM可）または CP932 を指定してください。")
    if path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError("TXTファイルは8 MiB以内にしてください。")
    try:
        return path.read_text(encoding=encoding)
    except UnicodeDecodeError as exc:
        raise ValueError("ファイルを読めません。文字コードの選択を確認してください。") from exc


@dataclass(frozen=True)
class QuestionOptions:
    mode: str = "pattern"
    pattern: str = "[あかさたな]ん?[あかさたな]"
    path: str = ""
    encoding: str = "utf-8-sig"
    regex: str = ""
    count: int = 20
    duplicates: bool = False
    order: str = "shuffle"
    seed: int | None = None


@dataclass(frozen=True)
class Question:
    text: str
    words: tuple[str, ...]
    seed: int
    options: dict
    source_file: str | None = None

    @property
    def is_words(self):
        return self.options["mode"] != "article"

    @property
    def characters(self):
        return character_count(self.text)


def generate_question(options: QuestionOptions) -> Question:
    seed = options.seed if options.seed is not None else secrets.randbits(64)
    rng = random.Random(seed)
    source = None
    if options.mode == "article":
        directory = Path(options.path).expanduser()
        files = sorted(p for p in directory.iterdir() if p.is_file() and p.suffix.lower() == ".txt")
        if not files:
            raise ValueError("選択したディレクトリ直下に TXT ファイルがありません。")
        source = str(rng.choice(files).resolve())
        text = read_text(Path(source), options.encoding).strip()
        words = ()
    else:
        if not 1 <= options.count <= 10000:
            raise ValueError("出題数は1〜10000個にしてください。")
        if options.mode == "pattern":
            words = KanaPattern(options.pattern).sample(options.count, options.duplicates, rng)
        elif options.mode == "words":
            source = str(Path(options.path).expanduser().resolve())
            candidates = [
                line.strip() for line in read_text(Path(source), options.encoding).splitlines()
            ]
            try:
                regex = re.compile(options.regex)
            except re.error as exc:
                raise ValueError(f"フィルタの正規表現が不正です: {exc}") from exc
            candidates = [w for w in candidates if character_count(w) and regex.search(w)]
            if not options.duplicates:
                candidates = list(dict.fromkeys(candidates))
            if not candidates:
                raise ValueError(
                    "条件に合う単語がありません。ファイルとフィルタを確認してください。"
                )
            if not options.duplicates and options.count > len(candidates):
                raise ValueError(
                    f"重複なしでは最大{len(candidates)}個です（指定: {options.count}個）。"
                )
            if options.order == "sequential":
                words = tuple(candidates[i % len(candidates)] for i in range(options.count))
            elif options.order == "shuffle":
                words = tuple(
                    rng.choices(candidates, k=options.count)
                    if options.duplicates
                    else rng.sample(candidates, options.count)
                )
            else:
                raise ValueError("出題順の指定が不正です。")
        else:
            raise ValueError("出題モードが不正です。")
        text = "\n".join(words)
    if not character_count(text):
        raise ValueError("問題文に読み上げる文字がありません。")
    return Question(text, words, seed, asdict(options) | {"seed": seed}, source)
