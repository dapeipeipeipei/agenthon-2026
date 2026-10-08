"""The agent loop: plan -> code -> run -> check -> repair ... -> review -> finalize.

Time: everything is bounded by one deadline computed from the process start (`budget`), itself
the smaller of JP_UNIT_BUDGET_SEC (default 420 s) and the card's [agent].timeout_sec minus a
margin. The 12-hour Development stage clock across the whole roster (~8 min per unit over 86
units, DEVELOPMENT-RUNTIME.md "Execution clocks") is the binding limit, which is why the default
is well under every card's own ceiling.

Requests: `house.Client` counts every request sent (max JP_MAX_REQUESTS, default 18 of the 25 the
House admits). Before each call we check that the expected call time plus what must follow it
(a run, the final stub/sanitize pass) still fits.

Every stage degrades: no plan -> contract from the task text; no code -> stubs; a failing run ->
repair; no repair -> keep the best files written so far; and the output folder is sanitised last.
"""
from __future__ import annotations

import ast
import json
import os
import shutil
import threading
import time
from pathlib import Path

from . import house, prompts
from .outputs import (Contract, check_deliverables, contract_from_model, preview_outputs, rewrite_paths,
                      sanitize_tree, tree_summary, write_stub)
from .runner import RunResult, run_script
from .unit import Unit, load_unit

FINAL_RESERVE_S = 15.0        # stubs + sanitation
RUN_RESERVE_S = 20.0          # a run must leave at least this much before the deadline


def cfg() -> dict:
    return {
        "unit_budget": house.env_float("JP_UNIT_BUDGET_SEC", 420.0),
        "card_margin": house.env_float("JP_CARD_MARGIN_SEC", 150.0),
        "max_requests": house.env_int("JP_MAX_REQUESTS", 18),
        "call_timeout": house.env_float("JP_CALL_TIMEOUT_SEC", 150.0),
        "run_timeout": house.env_float("JP_RUN_TIMEOUT_SEC", 180.0),
        "max_repairs": house.env_int("JP_MAX_REPAIRS", 6),
        "candidates": max(1, min(3, house.env_int("JP_CANDIDATES", 2))),
        "review": house.env_int("JP_REVIEW", 1) == 1,
        "mem_gib": house.env_float("JP_MEM_GIB", 40.0),
        "threads": house.env_int("JP_THREADS", 8),
    }


