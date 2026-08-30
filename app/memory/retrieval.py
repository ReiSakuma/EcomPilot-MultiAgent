from __future__ import annotations


def keyword_retrieve(query: str, corpus: list[str], limit: int = 5) -> list[str]:
    terms = set(query.lower().split())
    ranked = sorted(corpus, key=lambda text: len(terms & set(text.lower().split())), reverse=True)
    return ranked[:limit]
