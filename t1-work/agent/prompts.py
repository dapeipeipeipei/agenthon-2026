"""Prompt templates. Everything the House model sees is built here.

Design (v3): one ANALYSIS call with thinking ON (deliverable contract + exact method, conventions
and the checks the outputs must pass, as JSON; no code, so the reasoning has the token room),
several independent CODE calls with thinking OFF (a whole script needs the full 4,000-token reply),
REPAIR calls fed with the execution result, a JUDGE call with thinking ON that compares the
candidates' code and outputs where they disagree, and a FIX call (thinking OFF) that applies the
judge's findings. The instruction text is shown verbatim (canary lines stripped) in every call,
with the file inventory, previews and the path mapping.
"""
from __future__ import annotations

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
- Never print or dump large data; print at most a few short progress lines, plus the CHECK lines asked for below.
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


def _notes_block(notes: str | None) -> str:
    if not notes:
        return ""
    return ("=== ANALYSIS OF THIS TASK (from a careful earlier reading; follow it unless it contradicts the task text) ===\n"
            f"{notes}\n\n")


def _deliv_block(out_dir: str, deliverables: list | None) -> str:
    if not deliverables:
        return ""
    return "=== DELIVERABLES (exact output files the task requires) ===\n" + "\n".join(
        f"- {out_dir.rstrip('/')}/{d.name}  [{d.fmt}]  {d.spec}" for d in deliverables) + "\n\n"


ANALYSIS_MARK = "extract the output contract and work out the exact solution method"


def analysis_messages(u: Unit, out_dir: str) -> list[dict]:
    """Thinking ON. The reply (after the reasoning) is one JSON object."""
    user = _task_block(u, out_dir) + f"""
=== YOUR JOB NOW ===
Before any code is written, {ANALYSIS_MARK}. Think it through carefully but efficiently: your reasoning and your answer share one 4000-token budget, so keep the reasoning focused (well under 2500 tokens) and leave room for the answer. Then reply with ONE JSON object and nothing else:

{{
  "deliverables": [
    {{"path": "<absolute path under the output directory, exact file name from the task>",
      "format": "json|csv|parquet|png|html|txt|other",
      "spec": "<one line: exact top-level keys / columns in order / shape / rounding the task requires>"}}
  ],
  "method": ["<step by step: which real input file feeds which step, the exact formulas (write them out), the conventions (day counts, annualisation, ddof, compounding, signs, units, sorting, date alignment, look-ahead rules) and the stated edge-case rules; at most 14 steps>"],
  "pitfalls": ["<at most 8 specific ways a plausible implementation gets this task wrong>"],
  "checks": ["<at most 8 verifiable properties the outputs must satisfy: invariants the task states or implies (e.g. a par bond reprices to 1.0, weights sum to 1, put-call parity, monotonicity) and any example numbers or expected values the task text gives>"]
}}

List every file the task requires as a deliverable (and only those; never reward.json or pytest_report.json): a task often names them only as bare file names ("1. option_values.json", "### File 2: asian_prices.csv") under its output section, and every one of them is required. Use the real input paths from the PATH MAPPING. Keep the JSON under 1200 words."""
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


plan_messages = analysis_messages      # v2 name


def code_messages(u: Unit, out_dir: str, notes: str | None, run_limit: int, deliverables: list | None = None) -> list[dict]:
    user = _task_block(u, out_dir) + "\n" + _deliv_block(out_dir, deliverables) + _notes_block(notes) + f"""=== YOUR JOB NOW ===
Write the complete program that solves the task and writes every deliverable to the output directory. Requirements:
- One self-contained script; no command-line arguments; no user input; no network.
- Read inputs only from the real paths in the PATH MAPPING. Write outputs only to the output directory, with the exact file names the task names.
- Implement the task's formulas and conventions literally; where the task gives example values or invariants, make sure your outputs reproduce them.
- It must run to completion within {run_limit} seconds on 16 CPUs.
- Wrap optional parts so that a failure there still lets every deliverable be written with the best available values (but never replace a required computation with a placeholder).
- At the end, print up to 8 lines of the form `CHECK <name>: <computed value> (expected <what the task implies>)` that test the key properties of your results (invariants, sums, parity relations, example values from the task).
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


def compact_messages(u: Unit, out_dir: str, notes: str | None, run_limit: int, deliverables: list | None = None) -> list[dict]:
    msgs = code_messages(u, out_dir, notes, run_limit, deliverables)
    msgs[-1]["content"] += ("\n\nIMPORTANT: a previous attempt was too long for the reply limit. Write a COMPACT script "
                            "(at most ~180 lines, short names, no comments, no blank-line padding, at most 3 CHECK lines) "
                            "that still implements every requirement.")
    return msgs


REPAIR_MARK = "WHAT HAPPENED WHEN IT RAN"


def repair_messages(u: Unit, out_dir: str, code: str, report: str, run_limit: int, notes: str | None = None,
                    deliverables: list | None = None) -> list[dict]:
    user = _task_block(u, out_dir) + "\n" + _deliv_block(out_dir, deliverables) + _notes_block(notes) + f"""=== CURRENT SCRIPT ===
