# Track 1（Coding）进度 — 2026-10-08（第一版：从零搭起的 House 驱动编程 agent）

一句话：T1 的提交是一个**调用 House 模型写代码并自测修复**的 agent 镜像（`solve --task-dir /input --out /app/output`）。
本地用假 House（`harness/mock_house.py`）跑完 86 道公开题：**86/86 exit 0、输出目录全部合规、0 崩溃**；
两道由假 House 给出真实解的题（`t1-EXAMPLE-bs-greeks-pde`、`t1-zero-coupon-bootstrapping`）**通过各自的官方 checker**，
证明「计划 → 生成 → 运行 → 自检 → 复审 → 落盘」整条链是通的。**真实答题质量只有上传 Dev 才知道**（House 只能从评测容器里访问）。

| 项 | 值 |
|---|---|
| 镜像 | `ghcr.io/dapeipeipeipei/jinpei-t1`（CI 构建；digest 见下表 / `submission/submission.json`） |
| Dev 描述文件 | `submission/submission.json`（`competition_id=agenthon2026-coding-dev`，`models[]` = HOUSE-MODEL.md 的 House 行） |
| 决赛描述文件 | `submission/submission.final.json`（`agenthon2026-coding-final`，`phase=final`） |
| 打包 | `.venv\Scripts\python pack_all.py t1-dev`（Dev）/ `pack_all.py t1-final`（决赛）；隐藏提示符输入 Team Key |
| 上传限制 | **每天 1 次**、Dev 共 23 次；10-12 20:00 UTC 后不再开跑 → 最多还能传 ~4 次 |

