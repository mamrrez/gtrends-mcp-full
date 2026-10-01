"""Does a query contain a term — as whole words, ignoring case.

"ai" must match «ai news» and not «rain». A term matches when its words
appear in the query, in order and next to each other, as whole words. The
comparison is literal apart from letter case: two spellings of a word are two
different words.
"""

from __future__ import annotations

import re
import unicodedata

_WORD = re.compile(r"\w+")


def words(text: str) -> list[str]:
    """The words of ``text``, lower-cased."""
    return _WORD.findall(unicodedata.normalize("NFC", text).casefold())


def fold(text: str) -> str:
    """A key that is equal for two texts with the same words: case, punctuation and spacing do not count."""
    return " ".join(words(text))


class TermMatcher:
    """Match many terms against many queries; the terms are split into words once."""

    def __init__(self, terms: list[str]):
        self.terms: list[tuple[str, str]] = []
        for term in terms:
            key = fold(term)
            if key:
                self.terms.append((term, f" {key} "))

    def __bool__(self) -> bool:
        return bool(self.terms)

    def matching_terms(self, query: str) -> list[str]:
        """The terms that ``query`` contains."""
        key = fold(query)
        if not key:
            return []
        padded = f" {key} "
        return [term for term, phrase in self.terms if phrase in padded]

    def matches(self, query: str) -> bool:
        return bool(self.matching_terms(query))


def contains_term(query: str, term: str) -> bool:
    return TermMatcher([term]).matches(query)