```python
{code}
```

=== {REPAIR_MARK} ===
{report}

=== YOUR JOB NOW ===
Fix the script so that it runs without error within {run_limit} seconds and writes every deliverable exactly as the task specifies. Fix the root cause (wrong path, wrong column, wrong shape, numerical blow-up, slow loop), not just the symptom; re-check file names, keys and columns against the task text while you are at it. Keep what already works.

Reply with exactly one ```python code block containing the whole corrected script, and no text before or after it."""
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


JUDGE_MARK = "Decide which candidate is correct"


def judge_messages(u: Unit, out_dir: str, cands: list[dict], diff: str, notes: str | None) -> list[dict]:
    """Thinking ON. `cands`: [{"label", "code", "previews", "stdout"}]. Reply: one JSON verdict."""
    blocks = []
    for c in cands:
        blocks.append(f"=== CANDIDATE {c['label']}: SCRIPT ===\n```python\n{c['code']}\n```\n"
                      f"=== CANDIDATE {c['label']}: FILES IT WROTE (previews) ===\n{c['previews']}\n"
                      f"=== CANDIDATE {c['label']}: ITS PRINTED OUTPUT (tail) ===\n{c['stdout'] or '(nothing)'}\n")
    labels = ", ".join(c["label"] for c in cands)
    what = ("One candidate solution ran successfully; audit it." if len(cands) == 1 else
            f"Independent candidate solutions ({labels}) ran successfully but do not all agree.")
    user = _task_block(u, out_dir) + "\n" + _notes_block(notes) + "\n".join(blocks) + f"""
=== WHERE THE OUTPUTS DIFFER ===
{diff}

=== YOUR JOB NOW ===
{what} An automated checker will verify the output files against the task text: exact file names, keys and columns, and numerical values (often within tight tolerances), including invariants and reference numbers. {JUDGE_MARK} by re-deriving the disputed quantities from the task text: check each formula, convention (day counts, annualisation, ddof, compounding, signs, units, rounding, sorting, date alignment), edge-case rule, the output structure, and the CHECK lines. Think carefully but efficiently: your reasoning and your answer share one 4000-token budget.

Then reply with ONE JSON object and nothing else:
{{"best": "<label of the most correct candidate>", "errors": ["<each DEFINITE remaining defect in that candidate, with the task rule it violates and the exact fix>"]}}
Leave "errors" empty unless you can point to the task text (or an impossible value) for each item; never list style, speed or equally valid alternative conventions."""
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


FIX_MARK = "DEFECTS FOUND BY A CAREFUL REVIEW AGAINST THE TASK TEXT"


def fix_messages(u: Unit, out_dir: str, code: str, errors: list[str], run_limit: int, notes: str | None = None,
                 deliverables: list | None = None) -> list[dict]:
    errs = "\n".join(f"- {e}" for e in errors)
    user = _task_block(u, out_dir) + "\n" + _deliv_block(out_dir, deliverables) + _notes_block(notes) + f"""=== CURRENT SCRIPT (it runs) ===
```python
{code}
```

=== {FIX_MARK} ===
{errs}

=== YOUR JOB NOW ===
Correct exactly these defects (and anything that follows directly from them); keep everything else as it is. The script must still finish within {run_limit} seconds and write every deliverable.

Reply with exactly one ```python code block containing the whole corrected script, and no text before or after it."""
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


def notes_from_analysis(obj: dict | None, text: str, limit: int = 9000) -> str | None:
    """The analysis as prompt text: the JSON's method/pitfalls/checks when the reply parsed, else the
    raw reply or (when every token went to thinking) the reasoning itself."""
    if isinstance(obj, dict):
        parts = []
        for key, title in (("method", "Method"), ("pitfalls", "Pitfalls"), ("checks", "Checks the outputs must pass")):
            v = obj.get(key) or (obj.get("plan") if key == "method" else None)
            if isinstance(v, list) and v:
                parts.append(title + ":\n" + "\n".join(f"- {str(x)[:700]}" for x in v[:16]))
            elif isinstance(v, str) and v.strip():
                parts.append(title + ":\n" + v.strip()[:3000])
        if parts:
            return "\n".join(parts)[:limit]
    t = (text or "").strip()
    if len(t) < 80:
        return None
    if len(t) > limit:
        t = "(reasoning notes, truncated)\n" + t[-limit:]
    return t
