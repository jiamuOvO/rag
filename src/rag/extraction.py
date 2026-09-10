from __future__ import annotations

import re

from .models import Evidence

EXTRACTION_SCHEMA_VERSION = "reaction-evidence-v1"
EXTRACTION_JSON_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["schema_version", "records"],
    "properties": {
        "schema_version": {"const": EXTRACTION_SCHEMA_VERSION},
        "records": {"type": "array", "items": {
            "type": "object", "required": ["field", "raw_value", "evidence_id"],
            "properties": {
                "field": {"enum": ["substrate", "product", "catalyst", "solvent",
                                     "temperature", "time", "yield", "conversion", "selectivity"]},
                "raw_value": {"type": "string"}, "normalized_value": {"type": ["number", "string", "null"]},
                "unit": {"type": ["string", "null"]}, "evidence_id": {"type": "string"},
            },
        }},
    },
}

NUMERIC_PATTERNS = {
    "temperature": re.compile(r"(?<!\w)(-?\d+(?:\.\d+)?)\s*(?:°|[\x00-\x1f])?\s*C\b", re.I),
    "time": re.compile(r"(?<!\w)(\d+(?:\.\d+)?)\s*(h|min|s)\b", re.I),
    "yield": re.compile(r"(?:yield(?:ed)?(?:\s+(?:of|was|is|reached|up to))?\s*[:=]?\s*)(\d+(?:\.\d+)?)\s*(%)", re.I),
    "conversion": re.compile(r"(?:conversion(?:\s+(?:of|was|is|reached|up to))?\s*[:=]?\s*)(\d+(?:\.\d+)?)\s*(%)", re.I),
    "selectivity": re.compile(r"(?:selectivity(?:\s+(?:of|was|is|reached|up to))?\s*[:=]?\s*)(\d+(?:\.\d+)?)\s*(%)", re.I),
}
ENTITY_TERMS = {
    "substrate": ["xylose", "glucose", "fructose", "cellulose", "biomass", "lignocellulose"],
    "product": ["furfural", "5-hmf", "hmf", "hydroxymethylfurfural", "levulinic acid"],
    "catalyst": ["sncl4", "alcl3", "crcl3", "fecl3", "hcl", "h2so4", "formic acid"],
    "solvent": ["water", "emimbr", "bmimcl", "dmso", "thf", "mibk", "toluene"],
}


def extract_reaction_evidence(evidence: list[Evidence]) -> dict:
    """Conservative rule prototype: every value keeps its source and conflicts coexist."""
    records: list[dict] = []
    seen: set[tuple] = set()
    for item in evidence:
        text = item.excerpt
        lowered = text.lower()
        for field, pattern in NUMERIC_PATTERNS.items():
            for match in pattern.finditer(text):
                raw = match.group(0)
                unit = match.group(2) if match.lastindex and match.lastindex >= 2 else "°C"
                key = (field, raw.lower(), item.evidence_id)
                if key not in seen:
                    seen.add(key)
                    records.append({"field": field, "raw_value": raw,
                                    "normalized_value": float(match.group(1)),
                                    "unit": unit, "evidence_id": item.evidence_id})
        for field, terms in ENTITY_TERMS.items():
            for term in terms:
                if re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", lowered):
                    key = (field, term, item.evidence_id)
                    if key not in seen:
                        seen.add(key)
                        records.append({"field": field, "raw_value": term,
                                        "normalized_value": term.upper() if term in {"hmf", "5-hmf"} else term,
                                        "unit": None, "evidence_id": item.evidence_id})
    return {"schema_version": EXTRACTION_SCHEMA_VERSION, "records": records}
