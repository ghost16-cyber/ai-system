from __future__ import annotations

import math
import re
from collections import Counter


_TOKENS = re.compile(r"[a-z0-9_]+")


def tokens(value: str) -> tuple[str, ...]:
    return tuple(_TOKENS.findall(value.casefold()))


def rank_bm25(
    query: str,
    documents: dict[str, str],
    *,
    limit: int,
) -> dict[str, float]:
    query_terms = tokens(query)
    if not query_terms or not documents:
        return {}
    tokenized = {key: tokens(text) for key, text in documents.items()}
    lengths = {key: len(value) for key, value in tokenized.items()}
    average = sum(lengths.values()) / max(1, len(lengths))
    document_frequency = Counter(
        term
        for terms in tokenized.values()
        for term in set(terms)
    )
    scores: dict[str, float] = {}
    count = len(tokenized)
    for key, terms in tokenized.items():
        frequencies = Counter(terms)
        score = 0.0
        for term in query_terms:
            frequency = frequencies[term]
            if not frequency:
                continue
            inverse = math.log(1 + (count - document_frequency[term] + 0.5) / (document_frequency[term] + 0.5))
            denominator = frequency + 1.5 * (1 - 0.75 + 0.75 * lengths[key] / max(1.0, average))
            score += inverse * frequency * 2.5 / denominator
        if score > 0 and math.isfinite(score):
            scores[key] = score
    ordered = sorted(scores, key=lambda key: (-scores[key], key))[:limit]
    return {key: scores[key] for key in ordered}