class Solver:
    def __init__(self, task_dir: Path, out_dir: Path, scratch: Path, t0: float) -> None:
        self.c = cfg()
        self.task_dir = task_dir
        self.out_dir = out_dir
        self.scratch = scratch
        self.t0 = t0
        self.events: list[str] = []
        self.unit: Unit | None = None
        self.contract = Contract()
        self.deadline = t0 + self.c["unit_budget"]
        self.client: house.Client | None = None
        self.last_code: str | None = None
        self.last_run: RunResult | None = None
        self.last_problems: list[str] = ["nothing ran"]
        self.finalized = False
        self.arm_alarm = None       # set by cli on POSIX: re-arm the hard alarm to the final budget

    # ---- helpers
    def log(self, msg: str) -> None:
        self.events.append(f"[{time.monotonic() - self.t0:6.1f}s] {msg}")
        print(self.events[-1], flush=True)

    def remaining(self) -> float:
        return self.deadline - time.monotonic()

    @property
    def out_posix(self) -> str:
        return self.out_dir.as_posix()

    # ---- main
    def run(self) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.unit = load_unit(self.task_dir)
        u = self.unit
        budget = min(self.c["unit_budget"], max(120.0, u.timeout_sec - self.c["card_margin"]))
        self.deadline = self.t0 + budget
        if self.arm_alarm is not None:
            try:
                self.arm_alarm(budget)
            except Exception:  # noqa: BLE001
                pass
        self.log(f"unit {u.unit_id}: card timeout {u.timeout_sec:.0f}s, our budget {budget:.0f}s, "
                 f"{len(u.files)} data files, {len(u.canaries)} canary ids, mapping {len(u.mapping)} paths")
        if not house.configured():
            self.log("House route not configured (MODEL_ENDPOINT/MODEL_TOKEN/MODEL_NAME missing): offline fallback")
            self.contract = contract_from_model(None, u, self.out_posix)
            return
        self.client = house.Client(deadline=self.deadline - FINAL_RESERVE_S, max_requests=self.c["max_requests"],
                                   call_timeout=self.c["call_timeout"])
        # 1. plan + contract
        plan_obj = None
        r = self.client.chat(prompts.plan_messages(u, self.out_posix), max_tokens=1800, tag="plan")
        if r.ok:
            plan_obj = house.extract_json(r.content)
        self.contract = contract_from_model(plan_obj, u, self.out_posix)
        self.log(f"plan: {r.status} ({r.seconds:.0f}s, {r.completion_tokens} tok); contract source={self.contract.source}, "
                 f"deliverables={[d.name for d in self.contract.deliverables]}")
        if r.status in ("http_401", "http_403"):
            self.log("House refuses our credentials; nothing more to do")
            return
        # 2. code candidates
        codes = self.generate_candidates()
        if not codes:
            self.log("no code obtained; stubs only")
            return
        # 3. run + repair
        best = self.first_working(codes)
        code, res, problems = best
        rounds = 0
        while problems and rounds < self.c["max_repairs"]:
            need = self.client.expected_call_seconds() + RUN_RESERVE_S + 25.0
            if not self.client.can_call(reserve=need):
                self.log(f"stop repairing: {self.client.left} requests left, {self.remaining():.0f}s left")
                break
            rounds += 1
            report = self.report(res, problems)
            new = self.get_code(prompts.repair_messages(u, self.out_posix, code, report, self.run_limit()), tag=f"repair{rounds}")
            if not new:
                break
            code, res, problems = self.try_code(new, tag=f"repair{rounds}")
        # 4. review
        if not problems and self.c["review"]:
            self.review(code)

    # ---- stages
    def run_limit(self) -> int:
        return int(max(30.0, min(self.c["run_timeout"], self.remaining() - RUN_RESERVE_S)))

    def generate_candidates(self) -> list[str]:
        u = self.unit
        k = self.c["candidates"]
        msgs = prompts.code_messages(u, self.out_posix, self.contract.plan, self.run_limit(), self.contract.deliverables)
        temps = [house.TEMPERATURE, 0.6, 0.9][:k]
        results: list[str | None] = [None] * k
        if k == 1:
            results[0] = self.get_code(msgs, tag="code")
        else:
            def work(i: int) -> None:
                results[i] = self.get_code(msgs, tag=f"code{i}", temperature=temps[i])
            ths = [threading.Thread(target=work, args=(i,), daemon=True) for i in range(k)]
            for t in ths:
                t.start()
            for t in ths:
                t.join(max(1.0, self.remaining()))
        return [c for c in results if c]

    def get_code(self, messages: list[dict], *, tag: str, temperature: float = house.TEMPERATURE) -> str | None:
        """A complete script from one logical call; continuation / compact retry when truncated."""
        cl = self.client
        r = cl.chat(messages, max_tokens=house.MAX_OUTPUT_TOKENS, tag=tag, temperature=temperature)
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
        if cl.can_call(reserve=RUN_RESERVE_S + 20) and "repair" not in tag:
            r3 = cl.chat(prompts.compact_messages(self.unit, self.out_posix, self.contract.plan, self.run_limit(), self.contract.deliverables),
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
        # drop a repeated overlap of up to 12 lines
        best = 0
        for k in range(min(12, len(h_lines), len(m_lines)), 0, -1):
            if [x.rstrip() for x in h_lines[-k:]] == [x.rstrip() for x in m_lines[:k]]:
                best = k
                break
        if best:
            return "\n".join(h_lines + m_lines[best:]) + "\n"
        # the head's last line was probably cut mid-way: if the continuation starts at column 0 with
        # a statement, keep the head whole; otherwise append directly
        return head.rstrip("\n") + ("\n" if more[:1] not in (" ", "\t", ")", "]", "}", ",", ".") else "") + more + "\n"

    def first_working(self, codes: list[str]) -> tuple[str, RunResult | None, list[str]]:
        best: tuple[str, RunResult | None, list[str]] | None = None
        for i, code in enumerate(codes):
            if best is not None and self.remaining() < RUN_RESERVE_S + 40:
                break
            cur = self.try_code(code, tag=f"cand{i}")
            if not cur[2]:
                return cur
            if best is None or len(cur[2]) < len(best[2]):
                best = cur
        return best if best is not None else (codes[0], None, ["did not run"])

    def try_code(self, code: str, *, tag: str) -> tuple[str, RunResult | None, list[str]]:
        code = rewrite_paths(code, self.unit, self.out_posix)
        limit = self.run_limit()
        res = run_script(code, self.scratch, self.out_dir, timeout=limit, mem_gib=self.c["mem_gib"], threads=self.c["threads"])
        problems = check_deliverables(self.out_dir, self.contract.deliverables)
        if not res.ok:
            problems = [res.report()] + problems
        self.log(f"{tag}: run {'ok' if res.ok else 'FAILED'} in {res.seconds:.0f}s (limit {limit}s); problems={len(problems)}"
                 + (": " + problems[0][:160].replace("\n", " | ") if problems else ""))
        self.last_code, self.last_run, self.last_problems = code, res, problems
        return code, res, problems

    @staticmethod
    def report(res: RunResult | None, problems: list[str]) -> str:
        parts = []
        if res is not None:
            parts.append(res.report())
        extra = [p for p in problems if res is None or p != res.report()]
        if extra:
            parts.append("Deliverable check:\n- " + "\n- ".join(extra))
        return "\n\n".join(parts)[:6000]

    def review(self, code: str) -> None:
        cl = self.client
        need = cl.expected_call_seconds() + RUN_RESERVE_S + 40.0
        if not cl.can_call(reserve=need):
            self.log("skip review: not enough time/requests")
            return
        names = [d.name for d in self.contract.deliverables]
        size = sum((self.out_dir / n).stat().st_size for n in names if (self.out_dir / n).is_file())
        if size > 24 * (1 << 20):
            self.log("skip review: deliverables too large to back up")
            return
        previews = preview_outputs(self.out_dir, self.contract.deliverables)
        r = cl.chat(prompts.review_messages(self.unit, self.out_posix, code, previews, self.run_limit()),
                    max_tokens=house.MAX_OUTPUT_TOKENS, tag="review")
        if not r.ok:
            self.log(f"review: no reply ({r.status})")
            return
        body = house.strip_think(r.content).strip()
        new = house.extract_code(r.content, allow_open=False)
        if body.upper().rstrip(".") == "OK" or not new or r.truncated:
            self.log(f"review: {'OK' if body.upper().startswith('OK') else 'no usable code'} ({r.seconds:.0f}s)")
            return
        if not self.compiles(new):
            self.log("review: proposed code does not compile; kept the current outputs")
            return
        backup = self.scratch / "backup"
        shutil.rmtree(backup, ignore_errors=True)
        backup.mkdir(parents=True, exist_ok=True)
        for n in names:
            src = self.out_dir / n
            if src.is_file():
                (backup / n).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, backup / n)
        if self.remaining() < RUN_RESERVE_S + 25:
            self.log("review: no time left to run the revised script")
            return
        self.log("review: running the revised script")
        code2, res2, problems2 = self.try_code(new, tag="review-run")
        if problems2:
            for n in names:
                b = backup / n
                if b.is_file():
                    (self.out_dir / n).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(b, self.out_dir / n)
            self.last_code, self.last_problems = code, []
            self.log("review: revised script failed; restored the previous outputs")
        else:
            self.log("review: revised outputs kept")

    # ---- finalization (always runs)
    def finalize(self) -> dict:
        if self.finalized:
            return {}
        self.finalized = True
        log: list[str] = []
        try:
            self.out_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        names = set()
        try:
            if not self.contract.deliverables and self.unit is not None:
                self.contract = contract_from_model(None, self.unit, self.out_posix)
            if not self.contract.deliverables:
                from .outputs import Deliverable

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
            "final_problems": self.last_problems,
            "tree": tree_summary(self.out_dir),
            "finalize": log,
        }
        print("JP_SUMMARY " + json.dumps(summary, ensure_ascii=False)[:20000], flush=True)
        return summary
