"""Adversarial / malformed-input checks for the agent. Each case builds a synthetic unit under
harness/out/_robust/, runs `python -m agent analyze`, and asserts: exit code 0, answer.json
written, schema-valid, roster exact, and (where the unit has a valid manifest) zero false claims
and no refusal by the scorer's own deterministic claim rules.

Usage (from t4-work/):  PYTHONUTF8=1 ../.venv/Scripts/python harness/robustness.py
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORK = HERE.parent
T4 = WORK.parent / "track4-analysis-public"
sys.path.insert(0, str(T4))
sys.path.insert(0, str(HERE))
import winshim  # noqa: E402,F401
from run_local import _undo_crlf, schema_errors  # noqa: E402

OUT = HERE / "out" / "_robust"


def write_manifest(unit: Path, labels: dict[str, object]) -> None:
    files = []
    for p in sorted((unit / "corpus").glob("*.json")):
        if p.name == "manifest.json":
            continue
        e = {"path": f"corpus/{p.name}", "role": "corpus", "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
        lab = labels.get(p.stem, "shared")
        if lab == "shared":
            e["shared"] = True
        else:
            e["entity_ids"] = lab
        files.append(e)
    (unit / "manifest.json").write_text(json.dumps({"manifest_version": "2.0", "files": files}, indent=1), encoding="utf-8")
    cm = unit / "corpus" / "manifest.json"
    if cm.exists():
        cm.unlink()


def clone(name: str, src: str) -> Path:
    dst = OUT / name
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(T4 / "units" / src, dst)
    _undo_crlf(dst)
    return dst


def labels_of(unit: Path) -> dict[str, object]:
    for m in (unit / "manifest.json", unit / "corpus" / "manifest.json"):
        if m.exists():
            man = json.loads(m.read_text(encoding="utf-8"))
            out = {}
            for f in man["files"]:
                if f["path"].startswith("corpus/") and not f["path"].endswith("manifest.json"):
                    stem = f["path"][7:-5]
                    out[stem] = "shared" if f.get("shared") else f.get("entity_ids", [])
            return out
    return {}


def run(unit: Path, corpus: Path | None = None) -> tuple[int, dict | None, float]:
    out = unit / "_out" / "answer.json"
    if out.exists():
        out.unlink()
    env = dict(os.environ, PYTHONUTF8="1", QFBENCH_SEED="0")
    t0 = time.time()
    p = subprocess.run([sys.executable, "-m", "agent", "analyze", "--task", str(unit / "task.json"),
                        "--corpus", str(corpus or (unit / "corpus")), "--out", str(out)],
                       cwd=WORK, env=env, capture_output=True, text=True, timeout=900)
    ans = json.loads(out.read_text(encoding="utf-8")) if out.exists() else None
    return p.returncode, ans, time.time() - t0


def check(name: str, unit: Path, ans: dict | None, rc: int, secs: float, *, scorer: bool = True, extra=None) -> bool:
    from baselines.guardrails_example.citation_rail import check_claim_rules

    problems = []
    if rc != 0:
        problems.append(f"rc={rc}")
    if ans is None:
        problems.append("no answer")
    else:
        if schema_errors(ans):
            problems.append("schema")
        task = json.loads((unit / "task.json").read_text(encoding="utf-8"))
        roster = sorted(e["entity_id"] for e in task["entities"])
        if sorted(r["entity_id"] for r in ans["entity_predictions"]) != roster:
            problems.append("roster")
        if scorer:
            try:
                fs = [f for f in check_claim_rules(ans, unit, token_counter=None) if f.code != "claim_tokens_unchecked"]
                if fs:
                    problems.append("claims: " + ", ".join(sorted({f.code for f in fs})))
            except Exception as exc:  # noqa: BLE001
                problems.append(f"scorer error {exc!r}"[:160])
        if extra:
            msg = extra(ans)
            if msg:
                problems.append(msg)
    ok = not problems
    fb = (ans or {}).get("notes", {}).get("fallback")
    print(f"{'PASS' if ok else 'FAIL'}  {name:38s} {secs:5.1f}s fallback={bool(fb)}  {'; '.join(problems)}")
    return ok


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    results = []

    # 1. stale-evidence trap: a post-cutoff doc that "announces" the outcome must change nothing
    base = clone("stale_base", "t4-credit-event-2023")
    rc0, a0, s0 = run(base)
    u = clone("stale_trap", "t4-credit-event-2023")
    doc = {"doc_id": "EDGAR_TRAP_8K_20231201", "doc_date": "2023-12-01",
           "text": "Best Buy Co., Inc. filed a voluntary petition under Chapter 11 of the Bankruptcy Code on 2023-11-30. "
                   "There is substantial doubt about the Company's ability to continue as a going concern. " * 20}
    (u / "corpus" / "EDGAR_TRAP_8K_20231201.json").write_text(json.dumps(doc), encoding="utf-8")
    lab = labels_of(u)
    lab["EDGAR_TRAP_8K_20231201"] = ["BBY"]
    write_manifest(u, lab)
    rc, a, s = run(u)

    def same_and_not_cited(ans):
        cited = {c["doc_id"] for r in ans["entity_predictions"] for c in r["claims"]}
        if "EDGAR_TRAP_8K_20231201" in cited:
            return "cites the post-cutoff doc"
        p0 = {r["entity_id"]: (r.get("label"), r["point_forecast"]) for r in a0["entity_predictions"]}
        p1 = {r["entity_id"]: (r.get("label"), r["point_forecast"]) for r in ans["entity_predictions"]}
        return None if p0 == p1 else "prediction changed by a post-cutoff doc"
    results.append(check("stale-evidence trap", u, a, rc, s, extra=same_and_not_cited))

    # 2. spans[] instead of flat text, plus an undated and a malformed doc
    u = clone("spans_and_junk", "t4-cpicomp-202410-us11")
    p = u / "corpus" / "ALFRED_CPI_COMPONENTS_20241031.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    d["spans"] = [{"text": line} for line in d.pop("text").split("\n")]
    p.write_text(json.dumps(d), encoding="utf-8")
    (u / "corpus" / "UNDATED_DOC.json").write_text(json.dumps({"doc_id": "UNDATED_DOC", "text": "Shelter rose 9.9%."}), encoding="utf-8")
    (u / "corpus" / "BROKEN_DOC.json").write_text("{not json", encoding="utf-8")
    lab = labels_of(u)
    lab.pop("UNDATED_DOC", None)
    lab.pop("BROKEN_DOC", None)
    write_manifest(u, {k: v for k, v in lab.items() if k not in ("UNDATED_DOC", "BROKEN_DOC")})
    # the scorer would refuse this unit as an organizer fault (undeclared files); check shape only
    rc, a, s = run(u)
    results.append(check("spans[] + undated + malformed docs", u, a, rc, s, scorer=False))

    # 3. no manifest at all -> no corpus doc is provably admissible -> task-row claims only
    u = clone("no_manifest", "t4-eps-yoy-2023Q2-mixed")
    (u / "manifest.json").unlink()
    if (u / "corpus" / "manifest.json").exists():
        (u / "corpus" / "manifest.json").unlink()
    rc, a, s = run(u)
    only_task = lambda ans: None if all(c["doc_id"] == "task" for r in ans["entity_predictions"] for c in r["claims"]) else "cited an unlabelled doc"  # noqa: E731
    results.append(check("no manifest", u, a, rc, s, scorer=False, extra=only_task))

    # 4. missing corpus directory
    u = clone("no_corpus", "t4-fomc-curve-20220728")
    shutil.rmtree(u / "corpus")
    rc, a, s = run(u)
    results.append(check("missing corpus dir", u, a, rc, s, scorer=False))

    # 5. unknown family: odd target, no labels, string-only rows, unicode names
    u = clone("unknown_family", "t4-fomc-curve-20240918")
    t = json.loads((u / "task.json").read_text(encoding="utf-8"))
    t["family"] = "something_new"
    t["target"] = {"name": "widget_index_level", "type": "regression"}
    t["prompt"] = "Predict the widget index level for each row."
    for i, e in enumerate(t["entities"]):
        for k in list(e):
            if k not in ("entity_id", "corpus_ref"):
                e.pop(k)
        e["name"] = f"Société Générale ünït {i} — 東京"
    (u / "task.json").write_text(json.dumps(t, ensure_ascii=False), encoding="utf-8")
    rc, a, s = run(u)
    results.append(check("unknown family / no numeric columns", u, a, rc, s))

    # 6. classification with labels the agent has never seen
    u = clone("odd_labels", "t4-credit-event-2023")
    t = json.loads((u / "task.json").read_text(encoding="utf-8"))
    t["target"] = {"name": "guidance_action", "type": "classification", "labels": ["raise", "maintain", "lower", "withdraw"]}
    (u / "task.json").write_text(json.dumps(t), encoding="utf-8")
    rc, a, s = run(u)
    labs_ok = lambda ans: None if all(r.get("label") in t["target"]["labels"] for r in ans["entity_predictions"]) else "label outside vocabulary"  # noqa: E731
    results.append(check("unseen label vocabulary", u, a, rc, s, extra=labs_ok))

    # 7. ranking with a one-row roster
    u = clone("one_row_ranking", "t4-cotpos-202411-us10")
    t = json.loads((u / "task.json").read_text(encoding="utf-8"))
    t["entities"] = t["entities"][:1]
    (u / "task.json").write_text(json.dumps(t), encoding="utf-8")
    rc, a, s = run(u)
    results.append(check("ranking, one row", u, a, rc, s))

    # 8. large corpus: an ~8 MB filing for one entity (the scorer caps a document at 8 MiB)
    u = clone("big_doc", "t4-eps-growth-2024Q3-banks")
    p = next((u / "corpus").glob("EDGAR_0000019617_10Q_*.json"))
    d = json.loads(p.read_text(encoding="utf-8"))
    d["text"] = (d["text"] + "\n") * 10
    p.write_text(json.dumps(d), encoding="utf-8")
    write_manifest(u, labels_of(u))
    rc, a, s = run(u)
    results.append(check("~8 MB filing (time)", u, a, rc, s, extra=lambda ans: None if s < 120 else f"slow {s:.0f}s"))

    # 9. task_id echo + target_type omitted in task (flat key absent) -> no target_type emitted
    u = clone("no_target_type", "t4-EXAMPLE-eps-beat")
    t = json.loads((u / "task.json").read_text(encoding="utf-8"))
    t["target"].pop("type")
    (u / "task.json").write_text(json.dumps(t), encoding="utf-8")
    rc, a, s = run(u)
    results.append(check("task without target.type", u, a, rc, s, scorer=False,
                         extra=lambda ans: None if "target_type" not in ans and ans["task_id"] == t["task_id"] else "target_type guessed"))

    print(f"\n{sum(results)}/{len(results)} robustness cases pass")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
