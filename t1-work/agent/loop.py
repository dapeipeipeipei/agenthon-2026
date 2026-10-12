"""The agent loop (v3): analyse || candidates -> run/repair each -> agree or judge -> fix -> finalize.

v1 on the Development platform (run 970099): 86/86 units exited 0 with their output accepted, but
77 were `domain_gate_failed` -- wrong answers, not crashes or time. v3 therefore spends more of the
unit's House allowance and time on getting the numbers right:

1. ANALYSIS (thinking ON, one call): deliverable contract + exact method + checks, as JSON. Runs in
   parallel with candidate 0, which is written straight from the task (thinking OFF).
2. Candidates 1..K-1 (thinking OFF) are written with the analysis in the prompt.
3. Every candidate runs in its own workspace (`_jp_c<i>` under the output directory) and is repaired
   from its execution report (bounded rounds). The first candidate that runs clean is promoted to
   the output directory at once (a draft); later choices replace it.
4. Clean candidates are compared value by value (`compare.py`). Unanimous -> done (early finish).
   Otherwise (or a single clean candidate) a JUDGE call (thinking ON) picks the best candidate and
   lists definite defects; a FIX call (thinking OFF) applies them; the fixed script replaces the
   pick only if it runs clean.
5. Finalize always: workspaces removed, stubs for missing deliverables, output tree sanitised.

v3f (Final build): up to 5 parallel candidates, a request reserve kept for the judge, and up to
two judge -> fix rounds (the second only when the fix changed the outputs, to adjudicate old vs new).

Time (see `unit_budget`): one hard deadline

    budget = min(stage_cap, card_timeout - max(JP_CARD_MARGIN_SEC=120, 0.10 * card_timeout))
    stage_cap = (JP_STAGE_SEC=86400 - JP_STAGE_RESERVE_SEC=1800) / JP_ROSTER_ASSUME=150 - JP_UNIT_OVERHEAD_SEC=30

(JP_UNIT_BUDGET_SEC, when set, replaces stage_cap) and a soft deadline min(JP_SOFT_BUDGET_SEC, 0.72 *
budget) after which no optional stage starts. The Final gives each unit 600-2400 s on its card and
the whole run 24 h; the roster size is unpublished and nothing tells a running unit how much stage
time is left (track1#31, hub#2), so the cap assumes up to 150 units all hitting it. Requests: at most
JP_MAX_REQUESTS (default 24 of the 25 the House admits).
"""
from __future__ import annotations

import ast
import json
import threading
import time
from pathlib import Path

from . import house, prompts
from .compare import agreement, clear_workspaces, diff_table, fingerprint, promote, workspace
from .outputs import (Contract, Deliverable, check_deliverables, contract_from_model, preview_outputs,
                      rewrite_paths, sanitize_tree, tree_summary, write_stub)
from .runner import RunResult, run_script
from .unit import Unit, compact_unit, load_unit

FINAL_RESERVE_S = 15.0        # stubs + sanitation
RUN_RESERVE_S = 20.0          # a run must leave at least this much before the deadline
TOO_BIG = ("http_400", "http_413", "http_422")   # refused before admission (prompt does not fit the context window)
UNANIMOUS = 0.97
LABELS = "ABCDEFGH"


def stage_cap() -> float:
    """Per-unit cap so that the whole Final roster fits the stage clock even if every unit hits it."""
    stage = house.env_float("JP_STAGE_SEC", 86400.0)
    reserve = house.env_float("JP_STAGE_RESERVE_SEC", 1800.0)      # image pull + platform slack
    roster = max(1.0, house.env_float("JP_ROSTER_ASSUME", 150.0))
    overhead = house.env_float("JP_UNIT_OVERHEAD_SEC", 30.0)       # container create/teardown per unit
    return max(120.0, (stage - reserve) / roster - overhead)


def unit_budget(card_timeout: float, c: dict | None = None) -> float:
    c = c or cfg()
    margin = max(c["card_margin"], 0.10 * card_timeout)
    return max(60.0, min(c["unit_budget"], card_timeout - margin))


