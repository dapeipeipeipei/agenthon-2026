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
    # the card still says classification, so every row must carry a label from the vocabulary
    results.append(check("task without target.type", u, a, rc, s, scorer=False,
                         extra=lambda ans: None if "target_type" not in ans and ans["task_id"] == t["task_id"]
                         and all(r.get("label") in t["target"]["labels"] for r in ans["entity_predictions"])
                         else "target_type written or label missing"))

    # 10. a spans-only document WITH a valid manifest: the scorer reads offsets into the flat
    #     `text` field only, so such a document must never be cited by a claim
    u = clone("spans_cited", "t4-cpicomp-202410-us11")
    p = u / "corpus" / "ALFRED_CPI_COMPONENTS_20241031.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    d["spans"] = [{"text": line} for line in d.pop("text").split("\n")]
    p.write_text(json.dumps(d), encoding="utf-8")
    write_manifest(u, labels_of(u))
    rc, a, s = run(u)
    no_spans_doc = lambda ans: None if all(c["doc_id"] != "ALFRED_CPI_COMPONENTS_20241031" for r in ans["entity_predictions"] for c in r["claims"]) else "cited a spans-only doc"  # noqa: E731
    results.append(check("spans-only doc never cited", u, a, rc, s, extra=no_spans_doc))

    # 11. a corpus file listed with a role other than "corpus" does not resolve in the scorer
    u = clone("role_input", "t4-eps-growth-2024Q3-banks")
    man = json.loads((u / "manifest.json").read_text(encoding="utf-8")) if (u / "manifest.json").exists() else None
    if man is None:
        write_manifest(u, labels_of(u))
        man = json.loads((u / "manifest.json").read_text(encoding="utf-8"))
    demoted = set()
    for f in man["files"]:
        if f.get("role") == "corpus" and "_10Q_" in f["path"]:
            f["role"] = "input"
            demoted.add(f["path"][7:-5])
    (u / "manifest.json").write_text(json.dumps(man, indent=1), encoding="utf-8")
    if (u / "corpus" / "manifest.json").exists():
        (u / "corpus" / "manifest.json").unlink()
    rc, a, s = run(u)
    no_demoted = lambda ans: None if demoted and not ({c["doc_id"] for r in ans["entity_predictions"] for c in r["claims"]} & demoted) else "cited a non-corpus-role doc"  # noqa: E731
    results.append(check("non-corpus role never cited", u, a, rc, s, scorer=False, extra=no_demoted))

    # 12. classification with no label vocabulary at all, card missing: a label on every row
    u = clone("no_vocab", "t4-postearn-20240201-megacap")
    t = json.loads((u / "task.json").read_text(encoding="utf-8"))
    t["target"].pop("labels")
    (u / "task.json").write_text(json.dumps(t), encoding="utf-8")
    (u / "card.toml").unlink()
    rc, a, s = run(u)
    results.append(check("classification, no vocabulary, no card", u, a, rc, s, scorer=False,
                         extra=lambda ans: None if all(isinstance(r.get("label"), str) and r["label"] for r in ans["entity_predictions"]) else "row without label"))

    # 13. a very long pipe table (20,000 rows) must not blow the time budget
    u = clone("long_table", "t4-auction-btc-202411-us7")
    p = [q for q in sorted((u / "corpus").glob("*.json")) if q.name != "manifest.json"][0]
    d = json.loads(p.read_text(encoding="utf-8"))
    rows = "\n".join(f"{2000 + i // 365}-{(i // 28) % 12 + 1:02d}-{i % 28 + 1:02d} | {2.0 + (i % 17) / 10:.2f}" for i in range(20000))
    d["text"] = d["text"] + "\n\ndate | bid_to_cover_ratio\n" + rows + "\n"
    p.write_text(json.dumps(d), encoding="utf-8")
    write_manifest(u, labels_of(u))
    rc, a, s = run(u)
    results.append(check("20k-row table (time)", u, a, rc, s, extra=lambda ans: None if s < 60 else f"slow {s:.0f}s"))

    # 14. canary material in the corpus and card must never reach the output
    u = clone("canary", "t4-credit-event-2023")
    guid = "1ac5a43a-a107-4a23-8ce1-8b702a2d5da5"
    for p in sorted((u / "corpus").glob("*.json")):
        if p.name == "manifest.json":
            continue
        d = json.loads(p.read_text(encoding="utf-8"))
        d["text"] = f"CANARY {guid} There is substantial doubt about the Company's ability to continue as a going concern. Net loss of $12.5 million. {guid}\n" + d["text"]
        p.write_text(json.dumps(d), encoding="utf-8")
    write_manifest(u, labels_of(u))
    rc, a, s = run(u)
    no_canary = lambda ans: None if guid not in json.dumps(ans) and "canary" not in json.dumps(ans).lower() else "canary in output"  # noqa: E731
    results.append(check("canary never echoed", u, a, rc, s, extra=no_canary))

    # 15. a 35-row regression unit: full precision overflows the 3,000-byte grader cap, compact fits
    u = clone("wide_roster", "t4-fomc-curve-20240918")
    t = json.loads((u / "task.json").read_text(encoding="utf-8"))
    base = t["entities"]
    t["entities"] = [dict(base[i % len(base)], entity_id=f"R{i:02d}", name=f"Row {i}") for i in range(35)]
    (u / "task.json").write_text(json.dumps(t), encoding="utf-8")
    rc, a, s = run(u)

    def answer_bytes(ans):
        proj = [{"entity_id": r["entity_id"], **{k: r[k] for k in ("label", "point_forecast", "interval") if k in r}} for r in ans["entity_predictions"]]
        n = len(json.dumps(proj, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")) - 2
        return None if n <= 3000 else f"answer bytes {n} > 3000"
    results.append(check("35-row roster (answer bytes)", u, a, rc, s, scorer=False, extra=answer_bytes))

    # 17. wide roster x large shared corpus (keyword retrieval must not rescan per entity)
    u = clone("wide_shared", "t4-fomc-curve-20240918")
    t = json.loads((u / "task.json").read_text(encoding="utf-8"))
    base = t["entities"]
    t["entities"] = [dict(base[i % len(base)], entity_id=f"S{i:03d}", name=f"Series {i}") for i in range(120)]
    (u / "task.json").write_text(json.dumps(t), encoding="utf-8")
    para = ("Treasury yields rose 12 basis points as the committee held the target range at 5.25 percent "
            "while inflation expectations eased to 2.4 percent and payrolls added 150,000 jobs. ")
    for j in range(12):
        doc = {"doc_id": f"BIG_SHARED_{j}", "doc_date": t["cutoff_date"], "text": para * 2500}
        (u / "corpus" / f"BIG_SHARED_{j}.json").write_text(json.dumps(doc), encoding="utf-8")
    lab = labels_of(u)
    for j in range(12):
        lab[f"BIG_SHARED_{j}"] = "shared"
    write_manifest(u, lab)
    rc, a, s = run(u)
    results.append(check("120 rows x 12 big shared docs (time)", u, a, rc, s, scorer=False,
                         extra=lambda ans: None if s < 60 and not ans.get("notes", {}).get("fallback") else f"slow {s:.0f}s or fallback"))

    # 16. seeds do not change the answer
    u = clone("seed", "t4-cotpos-202411-us10")
    _, a1, _ = run(u)
    os.environ["QFBENCH_SEED"] = "12345"
    try:
        out = u / "_out" / "answer.json"
        p = subprocess.run([sys.executable, "-m", "agent", "analyze", "--task", str(u / "task.json"), "--corpus", str(u / "corpus"), "--out", str(out)],
                           cwd=WORK, env=dict(os.environ, PYTHONUTF8="1", QFBENCH_SEED="12345", PYTHONHASHSEED="777"), capture_output=True, text=True, timeout=900)
        a2 = json.loads(out.read_text(encoding="utf-8"))
    finally:
        os.environ.pop("QFBENCH_SEED", None)
    strip = lambda ans: {k: v for k, v in ans.items() if k != "notes"}  # noqa: E731
    results.append(check("seed / hash-seed invariance", u, a2, p.returncode, 0.0,
                         extra=lambda ans: None if strip(a1) == strip(ans) else "answer depends on the seed"))

    print(f"\n{sum(results)}/{len(results)} robustness cases pass")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
