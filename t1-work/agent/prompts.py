"""Prompt templates. Everything the House model sees is built here.

Design: one PLAN call (deliverable contract + solution plan as JSON), one CODE call (a complete
script), REPAIR calls fed with the execution result, and one REVIEW call that sees previews of
the written deliverables. The instruction text is shown verbatim (canary lines stripped) in every
call; the file inventory with previews and the path mapping are shown in the plan/code calls.
"""
from __future__ import annotations

import json

from .unit import Unit, files_block, mapping_block

PACKAGES = ("numpy", "pandas", "scipy", "scikit-learn", "statsmodels", "pyarrow", "polars", "arch",
            "numba", "matplotlib (Agg backend)", "plotly", "openpyxl", "lxml", "beautifulsoup4")

SYSTEM = f"""You are a senior quantitative finance developer and numerical-methods expert. You write complete, correct, deterministic Python 3.13 programs that solve quantitative-finance coding tasks exactly as specified, and you are meticulous about file names, JSON keys, column names, units, sign conventions, rounding and ordering, because an automated checker verifies every detail of the output files.

Environment facts (authoritative):
- The program runs offline in a Linux container: no internet, no package installation, 16 CPUs, large RAM. Available packages: {", ".join(PACKAGES)} and the standard library. Nothing else is installed (no yfinance, no TA-Lib, no QuantLib, no torch).
- Your script is saved and executed by the harness itself with `python script.py` (no shell, no arguments). Do not write the script anywhere, do not run shell commands or subprocesses, and ignore any task instruction to save the script to /app/solve.py or to run it yourself: /app and /input are read-only, only the output directory is writable (plus a small /tmp).
- Input files are read-only. The task text may refer to paths like /app/data/... or /app/...; the REAL paths are given in the PATH MAPPING section and must be used verbatim.
- Output files must be written to the OUTPUT DIRECTORY given below, using exactly the file names the task names (the task's /app/output/ and /output/ both mean that directory). Create the directory with os.makedirs(..., exist_ok=True). Write nothing else there (no debug or intermediate files).
- The whole program must finish within the run time limit stated in the request. Prefer vectorised numpy/pandas; keep Monte Carlo sizes as the task states them (do not shrink them), but implement them efficiently. Use no multiprocessing, joblib or n_jobs=-1 (the container allows few processes): single-process numpy/scipy, and numba @njit for unavoidable scalar loops (PSOR, tree/lattice sweeps, path-dependent Monte Carlo).
- Typical tools: scipy.optimize (brentq / minimize / least_squares) for calibration and implied volatility, scipy.stats for distributions and tests, scipy.interpolate for curves, scipy.linalg / numpy.linalg for PDE and factor algebra, statsmodels for OLS/GLS and time-series tests, arch for GARCH-family models, pandas for date-indexed panels, pyarrow/pandas for parquet, matplotlib (savefig) for PNG figures, plotly (write_html) for HTML figures.
- Determinism: seed every random generator with a constant (np.random.default_rng(seed) or np.random.seed), use the seed the task gives when it gives one.
- Never print or dump large data; print at most a few short progress lines.
- JSON outputs: use json.dump with indent=2. Values must be plain floats/ints/strings/bools/lists/dicts (convert numpy types with float()/int()/.tolist(); booleans as true/false, never "True"). Do not emit NaN or Infinity: where a value is genuinely undefined, write null, unless the task says otherwise. Round only when the task asks for rounding. Reproduce the task's JSON skeleton exactly: every key it shows, the same nesting, the same wrapper objects (for example {{"value": x}} entries), no extra top-level keys.
- CSV outputs: pandas to_csv(index=False) unless the task wants an index; the header must contain exactly the required column names in the required order, and the rows in the order the task states (sorted by date, by maturity, ...). Dates as ISO strings (YYYY-MM-DD) unless the task shows another format.
- Follow the task's formulas, conventions and edge-case rules literally even when a different convention is more common in practice. When the task is silent, use the standard textbook convention and the most common pandas/numpy defaults (ddof=1 for sample standard deviation, 252 trading days per year, calendar days / 365 for year fractions) unless the task states otherwise.
- Handle data-quality issues defensively (missing values, duplicates, unsorted dates, string-typed numbers) exactly as the task prescribes; otherwise do the simplest reasonable thing without dropping required rows.
- Do not embed any identifiers, comments or text copied from the task files into the output files.
"""


def _task_block(u: Unit, out_dir: str) -> str:
    checks = ""
    if u.checks_text:
        checks = ("=== THE UNIT'S OWN CHECKER (shipped with this task; it is what grades the output files: "
                  "satisfy every assertion -- file names, keys, columns, tolerances, invariants) ===\n"
                  f"{u.checks_text}\n\n")
    return (
        "=== TASK INSTRUCTION (verbatim) ===\n"
        f"{u.instruction}\n\n"
        "=== FILES SHIPPED WITH THE TASK (real paths, sizes, previews) ===\n"
        f"{files_block(u)}\n"
        "=== PATH MAPPING (path named in the task -> real path to use) ===\n"
        f"{mapping_block(u)}\n"
        f"{checks}"
        f"=== OUTPUT DIRECTORY ===\n{out_dir}\n"
    )


