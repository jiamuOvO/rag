"""Final closeout: apply all 100 semantic verdicts, rebuild the state ledger and the frozen qrels.

Runs ONCE, after all 100 combinations have been read. Nothing here re-runs retrieval or re-judges
anything; it only writes back the verdicts that were produced by reading the complete blocks.

Steps
  1. backup judgments/ + qrels.tsv + final_state.json + qrels_frozen_scope_1312.tsv
  2. apply the 75 new verdicts (the 25 earlier ones are already written back) to judgments/q_*.json
  3. rewrite master qrels.tsv
  4. rebuild final_state.json -- mutually exclusive states over frozen_scope_1312
  5. regenerate qrels_frozen_scope_1312.tsv from labelled x frozen scope, with hard assertions
  6. merge semantic_review_verdicts.json to the full 100 and print the final distribution

Usage: python -B tests/biomass_furan/closeout_semantic.py [--apply]
"""
from __future__ import annotations

import json
import shutil
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
JUDGES = ROOT / "judgments"
QRELS = ROOT / "qrels.tsv"
FROZEN_QRELS = ROOT / "qrels_frozen_scope_1312.tsv"
FINAL_STATE = ROOT / "final_state.json"
FROZEN_SCOPE = ROOT / "frozen_scope_1312.json"
VERDICTS = ROOT / "semantic_review_verdicts.json"
NEW_VERDICTS = ROOT / "verdicts_new75.json"
# The three borderline combinations (n=16 q_0005 / n=41 q_0014 / n=96 q_0044) live here and OVERRIDE
# their entries in verdicts_new75.json. They are only applied once the auditor has ruled on them; the
# file is present either way so that approval needs no further edit -- just run --apply.
BORDER3 = ROOT / "verdicts_border3_proposed.json"


def read_jsonl(path: Path):
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def load_labels() -> dict:
    labels = {}
    for path in sorted(JUDGES.glob("q_*.json")):
        for row in json.loads(path.read_text(encoding="utf-8"))["judgments"]:
            labels[(path.stem, row["doc_id"])] = row
    return labels


def write_labels(labels: dict, touched: set) -> None:
    by_query = defaultdict(list)
    for (qid, doc_id), row in labels.items():
        by_query[qid].append(row)
    for qid in touched:
        rows = sorted(by_query[qid], key=lambda r: r["doc_id"])
        (JUDGES / f"{qid}.json").write_text(
            json.dumps({"query_id": qid, "judgments": rows}, ensure_ascii=False, indent=1),
            encoding="utf-8")


def write_qrels(labels: dict, path: Path, scope: set | None = None) -> int:
    rows = sorted((q, d, r.get("relevance")) for (q, d), r in labels.items()
                  if scope is None or f"{q}\t{d}" in scope)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write("query_id\tdoc_id\trelevance\n")
        for q, d, rel in rows:
            fh.write(f"{q}\t{d}\t{rel}\n")
    return len(rows)


