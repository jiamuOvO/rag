from __future__ import annotations

import json
import math
import re

from .models import Evidence

MAX_ROWS, MAX_COLUMNS, MAX_TEXT, MAX_ABS_VALUE = 20, 6, 160, 1e12
BLOCK_RE = re.compile(r"\n?```(?:json|visualization)?\s*(\{.*\})\s*```\s*$", re.DOTALL)


def _number_in_excerpt(value: int | float, excerpt: str, column: str = "") -> bool:
    if (isinstance(value, bool) or not math.isfinite(float(value)) or
            abs(float(value)) > MAX_ABS_VALUE):
        return False
    token = str(value)
    variants = {token, f"{float(value):g}"}
    matches = [match for variant in variants for match in
               re.finditer(rf"(?<![\d.]){re.escape(variant)}(?![\d.])", excerpt, re.IGNORECASE)]
    if not matches:
        return False
    name = column.casefold()
    unit_patterns = {
        "yield": r"%|percent", "conversion": r"%|percent",
        "temperature": r"°\s*c|◦\s*c|\b[ck]\b", "time": r"\b(?:s|min|h|hour|hours)\b",
        "pressure": r"\b(?:pa|kpa|mpa|bar)\b", "concentration": r"%|\b(?:mm|mol|wt|g\s*l)\b",
    }
    expected = next((pattern for key, pattern in unit_patterns.items() if key in name), None)
    if not expected:
        return True
    return bool(re.search(expected, excerpt, re.IGNORECASE))


def _claim_supported(value: object, excerpts: list[str]) -> bool:
    raw = str(value).strip().casefold()
    numbers = re.findall(r"\d+(?:\.\d+)?", raw)
    if numbers:
        column = ("temperature" if re.search(r"°\s*c|◦\s*c|\b[ck]\b", raw) else
                  "time" if re.search(r"\b(?:s|min|h|hour|hours)\b", raw) else "")
        return all(any(_number_in_excerpt(float(number), excerpt, column)
                       for excerpt in excerpts) for number in numbers)
    tokens = [token.rstrip("s") for token in re.findall(r"[A-Za-z]{3,}", raw)
              if token not in {"table", "chart", "case", "value", "values", "comparison"}]
    if tokens:
        return any(token in excerpt.casefold() for token in tokens for excerpt in excerpts)
    if not raw:
        return True
    return any(re.search(rf"(?<!\w){re.escape(raw)}(?!\w)", excerpt.casefold()) for excerpt in excerpts)


def extract_visualization(answer: str, evidence: list[Evidence]) -> tuple[str, list[dict], list[dict]]:
    match = BLOCK_RE.search(answer or "")
    if not match:
        return answer, [], []
    clean = answer[:match.start()].rstrip()
    warning = {"code": "VISUALIZATION_INVALID", "message": "不可信或无效的可视化已被移除"}
    try:
        item = json.loads(match.group(1))
        if item.get("type") not in {"bar", "table"}:
            raise ValueError("unsupported type")
        columns, rows = item.get("columns"), item.get("rows")
        if not isinstance(columns, list) or not 2 <= len(columns) <= MAX_COLUMNS:
            raise ValueError("invalid columns")
        if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_ROWS:
            raise ValueError("invalid rows")
        if len(str(item.get("title", ""))) > MAX_TEXT or any(len(str(x)) > MAX_TEXT for x in columns):
            raise ValueError("text too long")
        allowed = {x.evidence_id: x for x in evidence}
        excerpts = [x.excerpt for x in evidence]
        if not _claim_supported(item.get("title", ""), excerpts):
            raise ValueError("unsupported title claim")
        valid = []
        for row in rows:
            if not isinstance(row, list) or len(row) != len(columns):
                continue
            ev_id = row[-1]
            ev = allowed.get(ev_id)
            if (not ev or any(len(str(x)) > MAX_TEXT for x in row) or
                    not _claim_supported(row[0], [ev.excerpt])):
                continue
            numbers = [(x, columns[index]) for index, x in enumerate(row[:-1])
                       if isinstance(x, (int, float)) and not isinstance(x, bool)]
            if not numbers or not all(_number_in_excerpt(x, ev.excerpt, column) for x, column in numbers):
                continue
            valid.append(row)
        if len(valid) < 3:
            raise ValueError("fewer than three evidence-verified rows")
        item = {"type": item["type"], "title": str(item.get("title", "")),
                "columns": [str(x) for x in columns], "rows": valid}
        return clean, [item], []
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        warning["reason"] = str(exc)[:80]
        return clean, [], [warning]
