"""Grade the finished units of one or more proxy runs (also while they are still running: a unit
counts once its <unit>.log exists) and print a per-unit comparison table.

    python harness/proxy_compare.py <run dir> [<run dir> ...] [--md]

Grades are cached in <run dir>/grades.json (keyed by unit and the log's mtime).
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import check_units as cu  # noqa: E402

KIT = cu.DEFAULT_KIT


def grade_dir(d: Path) -> dict[str, dict]:
    cache_p = d / "grades.json"
    cache = json.loads(cache_p.read_text(encoding="utf-8")) if cache_p.exists() else {}
    todo = []
    for log in sorted(d.glob("t1-*.log")):
        unit = log.stem
        key = f"{log.stat().st_mtime:.0f}"
        if cache.get(unit, {}).get("key") != key:
            todo.append((unit, key, log))
    if todo:
        lock = Path(os.environ.get("TEMP", "/tmp")) / "jp_check_units.lock"
        while True:
            try:
                lock.mkdir()
                break
            except FileExistsError:
                time.sleep(5)
        try:
            for unit, key, log in todo:
                u = KIT / "units" / unit
                c = cu.run_checker(u, d / unit)
                text = log.read_text(encoding="utf-8", errors="replace")
                m = re.search(r"^JP_SUMMARY (\{.*\})$", text, re.M)
                s = json.loads(m.group(1)) if m else {}
                cache[unit] = {"key": key, "pass": c.get("status") == "pass", "tests": f"{c.get('passed', 0)}/{c.get('total', 0)}",
                               "passed": c.get("passed", 0), "total": c.get("total", 0),
                               "requests": s.get("requests"), "seconds": s.get("seconds"), "chosen": s.get("chosen"),
                               "failing": c.get("failing", [])[:4]}
        finally:
            lock.rmdir()
        cache_p.write_text(json.dumps(cache, indent=1), encoding="utf-8")
    return cache


def main() -> int:
    dirs = [Path(a) for a in sys.argv[1:] if not a.startswith("--")]
    md = "--md" in sys.argv
    grades = {d.name: grade_dir(d) for d in dirs}
    units = sorted(set().union(*[set(g) for g in grades.values()]))
    common = [u for u in units if all(u in g for g in grades.values())]
    names = list(grades)
    if md:
        print("| unit | " + " | ".join(names) + " |")
        print("|---|" + "---|" * len(names))
    for u in units:
        cells = []
        for n in names:
            g = grades[n].get(u)
            cells.append("-" if g is None else f"{'PASS' if g['pass'] else 'fail'} {g['tests']} r{g['requests']} {g['seconds']:.0f}s"
                         if g.get("seconds") is not None else f"{'PASS' if g['pass'] else 'fail'} {g['tests']}")
        print(("| " + u + " | " + " | ".join(cells) + " |") if md else f"{u:50s} " + "  ".join(f"{c:28s}" for c in cells))
    print()
    for n in names:
        g = grades[n]
        cg = [g[u] for u in common]
        print(f"{n:28s} graded {len(g):3d}  pass {sum(x['pass'] for x in g.values()):3d}   on {len(common)} common units: pass {sum(x['pass'] for x in cg):3d}, "
              f"tests {sum(x['passed'] for x in cg)}/{sum(x['total'] for x in cg)}, "
              f"mean req {sum((x['requests'] or 0) for x in cg) / max(1, len(cg)):.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
