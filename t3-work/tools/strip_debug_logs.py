"""Remove `logger.debug(...)` / `logger.info(...)` expression statements from vendored ABIDES.

Why: the baseline runs with the log level at WARNING, so these calls never emit anything, but
their arguments are still evaluated first: `"...".format(order)` renders `Order.__str__`, which
calls `fmt_ts` (a pandas Timestamp round trip) on every order message. On t3-mp01 that is about
1.2 s of a 7.9 s profiled run. Removing the statements changes no simulation state, RNG draw or
output; the statement is replaced by `pass` so block structure is preserved and the diff stays
reviewable (the vendored copy still mirrors upstream line for line elsewhere).

Usage: python strip_debug_logs.py <file.py> [...]   (edits in place, prints counts)
"""

from __future__ import annotations

import ast
import pathlib
import sys

TARGETS = {("logger", "debug"), ("logger", "info")}


def strip(path: pathlib.Path) -> int:
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    spans: list[tuple[int, int, int]] = []  # (lineno, end_lineno, col)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
            continue
        fn = node.value.func
        if (
            isinstance(fn, ast.Attribute)
            and isinstance(fn.value, ast.Name)
            and (fn.value.id, fn.attr) in TARGETS
        ):
            spans.append((node.lineno, node.end_lineno or node.lineno, node.col_offset))
    if not spans:
        return 0
    lines = src.splitlines(keepends=True)
    for lineno, end, col in sorted(spans, reverse=True):
        indent = " " * col
        lines[lineno - 1 : end] = [f"{indent}pass  # [jpsim] debug log statement removed\n"]
    path.write_text("".join(lines), encoding="utf-8")
    ast.parse("".join(lines))  # must still be valid Python
    return len(spans)


if __name__ == "__main__":
    total = 0
    for arg in sys.argv[1:]:
        p = pathlib.Path(arg)
        n = strip(p)
        total += n
        print(f"{p}: {n} statement(s) removed")
    print(f"total {total}")