def main() -> int:
    apply_mode = "--apply" in sys.argv
    scope_list = json.loads(FROZEN_SCOPE.read_text(encoding="utf-8"))
    scope = set(scope_list)
    assert len(scope) == len(scope_list) == 1312, "frozen scope must be 1312 unique pairs"

    old_verdicts = json.loads(VERDICTS.read_text(encoding="utf-8"))
    new_verdicts = json.loads(NEW_VERDICTS.read_text(encoding="utf-8"))
    assert len(old_verdicts) == 25 and len(new_verdicts) == 75, "expect 25 + 75 verdicts"

    # borderline overrides: same n, so the 75 stay 75 and no verdict is ever double-applied
    if BORDER3.exists():
        overrides = {v["n"]: v for v in json.loads(BORDER3.read_text(encoding="utf-8"))}
        statuses = {v.get("status") for v in overrides.values()}
        print(f"border3 overrides: {sorted(overrides)} status={sorted(s for s in statuses if s)}")
        by_n = {v["n"]: v for v in new_verdicts}
        for n, v in overrides.items():
            assert n in by_n, f"border3 n={n} not in the 75"
            assert (v["query_id"], v["doc_id"]) == (by_n[n]["query_id"], by_n[n]["doc_id"]), \
                f"border3 n={n} target mismatch"
            assert by_n[n]["old_grade"] == v["old_grade"], f"border3 n={n} old_grade mismatch"
            by_n[n] = v
        new_verdicts = [by_n[n] for n in sorted(by_n)]
        assert len(new_verdicts) == 75

    state_before = json.loads(FINAL_STATE.read_text(encoding="utf-8"))
    exhausted = {f"{x['query_id']}\t{x['doc_id']}" for x in state_before
                 if x["state"] == "exhausted_unresolved"}
    assert len(exhausted) == 118, f"expect 118 exhausted, got {len(exhausted)}"

    labels = load_labels()
    print(f"labels on disk: {len(labels)} pairs "
          f"({len({q for q, _ in labels})} queries)")
    assert len(labels) == 2996, f"expect 2996 labelled pairs (master), got {len(labels)}"
    in_scope_labelled = {f"{q}\t{d}" for (q, d) in labels if f"{q}\t{d}" in scope}
    assert len(in_scope_labelled) == 1194, \
        f"expect 1194 labelled pairs inside the frozen scope, got {len(in_scope_labelled)}"
    assert in_scope_labelled == scope - exhausted, \
        "every in-scope pair is either labelled or exhausted -- no third case"

    # -- the write-back plan ----------------------------------------------------------------
    plan = []
    for v in new_verdicts:
        key = (v["query_id"], v["doc_id"])
        assert key in labels, f"verdict target missing from judgments: {key}"
        assert labels[key].get("relevance") == v["old_grade"], \
            f"{key}: disk grade {labels[key].get('relevance')} != verdict old_grade {v['old_grade']}"
        assert f"{v['query_id']}\t{v['doc_id']}" in scope, f"{key} outside frozen scope"
        plan.append(v)

    changes = [v for v in plan if v["changed"]]
    print(f"verdicts to apply: {len(plan)} (changed {len(changes)}, kept {len(plan) - len(changes)})")
    print("transitions:", sorted(Counter((v["old_grade"], v["final_grade"]) for v in changes).items()))

    # -- preflight: the eight checks the auditor asked for -----------------------------------
    merged = [{**v, "final_grade": v.get("final_grade", v.get("new_grade"))} for v in old_verdicts] + plan
    merged_keys = [(v["query_id"], v["doc_id"]) for v in merged]
    assert len(merged) == 100, f"expect 100 verdicts, got {len(merged)}"
    assert len(set(merged_keys)) == 100, "100 verdict identities must be unique"
    assert all(f"{q}\t{d}" in scope for q, d in merged_keys), "every verdict pair must be in scope"
    assert all((q, d) in labels for q, d in merged_keys), "every verdict pair must be on disk"
    print("[1] 100 semantic verdicts: unique identities, in scope, present on disk  OK")

    assert len(plan) == 75 and len(changes) == 51 and len(plan) - len(changes) == 24, \
        f"expect 75 verdicts with changed=51/kept=24, got {len(plan)}/{len(changes)}"
    print("[2] 75 verdicts this round: changed=51 / kept=24  OK")

    border41 = [v for v in plan if v["query_id"] == "q_0014" and v["doc_id"] == "chk_45a69b0e755aa8b4a0a506fb"]
    assert len(border41) == 1 and border41[0]["old_grade"] == 3 and border41[0]["final_grade"] == 0, \
        "q_0014 chk_45a69b0e755aa8b4a0a506fb must be 3 -> 0"
    assert all(v.get("status") == "AUDITED_APPROVED" for v in json.loads(BORDER3.read_text(encoding="utf-8"))), \
        "the three borderline verdicts must be marked AUDITED_APPROVED"
    print("[3] q_0014 chk_45a69b0e755aa8b4a0a506fb: 3 -> 0；三条边界项状态 AUDITED_APPROVED  OK")

    print(f"[4] scope = {len(scope)}  OK")
    print(f"[5] exhausted_unresolved = {len(exhausted)}  OK")
    print(f"[6] in-scope labelled = {len(in_scope_labelled)}  OK")

    master_keys_before = set()
    for line in QRELS.read_text(encoding="utf-8").strip().split("\n")[1:]:
        q, d, _ = line.split("\t")
        master_keys_before.add(f"{q}\t{d}")
    print(f"[7] master qrels historical range: {len(master_keys_before)} pairs captured; "
          f"range structure will be re-checked after write-back  OK")

    if not apply_mode:
        print("[8] dry-run: nothing written  OK")
        print("dry-run: preflight passed, no file written")
        return 0

    stamp = time.strftime("%Y%m%dT%H%M%S")
    backup = ROOT / f"closeout_backup_{stamp}"
    backup.mkdir()
    shutil.copytree(JUDGES, backup / "judgments")
    for src in (QRELS, FINAL_STATE, FROZEN_QRELS, VERDICTS):
        shutil.copy2(src, backup / src.name)
    print(f"backup -> {backup.name}")

    # -- 2. write back --------------------------------------------------------------------
    touched = set()
    for v in plan:
        key = (v["query_id"], v["doc_id"])
        row = labels[key]
        row["relevance"] = v["final_grade"]
        row["review_type"] = "Agent review"
        row["agent_review"] = {
            "old_grade": v["old_grade"],
            "final_grade": v["final_grade"],
            "changed": v["changed"],
            "explanation": v["explanation"],
            "basis": v["review_basis"],
        }
        touched.add(v["query_id"])
    write_labels(labels, touched)
    print(f"judgments rewritten: {len(touched)} query files")

    # -- 3. master qrels -------------------------------------------------------------------
    n_master = write_qrels(labels, QRELS)
    master_keys_after = set()
    for line in QRELS.read_text(encoding="utf-8").strip().split("\n")[1:]:
        q, d, _ = line.split("\t")
        master_keys_after.add(f"{q}\t{d}")
    assert master_keys_after == master_keys_before, "master qrels range structure changed"
    print(f"{QRELS.name}: {n_master} rows | range structure unchanged "
          f"({len(master_keys_after)} pairs, identical before/after)")

    # -- 4. mutually exclusive state ledger -------------------------------------------------
    valid = {f"{q}\t{d}": r.get("relevance") for (q, d), r in labels.items()}
    unresolved_sem = sorted(scope - set(exhausted) - set(valid))
    ledger = []
    for pair in sorted(scope):
        if pair in valid:
            ledger.append({"query_id": pair.split("\t")[0], "doc_id": pair.split("\t")[1],
                           "state": "valid", "grade": valid[pair]})
        elif pair in exhausted:
            ledger.append({"query_id": pair.split("\t")[0], "doc_id": pair.split("\t")[1],
                           "state": "exhausted_unresolved", "grade": None})
        else:
            ledger.append({"query_id": pair.split("\t")[0], "doc_id": pair.split("\t")[1],
                           "state": "semantic_unresolved", "grade": None})
    FINAL_STATE.write_text(json.dumps(ledger, ensure_ascii=False, indent=1), encoding="utf-8")
    counts = Counter(x["state"] for x in ledger)
    print(f"{FINAL_STATE.name}: valid {counts['valid']} | "
          f"semantic_unresolved {counts['semantic_unresolved']} | "
          f"exhausted_unresolved {counts['exhausted_unresolved']} | total {len(ledger)}")
    assert sum(counts.values()) == 1312

    # -- 5. frozen qrels -------------------------------------------------------------------
    n_frozen = write_qrels(labels, FROZEN_QRELS, scope)
    frozen_pairs, frozen_grades = [], Counter()
    for line in FROZEN_QRELS.read_text(encoding="utf-8").strip().split("\n")[1:]:
        q, d, rel = line.split("\t")
        frozen_pairs.append(f"{q}\t{d}")
        frozen_grades[int(rel)] += 1
    outside = [p for p in frozen_pairs if p not in scope]
    assert not outside, f"out-of-scope rows: {outside[:5]}"
    assert len(frozen_pairs) == len(set(frozen_pairs)), "duplicate primary keys"
    in_scope_valid = {k: v for k, v in valid.items() if k in scope}
    assert len(in_scope_valid) == 1194, f"expect 1194 in-scope labelled, got {len(in_scope_valid)}"
    assert len(frozen_pairs) == len(in_scope_valid), \
        f"frozen rows {len(frozen_pairs)} must equal in-scope labelled {len(in_scope_valid)}"
    print(f"{FROZEN_QRELS.name}: {n_frozen} rows | outside scope {len(outside)} | unique keys True")
    print("grade distribution:", dict(sorted(frozen_grades.items())))
    coverage = len(frozen_pairs) / 1312
    print(f"judged coverage: {len(frozen_pairs)}/1312 = {coverage:.1%}")

    # -- 6. merged verdict ledger ----------------------------------------------------------
    merged = []
    for v in old_verdicts:
        merged.append({"n": None, "query_id": v["query_id"], "doc_id": v["doc_id"],
                       "old_grade": v["old_grade"], "final_grade": v.get("new_grade"),
                       "changed": v["old_grade"] != v.get("new_grade"),
                       "actual_evidence": v.get("actual_evidence"),
                       "explanation": v.get("explanation"),
                       "review_type": "Agent review", "review_basis": v.get("review_basis")})
    merged += [{k: v[k] for k in ("n", "query_id", "doc_id", "old_grade", "final_grade",
                                  "changed", "actual_evidence", "explanation",
                                  "review_type", "review_basis")} for v in new_verdicts]
    assert len({(v["query_id"], v["doc_id"]) for v in merged}) == 100
    VERDICTS.write_text(json.dumps(merged, ensure_ascii=False, indent=1), encoding="utf-8")
    total_changed = sum(1 for v in merged if v["changed"])
    print(f"{VERDICTS.name}: {len(merged)} verdicts | changed {total_changed} | kept {100 - total_changed}")
    print("all-100 transitions:",
          sorted(Counter((v["old_grade"], v["final_grade"]) for v in merged if v["changed"]).items()))

    (ROOT / "closeout_report.json").write_text(json.dumps({
        "backup": backup.name,
        "verdicts_applied": len(plan),
        "verdicts_changed": len(changes),
        "master_qrels_rows": n_master,
        "frozen_qrels_rows": n_frozen,
        "frozen_outside_scope": len(outside),
        "states": dict(counts),
        "grade_distribution": {str(k): v for k, v in sorted(frozen_grades.items())},
        "judged_coverage": coverage,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print("closeout_report.json written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
