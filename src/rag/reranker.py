from __future__ import annotations

from abc import ABC, abstractmethod

from .models import Evidence
from .text import tokenize


class Reranker(ABC):
    name: str
    version: str

    @abstractmethod
    def rerank(self, question: str, candidates: list[Evidence]) -> list[Evidence]: ...


class LexicalCoverageReranker(Reranker):
    """Small, explainable offline reranker used only when explicitly enabled."""

    name = "lexical_coverage"
    version = "lexical-coverage-v1"

    def rerank(self, question: str, candidates: list[Evidence]) -> list[Evidence]:
        query = set(tokenize(question))
        if not query:
            return candidates
        return sorted(candidates, key=lambda item: (
            len(query.intersection(tokenize(item.excerpt))) / len(query), item.score
        ), reverse=True)


def configured_reranker(provider: str) -> Reranker | None:
    if provider in {"", "disabled", "none"}:
        return None
    if provider == "lexical_coverage":
        return LexicalCoverageReranker()
    raise ValueError(f"unknown reranker provider: {provider}")
