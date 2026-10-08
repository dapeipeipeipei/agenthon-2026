# jpsim — resource and provenance declaration (Agenthon 2026 Track 3, team Jin & Pei)

This submission is the organizers' Track 3 reference engine, accelerated without changing any
output byte. Every material input is listed here so the submission can be reproduced lawfully.

| Component | Origin | Licence | Where in this repository |
|---|---|---|---|
| ABIDES (`abides-core`, `abides-markets`) | `jpmorganchase/abides-jpmc-public` @ `f9cbe51342b7dedd9587e4e069040d68a5c6477f` (the Track 3 pin) | BSD-3-Clause (`t3-work/engine/LICENSE.ABIDES`) | `t3-work/engine/abides_core`, `t3-work/engine/abides_markets` |
| Four Track 3 overlays | `Agenthon-2026/track3-simulation-public` `baselines/patches/` (pomegranate-free order-size model, kernel message ledger, exchange STP, oracle scheduled jump), applied in Dockerfile order before vendoring | MIT (kit `LICENSE`) | applied inside the vendored packages |
| `simulate` adapter (`config.py`, `agents.py`, `scenario_io.py`) | kit `baselines/abides_fork/` | MIT | `t3-work/engine/jpsim/` (copied, then edited; edits marked `# [jpsim]`) |
| Trace writer, CLI, batch runner | written by the team | BSD-3-Clause (this submission) | `t3-work/engine/jpsim/trace_fast.py`, `cli.py` |
| Runtime | Python 3.11 (`python:3.11-slim-bookworm`), numpy 1.26.4, pyarrow 15.0.2 | PSF / BSD-3 / Apache-2.0 | `t3-work/Dockerfile` |

No model, no network access, no data beyond the mounted scenario. Every change to the vendored
engine is annotated `# [jpsim]` and listed in `t3-work/PLAN.md`; `t3-work/tools/strip_debug_logs.py`
is the only automated rewrite (removal of `logger.debug` / `logger.info` statements). Outputs are
verified byte-for-byte against the organizers' reference traces on all 71 public units
(`t3-work/tools/check_hashes.py`, run in CI on every image build).
