import itertools
import random
import re

import pytest

from shorthand_trainer.patterns import KanaPattern, PatternError


@pytest.mark.parametrize(
    "pattern",
    [
        "[あか]ん?[あか]",
        "(あ|ああ)?あ?",
        "([あか]|あ){1,3}",
        "(あ?|か?){0,3}",
        "^[あ-お]{1,2}$",
        "(あ|あ)",
        "あ{0,0}か",
    ],
)
def test_language_matches_regex_without_duplicate_derivations(pattern):
    compiled = KanaPattern(pattern)
    alphabet = "".join(chr(i) for i in range(ord("あ"), ord("お") + 1)) + "かん"
    expected = {
        "".join(chars)
        for size in range(1, compiled.max_length + 1)
        for chars in itertools.product(alphabet, repeat=size)
        if re.fullmatch(pattern, "".join(chars))
    }
    actual = {compiled.at(i) for i in range(compiled.size)}
    assert actual == expected
    assert compiled.size == len(actual)


def test_can_sample_every_candidate_and_reject_impossible_request():
    pattern = KanaPattern("[あかさたな]ん?[あかさたな]")
    assert pattern.size == 50
    assert len(set(pattern.sample(50, False, random.Random(1)))) == 50
    with pytest.raises(PatternError, match="最大50個"):
        pattern.sample(51, False, random.Random(1))
    assert len(pattern.sample(51, True, random.Random(1))) == 51


def test_huge_language_does_not_need_enumeration_or_platform_sized_range():
    pattern = KanaPattern("[あかさたな]{32}")
    assert pattern.size == 5**32
    words = pattern.sample(100, False, random.Random(2))
    assert len(set(words)) == 100
    assert all(len(word) == 32 and re.fullmatch("[あかさたな]+", word) for word in words)
    assert words == pattern.sample(100, False, random.Random(2))


@pytest.mark.parametrize(
    "pattern",
    [
        "",
        "あ{0}",
        "[]",
        "[あ",
        "(あ",
        "あ)",
        "あ*",
        "あ+",
        "[^あ]",
        "(?=あ)",
        "あ{2,}",
        "あ{3,2}",
        "あ{33}",
        "[ん-あ]",
        "[ゖ-ー]",
        "あ{32}{2}",
        "あ{32}あ{32}あ",
        "(" * 18 + "あ" + ")" * 18,
    ],
)
def test_invalid_or_unbounded_patterns_fail_explicitly(pattern):
    with pytest.raises(PatternError):
        KanaPattern(pattern)