| 版本 | 镜像 digest | CI run | 说明 |
|---|---|---|---|
| v0.1 | `sha256:ec2fc7d56830dc0f350ac2386680d3808d99d498ee73faae5031c728e5a873fd` | [37820572588](https://github.com/dapeipeipeipei/agenthon-2026/actions/runs/37820572588)（ci/t1-image，commit 051b3bb+CI 修正） | 首版；镜像内 86/86 exit 0、输出树全合规、oracle 2/86 过 checker、9 种退化模式全过。**匿名拉取检查 10-08 已 PASS**（`verify_anonymous_pull.sh`：manifest/config/11 层全部匿名可取，压缩 360 MB，linux/amd64、label 2.0、非 root、无 entrypoint）→ 可直接 `pack_all.py t1-dev` 上传 |

## agent 做什么（`agent/`，纯标准库；数值栈只给生成的脚本用）

1. **读题**（`unit.py`）：`instruction.md`（去掉 canary 行）、`card.toml`（`[agent].timeout_sec`、canary GUID）、
   `environment/data/` 全部文件（大小 + 预览：CSV 前几行/行数、JSON 结构、parquet schema、xlsx 表头、zip 成员列表）、
   `environment/Dockerfile` 的 `COPY` 行 → **题面路径到真实路径的映射**（题面说 `/app/data/stock_data.parquet`，
   真实文件是 `/input/environment/data/stock_chars.pqt`；40/86 道题题面写 `/app/data`，平台实际挂 `/input`）。
2. **计划**（1 次 House 调用，≤1800 token）：让模型以 JSON 给出 **交付物清单**（文件名/格式/字段）+ 解题步骤 + 易错点。
   模型漏掉的交付物再用正则从题面补（`/app/output/xxx`、`/output/xxx`）。
3. **生成**（2 次并行调用，temperature 0.1 / 0.6，固定 seed）：完整单文件脚本。回复被 4000 token 截断时先要「续写」拼接，
   拼不上再要「紧凑版」。
4. **运行**（`runner.py`）：子进程、工作目录 = 输出目录、硬超时（默认 180 s）、RLIMIT_AS 40 GiB（避免 OOM kill 判
   `resource_oom`）、BLAS/numba 线程 8（256 PID 上限）。运行前把题面路径**确定性替换**成真实路径（`outputs.rewrite_paths`）。
5. **自检**（`outputs.check_deliverables`）：每个交付物存在、非空、JSON 可解析且无 NaN/Inf、CSV 有表头有数据、parquet 有行、PNG 魔数。
6. **修复**（最多 6 轮，受时间/请求预算约束）：把退出码、stderr 尾部、缺失/损坏的交付物回喂模型，要完整修正脚本。
7. **复审**（1 次）：把写出的文件预览给模型对照题面审一遍（文件名、键、列、单位、符号、舍入、金融合理性）；
   回 `OK` 则结束，否则运行修正版，**只在它也通过自检时才替换**（先备份，失败回滚）。
8. **落盘**（总是执行，含异常/闹钟路径）：缺的交付物写合法桩文件（`{}` / 表头 CSV / 1 行 parquet / 1×1 PNG），
   然后按主办方「输出目录规则」清理：去链接/特殊文件、>64 MiB、>256 个文件、大小写重名、非 NFC 名、权限位，
   **扫掉 canary GUID**，保证至少 1 个普通文件。`exit 0` 永远。

### 预算与时钟

- **每题墙钟**：`JP_UNIT_BUDGET_SEC`（默认 **420 s**）与 `card timeout − 150 s` 取小。Dev 阶段整卷 43,200 s / 86 题 ≈ 502 s，
  420 s 封顶 + 容器开销后整卷最坏 ≈ 10.5 h，不会撞 12 h 阶段钟（撞了未跑到的题记 `not_reached`=0）。
- **House 请求**：`JP_MAX_REQUESTS` 默认 **18**（平台上限 25，留 7 个富余）；每次请求发出即计数（和平台一致）；
  只对 407（代理拒绝、不计费）/429/5xx/传输失败重试 1 次；401/403 不重试。每次调用超时 150 s。
- 关思考：每个请求带 `chat_template_kwargs.enable_thinking=false`（思考 token 也算进 4000 上限，issue #28）。
- 采样固定在代码里：temperature 0.1（第二候选 0.6）、top_p 0.95、seed 常量（`QFBENCH_SEED` 每次重跑都变，不能用）。
- 无 `MODEL_*` 时（本地 `--network=none`）：直接写桩文件退出 0（规则 9：不算分，但目录合法）。

### 镜像（`Dockerfile`）

`python:3.13-slim` + 固定版本数值栈（numpy 2.2.6 / pandas 2.2.3 / scipy 1.15.3 / sklearn 1.6.1 / statsmodels 0.14.4 /
pyarrow 19.0.1 / polars 1.31 / arch 7.2 / numba 0.61.2 / matplotlib 3.10 / plotly 6 / openpyxl / lxml / bs4）。
非 root（65534）、无 ENTRYPOINT（`/usr/local/bin/solve` 在 PATH 上）、`LABEL qfbench2.interface_version="2.0"`、无 VOLUME、
HOME 与各种缓存指向 `/tmp`、`/app/data -> /input/environment/data` 软链。构建时跑 `agent.selftest`（离线桩 / 假 House 正常 / 假 House 先错后修）。

## 本地结果（假 House，`harness/`）

| 项 | 结果 |
|---|---|
| 86 道公开题跑通（源码模式，假 House good 模式） | **86/86 exit 0**，输出树全部合规，每题 4 次请求，共 21 s |
| 各题官方 checker（`harness/check_units.py`，本机 Windows 用 junction 模拟 `/app/output` 等路径） | **2/86 通过** = 假 House 给了真解的两题；其余是桩文件，理应失败 |
| 鲁棒模式（3 题 × flaky / truncated / think / garbage / 500 / 429 / 401 / slow / down / offline） | 全部 exit 0、目录合规、请求数 ≤ 6；截断续写拼接成功；flaky 走紧凑重写成功 |

命令（在 `t1-work/` 下，用主库 `.venv`）：

```
PYTHONUTF8=1 ..\..\agenthon\.venv\Scripts\python harness\run_units.py            # 86 题 + 假 House
PYTHONUTF8=1 ..\..\agenthon\.venv\Scripts\python harness\check_units.py          # 官方 checker 本地过一遍
PYTHONUTF8=1 ..\..\agenthon\.venv\Scripts\python harness\run_units.py --mode flaky --units t1-zero-coupon-bootstrapping
PYTHONUTF8=1 ..\..\agenthon\.venv\Scripts\python -m agent.selftest
```

CI（`.github/workflows/t1-image.yml`，推到 `ci/t1-image` 或 `cand/t1-*` 触发）：构建 linux/amd64 → 镜像内按平台容器设置
（只读根、uid 65534、64 MiB noexec /tmp、256 PID）跑 86 题 + 假 House → 输出树检查 → 各题 checker（两道 oracle 必须过）→
9 种退化模式 → 推 GHCR 打印 digest。推完用
`bash t2-work/pack/verify_anonymous_pull.sh --repo dapeipeipeipei/jinpei-t1 --digest sha256:…` 验证匿名可拉（v0.1 已 PASS；若 GitHub 把新包默认设为私有则要在 package 设置里改 public）。

## 第一次 Dev 上传能告诉我们什么

- 能看到：总分（通过题数/86）、run 页面每题的结果码（`domain_gate_failed` / `no_output` / `container_crashed` /
  `resource_oom` / `not_reached`）、总用时。主办方在 issue 里还会应要求给每题 House 请求数与时长。
- 要盯的：① 有没有 `container_crashed`/`no_output`（本版设计上不应出现）；② 每题用时分布（中位数应 ≈ 200–350 s，
  最长 ≤ 440 s）；③ 请求数是否接近 18；④ 通过题数——这是 House 模型 + 提示词的真实水平，本地测不出来。
- 对照：其他队同一模型下 Dev 榜在 25–33/86 一带（issue #17/#30 披露），头部可疑的 84/86 是预存答案。
- **拿到结果后再调**：通过率低 → 看是计划/生成失败多还是 checker 失败多（run 页面只给结果码，细节要靠本地复现）；
  用时逼近上限 → 降 `JP_UNIT_BUDGET_SEC` 或候选数；`no_output` → 输出树规则有漏网。

## 风险与已知限制

1. **真实质量未知**：House 只能从评测容器访问，本地没有同款模型；提示词、复审环节是否净增益都没法事先验证。
   每天只能传 1 次，最多还有 ~4 次 Dev 机会。
2. **11 道 `checks/verifier.py` 精确值题**（results.json + solution.json 对照 expected/checkpoints）基本无解，
   除非模型完全复现作者算法；本版不做特殊处理。
3. **时间**：House 中位响应 15–46 s（其他队的数据），4000 token 的脚本回复可能 60–80 s；420 s 内大约只够
   计划 + 2 候选 + 1–2 轮修复 + 复审。重算量大的题（100k × 990 蒙特卡洛）180 s 运行上限可能不够，会要求「更快的版本」。
4. **House 故障**（9-27 曾中断 3 小时）：无响应 150 s 超时后重试 1 次，再失败就桩文件退出；一整轮 Dev 可能因此白费。
5. **平台 `/input` 没有 `checks/`**（issue #28 确认），所以自检只能查格式/结构，不能跑 checker；真正的 g3 失败本地看不见。
6. **canary**：题面与 Dockerfile 里的 GUID 不给模型看，输出文件落盘前再扫一遍；但模型若把题面文字抄进输出文件
   （例如 html 报告），仍可能带其他可疑内容——目前只扫 GUID。
7. 规则 8：镜像**不带任何公开题的解**（`harness/mock_house.py` 的两道 oracle 解只在测试用，`.dockerignore` 排除整个 harness）。

## 文件

```
t1-work/
├── Dockerfile / .dockerignore / requirements.txt   镜像
├── bin/solve                                       verb 启动器（PATH 上，无 ENTRYPOINT）
├── agent/                                          cli / loop / unit / prompts / house / runner / outputs / selftest
├── harness/mock_house.py                           假 House（good/flaky/truncated/think/garbage/500/429/401/slow/down + 两道 oracle）
├── harness/run_units.py                            跑题（源码或镜像、平台容器设置）+ 输出树检查
├── harness/check_units.py                          各题官方 checker（路径 shim：Linux symlink / Windows junction）
├── descriptor_tool.py                              写/填/校验描述文件（toolkit v2.6.0 封签 + 语义检查）
├── submission/submission.json, submission.final.json
└── STATUS.md                                       本文件
```
