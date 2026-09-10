from __future__ import annotations

from pathlib import Path

import yaml

from .pipeline import Pipeline


def run_cases(pipeline: Pipeline, path: Path) -> dict:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cases = raw.get("cases", [])
    papers = {item["file_name"]: item["paper_id"] for item in pipeline.store.papers()}
    results = []
    for case in cases:
        scope = [papers[name] for name in case.get("paper_names", []) if name in papers] or None
        result = pipeline.query(case["question"], top_k=int(case.get("top_k", 5)), paper_ids=scope)
        evidence_text = " ".join(item.excerpt.lower() for item in result.evidence)
        expected_mode = case.get("expected_mode", "evidence")
        checks = {
            "mode": result.insufficient_evidence if expected_mode == "refusal" else bool(result.evidence),
            "terms": all(str(term).lower() in evidence_text for term in case.get("required_terms", [])),
            "scope": not scope or all(item.paper_id in scope for item in result.evidence),
            "citations": all(item.evidence_id.startswith("ev_") for item in result.evidence),
        }
        results.append({
            "id": case["id"], "passed": all(checks.values()), "checks": checks,
            "request_id": result.request_id, "answer_mode": result.answer_mode,
            "degraded": result.degraded, "degradation_reason": result.degradation_reason,
            "evidence_count": len(result.evidence),
            "diagnosis_hint": "retrieve" if not result.evidence else
                              ("component_degradation" if result.degraded else "passed"),
        })
    passed = sum(item["passed"] for item in results)
    return {"case_file": str(path), "total": len(results), "passed": passed,
            "failed": len(results) - passed, "results": results}
