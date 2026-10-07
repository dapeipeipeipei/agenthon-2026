"""Final-only checks, run locally: the full NLI faithfulness judge (both pinned DeBERTa models,
contradiction check APPLIED, as in the Final) and a strict submitted_reasons check, on every
answer under one or more answer roots.

    PYTHONUTF8=1 ../.venv/Scripts/python harness/nli_check.py --cache-dir C:/Users/wensh/hf-model-cache \
        --units-root harness/out/v_c/_scratch_units_verified harness/out/v_c/ours harness/out/v_d/ours

Exit 0 only if, for every answer: admitted, contradiction check applied, 0 false claims, schema
valid, and `check_submitted_reasons` reports nothing (a malformed reasons block voids the unit).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
T4 = HERE.parent.parent / "track4-analysis-public"
sys.path.insert(0, str(T4))
sys.path.insert(0, str(HERE))
import winshim  # noqa: E402,F401
from run_local import schema_errors  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache-dir", required=True)
    ap.add_argument("--units-root", required=True, help="scratch unit copies (LF-restored manifests)")
    ap.add_argument("answer_roots", nargs="+")
    args = ap.parse_args()

    from baselines.guardrails_example.citation_rail import check_submitted_reasons, load_corpus
    from faithfulness.judge import build_ensemble_judge, build_unit_context, check_answer

    judge = build_ensemble_judge(cache_dir=args.cache_dir)
    units_root = Path(args.units_root)
    allok = True
    for root in map(Path, args.answer_roots):
        for ans_path in sorted(root.glob("*/answer.json")):
            unit = units_root / ans_path.parent.name
            ans = json.loads(ans_path.read_text(encoding="utf-8"))
            why = []
            try:
                ctx = build_unit_context(str(unit))
                res = check_answer(ans, ctx, judge)
                worst = max((c.score for c in res.claims if c.score is not None), default=0.0)
                if res.false_count:
                    why.append(f"{res.false_count} false: " + "; ".join(
                        f"{c.entity_id} {','.join(c.reasons)}" for c in res.claims if c.false)[:300])
                if not res.contradiction_applied:
                    why.append("contradiction NOT applied")
                summary = f"claims={res.claim_count} false={res.false_count} maxP(contra)={worst:.3f}"
            except Exception as exc:  # noqa: BLE001
                why.append(f"refused/error: {type(exc).__name__}: {str(exc)[:200]}")
                summary = "-"
            if schema_errors(ans):
                why.append("schema")
            task = json.loads((unit / "task.json").read_text(encoding="utf-8"))
            rf = check_submitted_reasons(ans, load_corpus(unit / "corpus"), task["cutoff_date"])
            if rf:
                why.append("reasons: " + ",".join(sorted({f.code for f in rf})))
            n_r = len(ans.get("submitted_reasons") or [])
            allok &= not why
            print(f"{'PASS' if not why else 'FAIL'}  {root.parent.name}/{root.name:5s} {ans_path.parent.name:32s} {summary} reasons={n_r}"
                  + ("  " + " | ".join(why) if why else ""), flush=True)
    print(f"NLI + REASONS CHECK: {'PASS' if allok else 'FAIL'}")
    return 0 if allok else 1


if __name__ == "__main__":
    sys.exit(main())
