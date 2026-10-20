"""The commit guard must catch listed personal details without printing them."""

from privacy_check import find_terms, load_terms, mask


def test_terms_match_whole_words_case_insensitively():
    terms = ["Jane Citizen", "12345678", "ACME"]
    lines = ["From: JANE CITIZEN NA", "Account 12345678", "acmesoft is a different word", "ref 123456789"]
    assert find_terms(lines, terms) == ["Jane Citizen", "12345678"]


def test_terms_file_skips_comments_and_blank_lines(tmp_path):
    path = tmp_path / "private_terms.txt"
    path.write_text("# names\nJane\n\n  12-345 \n", encoding="utf-8")
    assert load_terms(path) == ["Jane", "12-345"]
    assert load_terms(tmp_path / "missing.txt") == []


def test_mask_never_prints_the_whole_term():
    assert mask("12345678") == "12…"
    assert mask("abc") == "…"
