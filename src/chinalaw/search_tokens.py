"""Bigram tokens for the article full-text index.

The article index stores Python-made tokens in a contentless FTS5 table with the
``unicode61`` tokenizer: every two consecutive Han characters form one token,
a lone Han character stays a token, and runs of digits or Latin letters are one
token each. Index and query share this module, so the rules cannot drift.

Exact search keeps its meaning — every query segment must occur verbatim in the
article or its law title. The index only narrows candidates; the caller checks
each segment as a substring before accepting a row. That check is what makes
the result identical to a substring scan: a bigram phrase can also match text
where the characters are split by punctuation, and a lone Han character next to
a digit (``第30条``) has no stable token in the text, so it is left to the check.
Digit and Latin runs are matched as token prefixes, and a run that opens the
segment is left to the check, since it may start inside a longer number.
"""

from __future__ import annotations

import re
import unicodedata

# Recorded in meta. Changing the rules below needs a schema migration that
# rebuilds the article index.
TOKENIZER_VERSION = "bigram-1"

_HAN = r"㐀-䶿一-鿿豈-﫿"
_RUNS = re.compile(rf"([{_HAN}]+)|([0-9a-z]+)")

LOCAL_LEVELS = frozenset({"local_regulation", "local_government_rule"})


def normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "").lower()


def tier_of(level: str | None) -> str:
    """Coarse level bucket stored in the index, so national hits come first cheaply."""
    return "local" if level in LOCAL_LEVELS else "national"


def _han_tokens(run: str) -> list[str]:
    if len(run) == 1:
        return [run]
    return [run[i : i + 2] for i in range(len(run) - 1)]


def index_tokens(text: str) -> str:
    """Space-separated tokens for one indexed column."""
    tokens: list[str] = []
    for han, word in _RUNS.findall(normalize(text)):
        tokens.extend(_han_tokens(han) if han else [word])
    return " ".join(tokens)


def segment_phrases(segment: str) -> list[str]:
    """FTS5 phrases that every text containing ``segment`` must match.

    One phrase per run, joined with AND by the caller. Han runs of one
    character are dropped: in running text that character is usually the half
    of a bigram, and the substring check covers it.
    """
    phrases = []
    normalized = normalize(segment)
    for match in _RUNS.finditer(normalized):
        han, word = match.groups()
        if han and len(han) == 1:
            continue
        if han:
            phrases.append('"' + " ".join(_han_tokens(han)) + '"')
        elif match.start() > 0:
            # A digit or Latin run may end inside a longer one (``30`` in
            # ``300``), so it matches as a prefix. A run opening the segment may
            # also start inside one (``5000`` in ``15000``) and cannot narrow.
            phrases.append(f'"{word}" *')
    return phrases


def is_exact_phrase(segment: str) -> bool:
    """True when the segment's phrase alone decides a verbatim match."""
    return (
        len(segment) >= 2
        and normalize(segment) == segment
        and _RUNS.fullmatch(segment) is not None
        and _RUNS.fullmatch(segment).group(1) is not None
    )


def match_expression(segments: list[str]) -> str | None:
    """AND of all segments' phrases, or ``None`` when the index cannot narrow."""
    phrases = [phrase for segment in segments for phrase in segment_phrases(segment)]
    if not phrases:
        return None
    return " AND ".join(dict.fromkeys(phrases))
