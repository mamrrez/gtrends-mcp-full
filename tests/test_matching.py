"""Whole-word, case-insensitive matching of terms against queries."""

import pytest

from gtrends_mcp_full.matching import TermMatcher, contains_term, fold, words


@pytest.mark.parametrize(
    "query, term, expected",
    [
        ("ai news today", "ai", True),
        ("AI News Today", "ai", True),  # case does not count
        ("rain tomorrow", "ai", False),  # not inside another word
        ("pineapple recipe", "apple", False),
        ("Apple event 2026", "apple", True),
        ("real madrid vs barcelona", "real madrid", True),
        ("madrid real estate", "real madrid", False),  # a phrase, in order — not a bag of words
        ("real-madrid: live", "real madrid", True),  # punctuation between words does not count
        ("café menu", "cafe", False),  # literal: another spelling is another word
        ("iphone 18 price", "iphone 18", True),
        ("iphone 180", "iphone 18", False),
    ],
)
def test_contains_term(query, term, expected):
    assert contains_term(query, term) is expected


def test_term_matcher_reports_which_terms_matched():
    m = TermMatcher(["apple", "ai", "  ", "!!!"])
    assert len(m.terms) == 2  # terms without a single word are dropped
    assert m.matching_terms("Apple AI chip") == ["apple", "ai"]
    assert m.matching_terms("pineapple") == [] and not m.matches("") and not m.matches("?!")
    assert not TermMatcher(["!!!"])


def test_fold_ignores_case_punctuation_and_spacing_only():
    assert fold("Champions  League!") == fold("champions league") == "champions league"
    assert fold("Common  Query") == "common query"
    assert fold("café") != fold("cafe")
    assert words("e-mail, now") == ["e", "mail", "now"]
