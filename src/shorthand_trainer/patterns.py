"""有限のかなパターンを DFA にして、全列挙せず一意な文字列を抽選する。"""

from __future__ import annotations

import random
from collections import deque
from functools import cache


class PatternError(ValueError):
    pass


def _kana(char: str) -> bool:
    return "ぁ" <= char <= "ゖ" or char == "ー"


class _Parser:
    def __init__(self, pattern: str):
        if len(pattern) > 256:
            raise PatternError("生成パターンは256文字以内にしてください。")
        self.pattern = pattern.removeprefix("^").removesuffix("$")
        self.pos = 0

    def parse(self):
        node = self._choice(0)
        if self.pos != len(self.pattern):
            raise PatternError(f"位置{self.pos + 1}: 対応していない記号です。")
        return node

    def _peek(self):
        return self.pattern[self.pos : self.pos + 1]

    def _choice(self, depth):
        if depth > 16:
            raise PatternError("括弧の入れ子が深すぎます（最大16段）。")
        nodes = [self._sequence(depth)]
        while self._peek() == "|":
            self.pos += 1
            nodes.append(self._sequence(depth))
        return ("choice", nodes)

    def _sequence(self, depth):
        nodes = []
        while self._peek() and self._peek() not in ")|":
            nodes.append(self._atom(depth))
        return ("sequence", nodes)

    def _atom(self, depth):
        char = self._peek()
        self.pos += 1
        if char == "(":
            node = self._choice(depth + 1)
            if self._peek() != ")":
                raise PatternError("閉じ括弧 ')' がありません。")
            self.pos += 1
        elif char == "[":
            chars = set()
            while self._peek() and self._peek() != "]":
                first = self._peek()
                self.pos += 1
                if not _kana(first):
                    raise PatternError("文字クラスにはひらがな・長音符を指定してください。")
                if self._peek() == "-":
                    self.pos += 1
                    last = self._peek()
                    self.pos += 1
                    if not last or not _kana(last) or ord(first) > ord(last):
                        raise PatternError("文字範囲の指定が不正です。")
                    ranged = {chr(i) for i in range(ord(first), ord(last) + 1)}
                    if not all(_kana(c) for c in ranged):
                        raise PatternError("ひらがな以外を含む文字範囲は指定できません。")
                    chars.update(ranged)
                else:
                    chars.add(first)
            if self._peek() != "]" or not chars:
                raise PatternError("文字クラスが空か、閉じ括弧 ']' がありません。")
            self.pos += 1
            node = ("chars", chars)
        elif _kana(char):
            node = ("chars", {char})
        else:
            raise PatternError(
                f"位置{self.pos}: '{char}' は生成用の構文ではありません。"
                "ひらがな、[]、()、|、?、{n,m} が使えます。*・+ は非対応です。"
            )
        if self._peek() == "?":
            self.pos += 1
            node = ("repeat", node, 0, 1)
        elif self._peek() == "{":
            end = self.pattern.find("}", self.pos)
            if end < 0:
                raise PatternError("回数指定の閉じ括弧 '}' がありません。")
            parts = self.pattern[self.pos + 1 : end].split(",")
            if len(parts) not in (1, 2) or not all(p.isascii() and p.isdigit() for p in parts):
                raise PatternError("回数は {n} または {n,m} の形式で指定してください。")
            low, high = int(parts[0]), int(parts[-1])
            if not 0 <= low <= high <= 32:
                raise PatternError("繰り返し回数は 0〜32 の範囲にしてください。")
            self.pos = end + 1
            node = ("repeat", node, low, high)
        return node


