"""Conservative, bounded literal fallback; no spelling or semantic substitution."""
from __future__ import annotations

import re
from difflib import SequenceMatcher

from chinalaw.search_tokens import index_tokens, normalize


def fragments(query: str) -> list[str]:
    # Do not silently truncate a query and match only its prefix.
    if len(query) > 80:
        return []
    return [part for part in re.split(
        r"[\s，。、；：！？（）《》〈〉【】〔〕「」『』“”‘’,;:!?()\[\]\"]+",
        normalize(query),
    ) if part]


def recall_expression(parts: list[str]) -> str | None:
    tokens = list(dict.fromkeys(index_tokens(" ".join(parts)).split()))
    if not tokens or len(tokens) > 80:
        return None
    return "text : (" + " OR ".join(f'"{token}"' for token in tokens) + ")"


def matching_fragments(parts: list[str], text: str) -> list[str] | None:
    """Cover every character with literal pieces of >=2 characters in one text.

    Dynamic programming avoids a greedy long prefix hiding a valid split.
    Single-character query parts must also occur, but are never split further.
    """
    text = normalize(text)
    matched = []
    for part in parts:
        if part in text:
            matched.append(part)
            continue
        # Literal recall must not manufacture a different number: 191 cannot
        # be covered by 19 + 1, nor 第三百零六条 by fragments of 第两百零六条.
        protected = {i for i in range(1, len(part))
                     if part[i - 1].isdigit() and part[i].isdigit()}
        for reference in re.finditer(
            r"第[0-9〇零一二三四五六七八九十百千万两]+条"
            r"(?:之[0-9〇零一二三四五六七八九十百千万两]+)?", part,
        ):
            protected.update(range(reference.start() + 1, reference.end()))
        paths: dict[int, list[str]] = {len(part): []}
        for start in range(len(part) - 2, -1, -1):
            if start in protected:
                continue
            for end in range(len(part), start + 1, -1):
                if end in protected:
                    continue
                if end in paths and part[start:end] in text:
                    paths[start] = [part[start:end], *paths[end]]
                    break
        if 0 not in paths:
            return None
        pieces = paths[0]
        if len(part) <= 4 and not any(
            all(piece in text[start:start + len(part) + 2] for piece in pieces)
            for start in range(len(text)) if text.startswith(pieces[0], start)
        ):
            return None
        matched.extend(pieces)
    return matched if len(matched) >= 2 else None


def name_score(query: str, name: str) -> float:
    def clean(value: str) -> str:
        value = normalize(value).replace("司法解释", "解释")
        value = re.sub(r"中华人民共和国|最高人民法院|最高人民检察院|若干问题|关于|适用", "", value)
        return re.sub(r"[\W_]|的|(?:19|20)\d{2}", "", value)

    query, name = clean(query), clean(name)
    if len(query) < 2 or not name:
        return 0.0
    matcher = SequenceMatcher(None, query, name, autojunk=False)
    common = sum(block.size for block in matcher.get_matching_blocks())
    coverage = 0.7 * common / len(query) + 0.3 * common / len(name)
    contiguous = matcher.find_longest_match().size / len(query)
    return 0.85 * coverage + 0.15 * contiguous
