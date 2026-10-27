from dataclasses import replace

import pytest

from shorthand_trainer.questions import (
    QuestionOptions,
    character_count,
    generate_question,
    read_text,
)


def test_word_filter_is_search_and_anchors_control_full_match(tmp_path):
    path = tmp_path / "日本語 の 単語.txt"
    path.write_text("\ufeffあか\nさあかた\nあか\n\n  かさ  \n。\n", encoding="utf-8")
    opts = QuestionOptions(mode="words", path=str(path), regex="あか", count=2, order="sequential")
    assert generate_question(opts).words == ("あか", "さあかた")
    with pytest.raises(ValueError, match="最大1個"):
        generate_question(replace(opts, regex="^あか$"))
    assert generate_question(replace(opts, count=1, regex="^あか$")).words == ("あか",)


def test_cp932_and_sequential_wrap_and_no_silent_replacement(tmp_path):
    path = tmp_path / "words.txt"
    path.write_bytes("さかな\nかさ\n".encode("cp932"))
    opts = QuestionOptions(
        mode="words", path=str(path), encoding="cp932", count=5, duplicates=True, order="sequential"
    )
    assert generate_question(opts).words == ("さかな", "かさ", "さかな", "かさ", "さかな")
    with pytest.raises(ValueError, match="文字コード"):
        read_text(path, "utf-8-sig")


def test_shuffle_is_reproducible_and_unique(tmp_path):
    path = tmp_path / "words.txt"
    path.write_text("\n".join(f"あ{'か' * i}" for i in range(10)), encoding="utf-8")
    opts = QuestionOptions(mode="words", path=str(path), count=10, seed=300)
    first = generate_question(opts)
    assert first == generate_question(opts)
    assert len(set(first.words)) == 10
    assert first.options["seed"] == 300


def test_article_chooses_only_direct_txt_and_logs_selected_source(tmp_path):
    (tmp_path / "見本.TXT").write_text(" あさ、です。\n", encoding="utf-8")
    (tmp_path / "ignore.csv").write_text("対象外", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "nested.txt").write_text("対象外", encoding="utf-8")
    question = generate_question(QuestionOptions(mode="article", path=str(tmp_path)))
    assert question.text == "あさ、です。"
    assert question.source_file == str((tmp_path / "見本.TXT").resolve())
    assert not question.is_words


@pytest.mark.parametrize("regex", ["[", "(?invalid)"])
def test_invalid_filter_is_a_user_error(tmp_path, regex):
    path = tmp_path / "words.txt"
    path.write_text("あさ", encoding="utf-8")
    with pytest.raises(ValueError, match="正規表現"):
        generate_question(QuestionOptions(mode="words", path=str(path), regex=regex, count=1))


def test_count_policy_includes_small_kana_but_not_punctuation_or_spaces():
    assert character_count("「きゃく」、１２３。\n あ") == 7
