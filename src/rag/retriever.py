from __future__ import annotations

import hashlib
import math
from collections import Counter

import numpy as np

from .models import Evidence
from .text import normalize_text, tokenize

QUERY_ALIASES = {
    "糠醛": ["furfural"],
    "木糖": ["xylose"],
    "葡萄糖": ["glucose"],
    "果糖": ["fructose"],
    "生物质": ["biomass"],
    "收率": ["yield"],
    "产率": ["yield"],
    "转化率": ["conversion"],
    "选择性": ["selectivity"],
    "温度": ["temperature"],
    "催化剂": ["catalyst"],
    "反应条件": ["reaction", "conditions"],
    "羟甲基糠醛": ["hydroxymethylfurfural", "hmf", "5-hmf"],
}
STOPWORDS = {"a", "an", "and", "are", "as", "at", "be", "by", "did", "do", "does",
             "for", "from", "how", "in", "is", "it", "of", "on", "or", "reported",
             "the", "to", "was", "were", "what", "which", "with"}


def query_tokens(query: str) -> list[str]:
    analysis = analyze_query(query)
    return analysis["tokens"] + analysis["expansions"]


def analyze_query(query: str) -> dict:
    terms = tokenize(query)
    expansions: list[str] = []
    lowered = query.lower()
    for phrase, aliases in QUERY_ALIASES.items():
        if phrase in lowered:
            expansions.extend(alias for alias in aliases if alias not in terms and alias not in expansions)
    return {"normalized": normalize_text(query).lower(), "tokens": terms, "expansions": expansions}


def has_reliable_lexical_support(query: str, excerpt: str) -> bool:
    query_terms = {term for term in query_tokens(query) if term not in STOPWORDS and len(term) > 1}
    evidence_terms = set(tokenize(excerpt))
    required = 1 if len(query_terms) <= 2 else 2
    return len(query_terms.intersection(evidence_terms)) >= required


class BM25Retriever:
    def __init__(self, chunks: list[dict], *, k1: float = 1.5, b: float = 0.75):
        self.chunks = chunks
        self.k1, self.b = k1, b
        self.tokens = [tokenize(c["text"]) for c in chunks]
        self.term_freqs = [Counter(x) for x in self.tokens]
        self.lengths = [len(x) for x in self.tokens]
        self.avg_len = sum(self.lengths) / max(1, len(self.lengths))
        self.doc_freq: Counter[str] = Counter()
        for values in self.tokens:
            self.doc_freq.update(set(values))

    def search(self, query: str, top_k: int = 8) -> list[Evidence]:
        terms = [term for term in query_tokens(query) if term not in STOPWORDS]
        if not terms or not self.chunks:
            return []
        scores: list[tuple[float, int]] = []
        n = len(self.chunks)
        for index, frequencies in enumerate(self.term_freqs):
            score = 0.0
            for term in terms:
                tf = frequencies.get(term, 0)
                if not tf:
                    continue
                df = self.doc_freq[term]
                idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
                norm = tf + self.k1 * (1 - self.b + self.b * self.lengths[index] / max(1, self.avg_len))
                score += idf * tf * (self.k1 + 1) / norm
            if score > 0:
                scores.append((score, index))
        scores.sort(reverse=True)
        evidence: list[Evidence] = []
        seen: set[str] = set()
        for score, index in scores:
            chunk = self.chunks[index]
            fingerprint = " ".join(tokenize(chunk["text"])[:80])
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            evidence_id = "ev_" + hashlib.sha256(
                f"{chunk['chunk_id']}:{query}".encode("utf-8")
            ).hexdigest()[:20]
            evidence.append(Evidence(
                evidence_id=evidence_id,
                chunk_id=chunk["chunk_id"],
                paper_id=chunk["paper_id"],
                paper_name=chunk["paper_name"],
                page_start=chunk["page_start"],
                page_end=chunk["page_end"],
                section_path=chunk["section_path"],
                excerpt=chunk["text"][:1600],
                score=round(score, 6),
                document_id=chunk.get("document_id"),
                collection_id=chunk.get("collection_id"),
                source_type=chunk.get("scope_type", "official"),
            ))
            if len(evidence) >= top_k:
                break
        return evidence


class DenseRetriever:
    def __init__(self, chunks: list[dict]):
        self.chunks = chunks

    def search(self, query_vector: list[float], top_k: int = 8) -> list[Evidence]:
        if not self.chunks or not query_vector:
            return []
        dimensions = {int(chunk["dimensions"]) for chunk in self.chunks}
        if len(dimensions) != 1 or next(iter(dimensions)) != len(query_vector):
            raise ValueError("query and stored embedding dimensions differ")
        matrix = np.vstack([
            np.frombuffer(chunk["vector"], dtype=np.float32) for chunk in self.chunks
        ])
        query = np.asarray(query_vector, dtype=np.float32)
        denominator = np.linalg.norm(matrix, axis=1) * np.linalg.norm(query)
        similarities = np.divide(matrix @ query, denominator, out=np.zeros(len(matrix)), where=denominator > 0)
        order = np.argsort(-similarities)[:top_k]
        result: list[Evidence] = []
        for index in order:
            score = float(similarities[index])
            if not np.isfinite(score):
                continue
            chunk = self.chunks[int(index)]
            evidence_id = "ev_" + hashlib.sha256(
                f"{chunk['chunk_id']}:dense".encode("utf-8")
            ).hexdigest()[:20]
            result.append(Evidence(
                evidence_id=evidence_id, chunk_id=chunk["chunk_id"], paper_id=chunk["paper_id"],
                paper_name=chunk["paper_name"], page_start=chunk["page_start"],
                page_end=chunk["page_end"], section_path=chunk["section_path"],
                excerpt=chunk["text"][:1600], score=round(score, 6),
                document_id=chunk.get("document_id"),
                collection_id=chunk.get("collection_id"),
                source_type=chunk.get("scope_type", "official"),
            ))
        return result


def reciprocal_rank_fusion(lexical: list[Evidence], dense: list[Evidence], *, top_k: int = 8,
                           rank_constant: int = 60) -> list[Evidence]:
    scores: dict[str, float] = {}
    items: dict[str, Evidence] = {}
    for ranking in (lexical, dense):
        for rank, evidence in enumerate(ranking, start=1):
            scores[evidence.chunk_id] = scores.get(evidence.chunk_id, 0.0) + 1.0 / (rank_constant + rank)
            items[evidence.chunk_id] = evidence
    ordered = sorted(scores, key=scores.get, reverse=True)[:top_k]
    result = []
    for chunk_id in ordered:
        item = items[chunk_id]
        item.evidence_id = "ev_" + hashlib.sha256(f"{chunk_id}:hybrid".encode("utf-8")).hexdigest()[:20]
        item.score = round(scores[chunk_id], 8)
        result.append(item)
    return result