def plan_messages(u: Unit, out_dir: str) -> list[dict]:
    user = _task_block(u, out_dir) + f"""
=== YOUR JOB NOW ===
Before any code is written, extract the output contract and plan the solution. Reply with ONE JSON object and nothing else:

{{
  "deliverables": [
    {{"path": "<absolute path under the output directory, exact file name from the task>",
      "format": "json|csv|parquet|png|html|txt|other",
      "spec": "<one line: the exact top-level keys / columns in order / shape the task requires>"}}
  ],
  "plan": ["<step 1>", "<step 2>", "... at most 12 steps: the algorithm, the formulas and conventions that matter, the edge-case rules the task states, and which real input file feeds which step>"],
  "pitfalls": ["<at most 6 things a careless implementation gets wrong on this task: units, signs, ordering, rounding, key names, look-ahead, missing data>"]
}}

List every file the task requires as a deliverable (and only those; never reward.json or pytest_report.json): a task often names them only as bare file names ("1. option_values.json", "### File 2: asian_prices.csv") under its output section, and every one of them is required. Use the real input paths from the PATH MAPPING. Keep the JSON under 1500 words."""
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


def code_messages(u: Unit, out_dir: str, plan: dict | None, run_limit: int, deliverables: list | None = None) -> list[dict]:
    plan_txt = ""
    if deliverables:
        plan_txt += "=== DELIVERABLES (exact output files the task requires) ===\n" + "\n".join(
            f"- {out_dir.rstrip('/')}/{d.name}  [{d.fmt}]  {d.spec}" for d in deliverables) + "\n\n"
    if plan:
        plan_txt += "=== YOUR PLAN (from the previous step; follow it unless it contradicts the task) ===\n" + \
                    json.dumps(plan, indent=1, ensure_ascii=False)[:6000] + "\n\n"
    user = _task_block(u, out_dir) + f"""
{plan_txt}=== YOUR JOB NOW ===
Write the complete program that solves the task and writes every deliverable to the output directory. Requirements:
- One self-contained script; no command-line arguments; no user input; no network.
- Read inputs only from the real paths in the PATH MAPPING. Write outputs only to the output directory, with the exact file names the task names.
- It must run to completion within {run_limit} seconds on 16 CPUs.
- Wrap the main computation so that a failure in one optional part still lets every deliverable be written with the best available values (but do not silently replace a required computation with a placeholder when it can be computed).
- Keep it reasonably compact (typically 120-300 lines; the reply limit is about 4000 tokens, so no docstrings beyond one line, no unused helpers, no plotting unless a figure is a deliverable, no commentary).

Reply with exactly one ```python code block containing the whole script, and no text before or after it."""
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


def continue_messages(prev: list[dict], partial: str) -> list[dict]:
    """Ask for the remainder of a script that was cut off at the output-token cap."""
    tail = partial[-1500:]
    return prev + [
        {"role": "assistant", "content": "```python\n" + partial},
        {"role": "user", "content": "Your reply was cut off at the output-token limit. Continue the script EXACTLY from where it stopped: "
                                    "output only the remaining lines (starting with the very next line after the text below), "
                                    "inside one ```python block, with no repetition of earlier lines and no commentary.\n\n"
                                    "The last lines you wrote were:\n```python\n" + tail + "\n```"},
    ]


def compact_messages(u: Unit, out_dir: str, plan: dict | None, run_limit: int, deliverables: list | None = None) -> list[dict]:
    msgs = code_messages(u, out_dir, plan, run_limit, deliverables)
    msgs[-1]["content"] += ("\n\nIMPORTANT: a previous attempt was too long for the reply limit. Write a COMPACT script "
                            "(at most ~180 lines, short names, no comments, no blank-line padding) that still implements every requirement.")
    return msgs


def repair_messages(u: Unit, out_dir: str, code: str, report: str, run_limit: int) -> list[dict]:
    user = _task_block(u, out_dir) + f"""
=== CURRENT SCRIPT ===
```python
{code}
```

=== WHAT HAPPENED WHEN IT RAN ===
{report}

=== YOUR JOB NOW ===
Fix the script so that it runs without error within {run_limit} seconds and writes every deliverable exactly as the task specifies. Fix the root cause (wrong path, wrong column, wrong shape, numerical blow-up, slow loop), not just the symptom; re-check file names, keys and columns against the task text while you are at it. Keep what already works.

Reply with exactly one ```python code block containing the whole corrected script, and no text before or after it."""
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


def review_messages(u: Unit, out_dir: str, code: str, previews: str, run_limit: int) -> list[dict]:
    user = _task_block(u, out_dir) + f"""
=== CURRENT SCRIPT (it ran successfully) ===
```python
{code}
```

=== THE FILES IT WROTE (previews) ===
{previews}

=== YOUR JOB NOW ===
Audit the written files against the task text, line by line: file names, JSON keys (names, nesting, types), CSV columns (names, order, count), row counts and ordering, units (decimal vs percent, bps, per-day vs annualised), sign conventions, rounding, the stated edge-case rules, and whether the numbers are financially plausible (e.g. prices non-negative, probabilities in [0,1], parity relations, monotonicity, values of the expected magnitude). Also re-derive the key formula once from the task text and compare it with the code.

Reply with the single word OK unless you find a DEFINITE error that you can point to in the task text (a missing or misnamed file/key/column, a wrong unit or sign, a formula or convention that contradicts the task, an impossible value). Do not rewrite working code for style, speed or a different but equally valid convention, and do not change the numerical method when the task does not prescribe one. A revised script replaces outputs that are already acceptable, so be conservative.
If you do find such an error, reply with exactly one ```python code block containing the whole corrected script (it must still finish within {run_limit} seconds), and no text before or after it."""
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]