class KanaPattern:
    """曖昧な選択・省略による同一表記も統合して数える。空文字列は出題しない。"""

    def __init__(self, pattern: str):
        tree = _Parser(pattern).parse()
        self._edges: list[list[tuple[str | None, int]]] = []

        def state():
            if len(self._edges) >= 1024:
                raise PatternError("パターンが複雑すぎます。繰り返しや選択を減らしてください。")
            self._edges.append([])
            return len(self._edges) - 1

        def build(node):
            begin, end = state(), state()
            kind = node[0]
            if kind == "chars":
                self._edges[begin].extend((c, end) for c in sorted(node[1]))
            elif kind == "choice":
                for child in node[1]:
                    a, b = build(child)
                    self._edges[begin].append((None, a))
                    self._edges[b].append((None, end))
            elif kind == "sequence":
                prev = begin
                for child in node[1]:
                    a, b = build(child)
                    self._edges[prev].append((None, a))
                    prev = b
                self._edges[prev].append((None, end))
            else:
                _, child, low, high = node
                prev = begin
                for i in range(high):
                    if i >= low:
                        self._edges[prev].append((None, end))
                    a, b = build(child)
                    self._edges[prev].append((None, a))
                    prev = b
                self._edges[prev].append((None, end))
            return begin, end

        begin, end = build(tree)

        def closure(nodes):
            found = set(nodes)
            pending = list(nodes)
            while pending:
                for char, dest in self._edges[pending.pop()]:
                    if char is None and dest not in found:
                        found.add(dest)
                        pending.append(dest)
            return frozenset(found)

        initial = closure([begin])
        ids = {initial: 0}
        pending = deque([initial])
        self.transitions: list[dict[str, int]] = []
        self.accepts: list[bool] = []
        while pending:
            nodes = pending.popleft()
            self.accepts.append(end in nodes)
            destinations: dict[str, set[int]] = {}
            for node in nodes:
                for char, dest in self._edges[node]:
                    if char is not None:
                        destinations.setdefault(char, set()).add(dest)
            transitions = {}
            for char, dests in sorted(destinations.items()):
                target = closure(dests)
                if target not in ids:
                    if len(ids) >= 4096:
                        raise PatternError("パターンが複雑すぎます（状態数の上限）。")
                    ids[target] = len(ids)
                    pending.append(target)
                transitions[char] = ids[target]
            self.transitions.append(transitions)

        @cache
        def length(node):
            return max((1 + length(n) for n in self.transitions[node].values()), default=0)

        # Finite NFA paths have at most 1024 states, below Python's recursion limit
        # for accepted lengths; check depth iteratively before recursive counting.
        stack = [(0, 0)]
        depths = {0: 0}
        while stack:
            node, depth = stack.pop()
            if depth > 64:
                raise PatternError("生成する単語は最大64文字です。")
            for target in self.transitions[node].values():
                if depth + 1 > depths.get(target, -1):
                    depths[target] = depth + 1
                    stack.append((target, depth + 1))
        self.max_length = length(0)

        @cache
        def count(node):
            return int(self.accepts[node]) + sum(count(n) for n in self.transitions[node].values())

        self._count = count
        self.size = count(0) - int(self.accepts[0])
        if not self.size:
            raise PatternError("空でない単語を生成できるパターンにしてください。")

    def at(self, rank: int) -> str:
        if not 0 <= rank < self.size:
            raise IndexError(rank)
        rank += int(self.accepts[0])
        node, result = 0, []
        while True:
            if self.accepts[node]:
                if rank == 0:
                    return "".join(result)
                rank -= 1
            for char, target in self.transitions[node].items():
                count = self._count(target)
                if rank < count:
                    result.append(char)
                    node = target
                    break
                rank -= count

    def sample(self, count: int, duplicates: bool, rng: random.Random) -> tuple[str, ...]:
        if not 1 <= count <= 10000:
            raise PatternError("出題数は1〜10000個にしてください。")
        if not duplicates and count > self.size:
            raise PatternError(f"重複なしでは最大{self.size}個です（指定: {count}個）。")
        if duplicates:
            ranks = [rng.randrange(self.size) for _ in range(count)]
        else:
            # Floyd sampling works even when the language is larger than sys.maxsize.
            selected: set[int] = set()
            ranks = []
            for i in range(self.size - count, self.size):
                value = rng.randrange(i + 1)
                value = i if value in selected else value
                selected.add(value)
                ranks.append(value)
            rng.shuffle(ranks)
        return tuple(self.at(rank) for rank in ranks)
