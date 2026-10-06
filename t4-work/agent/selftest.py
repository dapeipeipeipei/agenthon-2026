"""Build-time smoke test on a tiny synthetic unit (no competition data is vendored into the image).

    python -m agent.selftest      # exits non-zero if the answer is missing or schema-invalid
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path


def _unit(root: Path) -> Path:
    u = root / "unit"
    (u / "corpus").mkdir(parents=True)
    task = {
        "task_id": "selftest-unit", "schema_version": "3", "family": "selftest",
        "target": {"name": "widget_ratio", "type": "regression"},
        "prompt": "Predict each widget's next widget_ratio.", "cutoff_date": "2024-01-31",
        "resolution_date": "2024-02-29", "interval_level": 0.9, "corpus_manifest": "manifest.json",
        "entities": [{"entity_id": "W1", "name": "Widget one", "latest_widget_ratio": 2.5, "unit": "widget_ratio"},
                     {"entity_id": "W2", "name": "Widget two", "latest_widget_ratio": 1.1, "unit": "widget_ratio"}],
    }
    (u / "task.json").write_text(json.dumps(task), encoding="utf-8")
    files = []
    for i, eid in enumerate(("W1", "W2")):
        rows = "\n".join(f"2023-{m:02d}-15 | {2.0 + 0.05 * m + i:.2f}" for m in range(1, 13))
        doc = {"doc_id": f"TABLE_{eid}", "doc_date": "2024-01-15",
               "text": f"Widget {eid} history.\n\nmonth | widget_ratio\n{rows}\n\nNOTES: the latest widget_ratio was {2.6 + i:.2f}."}
        p = u / "corpus" / f"TABLE_{eid}.json"
        p.write_text(json.dumps(doc), encoding="utf-8")
        files.append({"path": f"corpus/TABLE_{eid}.json", "role": "corpus", "entity_ids": [eid],
                      "sha256": hashlib.sha256(p.read_bytes()).hexdigest()})
    stale = {"doc_id": "LATE", "doc_date": "2024-03-01", "text": "Widget one ratio printed 9.99."}
    (u / "corpus" / "LATE.json").write_text(json.dumps(stale), encoding="utf-8")
    (u / "manifest.json").write_text(json.dumps({"files": files}), encoding="utf-8")
    return u


def main() -> int:
    from .cli import run

    with tempfile.TemporaryDirectory() as td:
        u = _unit(Path(td))
        out = Path(td) / "out" / "answer.json"
        ans = run(u / "task.json", u / "corpus", out)
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data == ans and data["task_id"] == "selftest-unit"
        assert {r["entity_id"] for r in data["entity_predictions"]} == {"W1", "W2"}
        assert not data["notes"].get("fallback"), data["notes"]
        cited = {c["doc_id"] for r in data["entity_predictions"] for c in r["claims"]}
        assert "LATE" not in cited
        try:
            import importlib.resources as res

            import jsonschema

            schema = json.loads((res.files("qfbench2_common") / "schemas" / "analysis.schema.json").read_text(encoding="utf-8"))
            jsonschema.Draft202012Validator(schema).validate(data)
            print("selftest: schema ok")
        except ImportError:
            print("selftest: toolkit not installed; schema not checked")
    print("selftest: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