def cfg() -> dict:
    k = max(1, min(5, house.env_int("JP_CANDIDATES", 5)))
    return {
        "unit_budget": house.env_float("JP_UNIT_BUDGET_SEC", stage_cap()),
        "soft_budget": house.env_float("JP_SOFT_BUDGET_SEC", 420.0),
        "soft_frac": house.env_float("JP_SOFT_FRAC", 0.72),
        "card_margin": house.env_float("JP_CARD_MARGIN_SEC", 120.0),
        "max_requests": max(1, min(24, house.env_int("JP_MAX_REQUESTS", 24))),
        "judge_reserve": house.env_int("JP_JUDGE_RESERVE", 4),
        "judge_rounds": max(0, min(3, house.env_int("JP_JUDGE_ROUNDS", 2))),
        "call_timeout": house.env_float("JP_CALL_TIMEOUT_SEC", 150.0),
        "think_timeout": house.env_float("JP_THINK_TIMEOUT_SEC", 200.0),
        "run_timeout": house.env_float("JP_RUN_TIMEOUT_SEC", 180.0),
        "cand_repairs": house.env_int("JP_CAND_REPAIRS", 2),
        "max_repairs": house.env_int("JP_MAX_REPAIRS", 6),
        "candidates": k,
        "think": house.env_int("JP_THINK", 1) == 1,
        "judge": house.env_int("JP_JUDGE", 1) == 1,
        "mem_gib": house.env_float("JP_MEM_GIB", min(24.0, 100.0 / k)),     # k scripts may run at once (128 GiB unit)
        "threads": house.env_int("JP_THREADS", max(3, 16 // k)),
    }


class Cand:
    def __init__(self, idx: int, label: str, with_notes: bool) -> None:
        self.idx = idx
        self.label = label
        self.with_notes = with_notes
        self.dir: Path | None = None
        self.src: str | None = None          # the script as the model wrote it (shown back to the model)
        self.res: RunResult | None = None
        self.problems: list[str] = ["did not run"]
        self.started = 0.0
        self.rounds = 0

    @property
    def clean(self) -> bool:
        return self.dir is not None and self.res is not None and not self.problems


class Solver:
    def __init__(self, task_dir: Path, out_dir: Path, scratch: Path, t0: float) -> None:
        self.c = cfg()
        self.task_dir = task_dir
        self.out_dir = out_dir
        self.scratch = scratch
        self.t0 = t0
        self.events: list[str] = []
        self.unit: Unit | None = None
        self.pu: Unit | None = None       # the unit as shown in prompts (compact copy after a TOO_BIG refusal)
        self.lock = threading.RLock()
        self.contract = Contract()
        self.deadline = t0 + self.c["unit_budget"]
        self.soft_deadline = self.deadline
        self.client: house.Client | None = None
        self.notes: str | None = None
        self.notes_ready = threading.Event()
        self.abort = False
        self.cands: list[Cand] = []
        self.promoted: Cand | None = None
        self.closed = False
        self.last_problems: list[str] = ["nothing ran"]
        self.stage_log: list[str] = []
        self.finalized = False
        self.arm_alarm = None       # set by cli on POSIX: re-arm the hard alarm to the final budget

    # ---- helpers
    def log(self, msg: str) -> None:
        line = f"[{time.monotonic() - self.t0:6.1f}s] {msg}"
        with self.lock:
            self.events.append(line)
        print(line, flush=True)

    def remaining(self) -> float:
        return self.deadline - time.monotonic()

    def soft_left(self) -> float:
        return self.soft_deadline - time.monotonic()

    @property
    def out_posix(self) -> str:
        return self.out_dir.as_posix()

    def run_limit(self) -> int:
        return int(max(30.0, min(self.c["run_timeout"], self.remaining() - RUN_RESERVE_S)))

    def think_kw(self) -> dict:
        if self.c["think"]:
            return {"think": True, "temperature": house.THINK_TEMPERATURE, "timeout": self.c["think_timeout"]}
        return {"think": False, "temperature": house.TEMPERATURE}

    # ---- main
    def run(self) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.unit = load_unit(self.task_dir)
        u = self.pu = self.unit
        budget = unit_budget(u.timeout_sec, self.c)
        self.deadline = self.t0 + budget
        self.soft_deadline = self.t0 + min(self.c["soft_budget"], self.c["soft_frac"] * budget)
        if self.arm_alarm is not None:
            try:
                self.arm_alarm(budget)
            except Exception:  # noqa: BLE001
                pass
        self.contract = contract_from_model(None, u, self.out_posix)
        self.log(f"unit {u.unit_id}: card timeout {u.timeout_sec:.0f}s, budget {budget:.0f}s (soft {self.soft_deadline - self.t0:.0f}s), "
                 f"{len(u.files)} data files, {len(u.canaries)} canary ids, mapping {len(u.mapping)} paths, "
                 f"text deliverables={[d.name for d in self.contract.deliverables]}")
        if not house.configured():
            self.log("House route not configured (MODEL_ENDPOINT/MODEL_TOKEN/MODEL_NAME missing): offline fallback")
            return
        self.client = house.Client(deadline=self.deadline - FINAL_RESERVE_S, max_requests=self.c["max_requests"],
                                   call_timeout=self.c["call_timeout"])
        k = self.c["candidates"]
        self.cands = [Cand(i, LABELS[i], with_notes=(k == 1 or i > 0)) for i in range(k)]
        threads = [threading.Thread(target=self._guard, args=(self.analyse,), daemon=True)]
        threads += [threading.Thread(target=self._guard, args=(self.worker, c), daemon=True) for c in self.cands]
        for t in threads:
            t.start()
        for t in threads:
            t.join(max(0.5, self.remaining() - FINAL_RESERVE_S))
        if self.abort or self.remaining() < FINAL_RESERVE_S + 5:
            return
        self.select()

    def _guard(self, fn, *args) -> None:
        try:
            fn(*args)
        except Exception as exc:  # noqa: BLE001
            self.log(f"{getattr(fn, '__name__', 'stage')} raised {type(exc).__name__}: {str(exc)[:200]}")
        finally:
            if fn == self.analyse:
                self.notes_ready.set()

    # ---- stage 1: analysis
    def analyse(self) -> None:
        cl = self.client
        kw = self.think_kw()
        r = cl.chat(prompts.analysis_messages(self.pu, self.out_posix), max_tokens=house.MAX_OUTPUT_TOKENS, tag="analysis", **kw)
        if r.status in TOO_BIG and self.shrink_prompts():
            r = cl.chat(prompts.analysis_messages(self.pu, self.out_posix), max_tokens=house.MAX_OUTPUT_TOKENS, tag="analysis+compact", **kw)
        if r.status in ("http_401", "http_403"):
            self.log("House refuses our credentials; nothing more to do")
            self.abort = True
            return
        obj = house.extract_json(r.content) if r.ok else None
        if obj is not None and not isinstance(obj.get("deliverables"), list) and not obj.get("method"):
            obj = None
        if obj is not None:
            c = contract_from_model(obj, self.unit, self.out_posix)
            if c.deliverables:
                with self.lock:
                    self.contract = c
        body = house.strip_think(r.content or "").strip() if r.ok else ""
        self.notes = prompts.notes_from_analysis(obj, body if len(body) >= 80 else r.reasoning) if r.ok else None
        self.log(f"analysis: {r.status} ({r.seconds:.0f}s, {r.completion_tokens} tok, finish={r.finish}, reasoning {len(r.reasoning)} ch); "
                 f"json={'yes' if obj else 'no'}, notes {len(self.notes or '')} ch, contract={self.contract.source} "
                 f"{[d.name for d in self.contract.deliverables]}")

    def shrink_prompts(self) -> bool:
        with self.lock:
            if self.pu is not None and self.pu is not self.unit:
                return False
            self.pu = compact_unit(self.unit)
        self.log("prompt refused as too large; switching to compact previews")
        return True

    # ---- stage 2/3: one candidate, written, run and repaired
    def worker(self, cand: Cand) -> None:
        if cand.with_notes:
            self.notes_ready.wait(max(1.0, self.remaining() - 60.0))
        if self.abort:
            return
        notes = self.notes if cand.with_notes else None
        temp = (house.TEMPERATURE, house.TEMPERATURE, 0.6, 0.35, 0.8)[min(cand.idx, 4)]
        msgs = prompts.code_messages(self.pu, self.out_posix, notes, self.run_limit(), self.contract.deliverables)
        src = self.get_code(msgs, tag=f"code{cand.label}", temperature=temp, notes=notes)
        if not src:
            return
        self.try_cand(cand, src, tag=f"cand{cand.label}")
        while cand.problems and cand.rounds < self.c["cand_repairs"]:
            if self.soft_left() <= 0 and self.any_clean():
                break
            if self.any_clean() and self.c["judge"] and self.client.left <= self.c["judge_reserve"]:
                break          # keep requests for the judge -> fix stage
            if not self.client.can_call(reserve=self.client.expected_call_seconds() + RUN_RESERVE_S + 20.0):
                break
            new = self.get_code(prompts.repair_messages(self.pu, self.out_posix, cand.src, self.report(cand.res, cand.problems),
                                                        self.run_limit(), notes, self.contract.deliverables),
                                tag=f"repair{cand.label}{cand.rounds + 1}", temperature=temp, notes=notes)
            if not new:
                break
            cand.rounds += 1
            self.try_cand(cand, new, tag=f"repair{cand.label}{cand.rounds}")

    def any_clean(self) -> bool:
        return any(c.clean for c in self.cands)

    def get_code(self, messages: list[dict], *, tag: str, temperature: float = house.TEMPERATURE,
                 notes: str | None = None) -> str | None:
        """A complete script from one logical call; continuation / compact retry when truncated."""
        cl = self.client
        r = cl.chat(messages, max_tokens=house.MAX_OUTPUT_TOKENS, tag=tag, temperature=temperature)
        if r.status in TOO_BIG and tag.startswith("code") and cl.can_call(reserve=RUN_RESERVE_S + 20):
            self.shrink_prompts()
            messages = prompts.compact_messages(self.pu, self.out_posix, notes, self.run_limit(), self.contract.deliverables)
            r = cl.chat(messages, max_tokens=house.MAX_OUTPUT_TOKENS, tag=tag + "+small", temperature=temperature)
        if not r.ok:
            self.log(f"{tag}: no reply ({r.status})")
            return None
        code = house.extract_code(r.content)
        self.log(f"{tag}: {r.status} {r.seconds:.0f}s {r.completion_tokens} tok finish={r.finish} code={'yes' if code else 'no'}"
                 + (" TRUNCATED" if r.truncated else ""))
        if code and not r.truncated and self.compiles(code):
            return code
        if r.truncated and code and cl.can_call(reserve=RUN_RESERVE_S + 20):
            r2 = cl.chat(prompts.continue_messages(messages, code), max_tokens=house.MAX_OUTPUT_TOKENS, tag=tag + "+cont", temperature=temperature)
            if r2.ok:
                more = house.extract_code(r2.content) or ""
                joined = self.join_continuation(code, more)
                self.log(f"{tag}+cont: {r2.status} {r2.seconds:.0f}s finish={r2.finish} compiles={self.compiles(joined)}")
                if self.compiles(joined):
                    return joined
        if tag.startswith("code") and cl.can_call(reserve=RUN_RESERVE_S + 20):
            r3 = cl.chat(prompts.compact_messages(self.pu, self.out_posix, notes, self.run_limit(), self.contract.deliverables),
                         max_tokens=house.MAX_OUTPUT_TOKENS, tag=tag + "+compact", temperature=temperature)
            if r3.ok:
                c3 = house.extract_code(r3.content)
                self.log(f"{tag}+compact: {r3.status} {r3.seconds:.0f}s finish={r3.finish} code={'yes' if c3 else 'no'}")
                if c3 and self.compiles(c3):
                    return c3
        if code and self.compiles(code):
            return code          # truncated but compiles: partial output beats nothing
        return None

    @staticmethod
    def compiles(code: str) -> bool:
        try:
            ast.parse(code)
            return True
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    def join_continuation(head: str, more: str) -> str:
        """Glue a continuation onto the truncated head, dropping lines the model repeated."""
        h_lines = head.rstrip("\n").split("\n")
        m_lines = more.split("\n")
        best = 0
        for k in range(min(12, len(h_lines), len(m_lines)), 0, -1):
            if [x.rstrip() for x in h_lines[-k:]] == [x.rstrip() for x in m_lines[:k]]:
                best = k
                break
        if best:
            return "\n".join(h_lines + m_lines[best:]) + "\n"
        return head.rstrip("\n") + ("\n" if more[:1] not in (" ", "\t", ")", "]", "}", ",", ".") else "") + more + "\n"

    def runnable(self, src: str, ws: Path) -> str:
        """The script as it runs: the output directory -> this candidate's workspace, the task's
        image paths -> the real input paths."""
        ws_posix = ws.as_posix()
        code = src
        if self.out_posix.rstrip("/") not in ("/app/output", "/output"):
            code = code.replace(self.out_posix.rstrip("/"), ws_posix)
        return rewrite_paths(code, self.unit, ws_posix)

    def try_cand(self, cand: Cand, src: str, *, tag: str) -> None:
        ws = workspace(self.out_dir, f"c{cand.idx}")      # fresh: no stale file can pass for a new one
        cand.dir, cand.src = ws, src
        limit = self.run_limit()
        if limit <= 30 and self.remaining() < RUN_RESERVE_S + 10:
            cand.problems = ["no time left to run"]
            return
        cand.started = time.time()
        res = run_script(self.runnable(src, ws), self.scratch, ws, timeout=limit, mem_gib=self.c["mem_gib"],
                         threads=self.c["threads"], name=f"cand_{cand.idx}.py")
        cand.res = res
        problems = check_deliverables(ws, self.contract.deliverables, since=cand.started)
        if not res.ok:
            problems = [res.report()] + problems
        cand.problems = problems
        self.log(f"{tag}: run {'ok' if res.ok else 'FAILED'} in {res.seconds:.0f}s (limit {limit}s); problems={len(problems)}"
                 + (": " + problems[0][:160].replace("\n", " | ") if problems else ""))
        if not problems:
            with self.lock:
                if self.promoted is None:
                    self._promote(cand, "first clean candidate (draft)")

    def _promote(self, cand: Cand, why: str) -> None:
        with self.lock:
            if self.closed or cand.dir is None:
                return
            n = promote(cand.dir, self.out_dir)
            self.promoted = cand
            self.last_problems = list(cand.problems)
        self.log(f"promoted {cand.label} ({n} files): {why}")

    @staticmethod
    def report(res: RunResult | None, problems: list[str]) -> str:
        parts = []
        if res is not None:
            parts.append(res.report())
        extra = [p for p in problems if res is None or p != res.report()]
        if extra:
            parts.append("Deliverable check:\n- " + "\n- ".join(extra))
        return "\n\n".join(parts)[:6000]

    # ---- stage 4: choose, judge, fix
    def recheck(self) -> None:
        for c in self.cands:
            if c.dir is None or c.res is None:
                continue
            probs = check_deliverables(c.dir, self.contract.deliverables, since=c.started)
            c.problems = ([c.res.report()] if not c.res.ok else []) + probs

    def select(self) -> None:
        self.recheck()
        clean = [c for c in self.cands if c.clean]
        if not clean:
            ran = [c for c in self.cands if c.dir is not None and c.src]
            if not ran:
                self.log("no candidate produced a runnable script")
                return
            best = min(ran, key=lambda c: (len(c.problems), not c.with_notes, c.idx))
            self._promote(best, "best of the failing candidates")
            self.repair_more(best)
            if not best.clean:
                return
            clean = [best]
        names = [d.name for d in self.contract.deliverables]
        fps = {c.label: fingerprint(c.dir, names) for c in clean}
        chosen = clean[0]
        unanimous = False
        score: dict[str, float] = {}
        if len(clean) >= 2:
            score = {c.label: 0.0 for c in clean}
            worst = 1.0
            for i, a in enumerate(clean):
                for b in clean[i + 1:]:
                    s = agreement(fps[a.label], fps[b.label])
                    score[a.label] += s
                    score[b.label] += s
                    worst = min(worst, s)
                    self.stage_log.append(f"agree {a.label}-{b.label} {s:.3f}")
            chosen = max(clean, key=lambda c: (round(score[c.label], 3), c.with_notes, -c.idx))
            unanimous = worst >= UNANIMOUS
            self.log("agreement: " + ", ".join(self.stage_log[-6:]) + f" -> {chosen.label}" + (" (unanimous)" if unanimous else ""))
        if self.promoted is not chosen:
            self._promote(chosen, "most agreed-upon clean candidate")
        if unanimous or not self.c["judge"]:
            return
        # judge -> fix rounds. Round 1 audits the clean candidates (most agreed-upon first); a later
        # round runs only when the previous fix changed the outputs, and adjudicates old vs fixed.
        ranked = [chosen] + sorted((c for c in clean if c is not chosen), key=lambda c: (-score.get(c.label, 0.0), c.idx))             if len(clean) >= 2 else [chosen]
        shown = ranked[:4]
        fix_labels = iter("FGH")
        for rnd in range(1, self.c["judge_rounds"] + 1):
            got = self.judge_and_fix(rnd, chosen, shown, fps, next(fix_labels, "H"))
            if got is None:
                return
            fixed, base = got
            fps[fixed.label] = fingerprint(fixed.dir, names)
            same = agreement(fps[fixed.label], fps[base.label]) >= 0.999
            self.stage_log.append(f"fix{rnd} {fixed.label} vs {base.label} {'same' if same else 'changed'}")
            prev, chosen = base, fixed
            if same:
                return
            shown = [prev, fixed]

    def judge_and_fix(self, rnd: int, chosen: Cand, shown: list[Cand], fps: dict, fix_label: str) -> tuple[Cand, Cand] | None:
        """One judge call (thinking ON) over `shown`, then one fix call on its pick. Returns the fixed
        candidate when it ran clean and was promoted, else None (the current best stays in place)."""
        cl = self.client
        need = max(90.0, cl.expected_call_seconds()) + cl.expected_call_seconds() + RUN_RESERVE_S + 30.0
        if self.soft_left() <= 0 or cl.left < 2 or not cl.can_call(reserve=need):
            self.log(f"skip judge{rnd}: soft {self.soft_left():.0f}s, hard {self.remaining():.0f}s, {cl.left} requests left")
            return None
        shown = sorted(shown, key=lambda c: c.idx)
        diff = diff_table({c.label: fps[c.label] for c in shown}) if len(shown) >= 2 else "(only one candidate ran successfully)"
        payload = [{"label": c.label, "code": c.src, "previews": preview_outputs(c.dir, self.contract.deliverables, budget=5000),
                    "stdout": (c.res.stdout_tail if c.res else "")[-1500:]} for c in shown]
        r = cl.chat(prompts.judge_messages(self.pu, self.out_posix, payload, diff, self.notes), max_tokens=house.MAX_OUTPUT_TOKENS,
                    tag=f"judge{rnd}", **self.think_kw())
        verdict = house.extract_json(r.content) if r.ok else None
        if not isinstance(verdict, dict):
            self.log(f"judge{rnd}: {r.status} ({r.seconds:.0f}s, finish={r.finish}): no verdict; keeping {chosen.label}")
            return None
        best_label = str(verdict.get("best") or "").strip().upper()[:1]
        errors = [str(e)[:800] for e in (verdict.get("errors") or []) if str(e).strip()][:8]
        pick = next((c for c in shown if c.label == best_label), None)
        self.log(f"judge{rnd}: {r.status} ({r.seconds:.0f}s): best={best_label or '?'} errors={len(errors)}")
        if pick is not None and pick is not chosen:
            chosen = pick
            self._promote(chosen, f"judge{rnd}'s pick")
        if not errors:
            return None
        need = cl.expected_call_seconds() + RUN_RESERVE_S + 15.0
        if not cl.can_call(reserve=need):
            self.log(f"skip fix{rnd}: not enough time/requests")
            return None
        src = self.get_code(prompts.fix_messages(self.pu, self.out_posix, chosen.src, errors, self.run_limit(), self.notes,
                                                 self.contract.deliverables), tag=f"fix{rnd}")
        if not src:
            return None
        fixed = Cand(9 + rnd, fix_label, with_notes=True)
        self.cands.append(fixed)
        self.try_cand(fixed, src, tag=f"fix{rnd}-run")
        if not fixed.clean and fixed.src and cl.can_call(reserve=cl.expected_call_seconds() + RUN_RESERVE_S + 20.0)                 and self.soft_left() > 0:
            new = self.get_code(prompts.repair_messages(self.pu, self.out_posix, fixed.src, self.report(fixed.res, fixed.problems),
                                                        self.run_limit(), self.notes, self.contract.deliverables), tag=f"fix{rnd}-repair")
            if new:
                fixed.rounds += 1
                self.try_cand(fixed, new, tag=f"fix{rnd}-repair-run")
        if fixed.clean:
            self._promote(fixed, f"judge{rnd}'s defects fixed")
            return fixed, chosen
        self.log(f"fix{rnd}: the corrected script did not run clean; kept " + chosen.label)
        return None

    def repair_more(self, cand: Cand) -> None:
        rounds = 0
        while cand.problems and rounds < self.c["max_repairs"]:
            if not self.client.can_call(reserve=self.client.expected_call_seconds() + RUN_RESERVE_S + 25.0):
                self.log(f"stop repairing: {self.client.left} requests left, {self.remaining():.0f}s left")
                break
            rounds += 1
            notes = self.notes if cand.with_notes else None
            new = self.get_code(prompts.repair_messages(self.pu, self.out_posix, cand.src, self.report(cand.res, cand.problems),
                                                        self.run_limit(), notes, self.contract.deliverables), tag=f"late-repair{rounds}")
            if not new:
                break
            self.try_cand(cand, new, tag=f"late-repair{rounds}")
            if cand.clean:
                self._promote(cand, "repaired")
            elif cand.res is not None and cand.res.ok:
                self._promote(cand, "repaired run (deliverable problems remain)")

    # ---- finalization (always runs)
    def finalize(self) -> dict:
        with self.lock:
            if self.finalized:
                return {}
            self.finalized = True
            self.closed = True
        log: list[str] = []
        try:
            self.out_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        try:
            if self.promoted is None:
                # nothing promoted yet (alarm / early exit): keep the best partial result, if any
                ran = [c for c in self.cands if c.dir is not None and c.dir.is_dir() and any(c.dir.iterdir())]
                if ran:
                    best = min(ran, key=lambda c: (len(c.problems), c.idx))
                    n = promote(best.dir, self.out_dir)
                    self.last_problems = list(best.problems)
                    log.append(f"late promote {best.label} ({n} files)")
        except Exception as exc:  # noqa: BLE001
            log.append(f"late promote failed: {exc!r}")
        clear_workspaces(self.out_dir)
        names = set()
        try:
            if not self.contract.deliverables and self.unit is not None:
                self.contract = contract_from_model(None, self.unit, self.out_posix)
            if not self.contract.deliverables:
                self.contract.deliverables = [Deliverable("results.json", "json")]
            for d in self.contract.deliverables:
                names.add(d.name)
                p = self.out_dir / d.name
                if not p.is_file() or p.stat().st_size == 0:
                    write_stub(self.out_dir, d)
                    log.append(f"stub: {d.name}")
        except Exception as exc:  # noqa: BLE001
            log.append(f"stub pass failed: {exc!r}")
        try:
            sanitize_tree(self.out_dir, self.unit.canaries if self.unit else [], keep_names=names, log=log)
        except Exception as exc:  # noqa: BLE001
            log.append(f"sanitize failed: {exc!r}")
        summary = {
            "unit": self.unit.unit_id if self.unit else self.task_dir.name,
            "seconds": round(time.monotonic() - self.t0, 1),
            "requests": self.client.sent if self.client else 0,
            "house_log": self.client.log if self.client else [],
            "contract": self.contract.source,
            "deliverables": sorted(names),
            "chosen": self.promoted.label if self.promoted else None,
            "candidates": [{"label": c.label, "clean": c.clean, "rounds": c.rounds, "problems": len(c.problems)} for c in self.cands],
            "agreement": self.stage_log,
            "final_problems": self.last_problems,
            "tree": tree_summary(self.out_dir),
            "finalize": log,
        }
        print("JP_SUMMARY " + json.dumps(summary, ensure_ascii=False)[:20000], flush=True)
        return summary
