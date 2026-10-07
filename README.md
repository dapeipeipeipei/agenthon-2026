# Agenthon 2026 — Team「Jin & Pei」

## 给队友：从这里开始

> Yuren，这一节是写给你的。读完这一节你就知道项目是什么、现在什么状态、截止前**你**必须做什么。
> 下面更长的部分是设计和操作手册，需要时再看。

### 这是什么

我们参加 [Agenthon 2026](https://www.agenthon.net/)（NeurIPS 2026 Competition Track，「可验证的 AI × 量化金融」）。
四个赛道里**主攻 Track 2（概率时序预测）**：给一段历史行情和一小堆央行文档，交 2000 个「可能的未来值」，
按 CRPS 打分并**除以一个不读文本的官方基线（M0）**，1.0 = 打平，越低越好。**副攻 Track 4**（带证据引用的表格预测）。
提交物是一个 Docker 镜像，主办方在离线沙盒里跑；上传的是用官方工具 `qfbench2 submission pack` 打出来的 zip。

### 现在的状态（2026-10-06 晚；10-07 评分规则更新见第一条）

- **10-07：主办方改了 T2 计分**（上游 `60509df`）：分母改为 M0「自己预期的误差」（不再看实际结果），截断/失败值 4.0 → 8.0，
  公开榜显示为 −(均值)，M0 参考行 −2.64。v4 按新规则重选：F2 宽度 1.0 → 1.25、F4 宽度 1.1 → 2.0。
  新规则 5 种子：全部 1.975、validation 65 张 1.773（旧行 2.236 / 2.026），估计榜上约 **−2.0 ~ −2.15**。
  下面几条里的 0.9xx 数字都是旧规则口径，详见 `t2-work/V4_NOTES.md` 修订 3。**镜像需要用新提交重新构建**（CI）。

- **T2 默认引擎是 v4**（`t2-work/engine/v4.py`）：以 M0 同样的信息（最近 300 行的均值和协方差）为骨架，按题卡上印的题族加一行常数
  （F1 宽度 1.0；F2 = 基线；F3 漂移 ×0.5；F4 宽度 1.1 + 20% 路径 ×1.5 倍波动并往压力方向偏 1 个标准差）。不调大模型。
  **按榜单口径 5 种子样本内 0.939**（F1 0.969 / F2 1.007 / F3 0.932 / F4 0.870），选参流程严格留出 0.937（留一时代）/ 0.993（前向），
  独立审计确认度量与官方源码逐卡一致。
- **旧的「v3 = 0.900」作废**：那是除以参考 CLI 的内部数字；按榜单口径 v3 = **1.139**，比基线还差。来龙去脉见 [WORKLOG §9、§11.2](WORKLOG.md#112-t2-模型-v4--完成默认-profile)。
- **两个赛道的镜像都已构建并测试**（在 GitHub Actions 里，本机 Docker 起不来）：T2 镜像内 104/104、T4 镜像内 11/11，digest 已填进提交描述文件。
  **但还从没上传过**——剩下的全是只有人能做的步骤（见下表）。
- **截止**：新的 Dev 运行在 **10-12 20:00 UTC 之后不再启动**（Dev 10-12 23:59 AoE 关闭）；Final + Verification 10-13 → 10-25，每赛道**只交一份**。
- **Track 4** 用确定性、不调模型的程序：11/11 可接纳、0 条虚假引用，近似本地分 0.68 vs 官方基线 0.46。10-06 五条线全部完成，见 [WORKLOG §11](WORKLOG.md#11-2026-10-06-冲刺已完成)。

### 东西在哪

```
agenthon/
├── README.md        ← 你在读：项目是什么 + 设计 + 操作手册
├── WORKLOG.md       ← 从 09-08 到现在做过的每件事、每组实验的数字、走过的弯路
├── STATUS.md        ← 倒计时、当前状态、下一步、阻塞项（每次开工的入口）
├── research/        ← 09-08 四个赛道的调研报告（英文）+ F4 危机事件表（中文）
├── t2-work/         ← Track 2 全部代码
│   ├── engine/      ←   预测引擎（默认 v4；v1–v3 保留作回退和对照）
│   ├── Dockerfile   ←   提交镜像
│   ├── pack/        ←   打包、本地验收、填 digest、校验 zip 的脚本（pack/README.md 是操作手册）
│   ├── submission/  ←   submission.json / submission.final.json（已填 digest；不含任何密钥）、CHECKLIST.md（规则逐条对照）
│   ├── run_all_gates.py / realized.py / score_local.py   ← 本地打分台
│   ├── V4_NOTES.md / AUDIT_T2.md / v4_experiments.csv  ← v4 的方法、独立审计、全部实验
│   ├── v4_eval.py / audit_metric.py  ←   按排行榜方式（除以 M0）打分
│   └── grid_v2.py / ablate_v3.py / *_table.csv / scores_*.csv / ablation_*_log.txt  ← 实验与证据
├── t4-work/         ← Track 4 参赛代码（PLAN.md、STATUS.md、agent/、harness/、submission/）
├── .github/workflows/  ← t2-image.yml、t4-image.yml：云端构建镜像 + 平台同等限制下全量测试 + 推 GHCR
├── LICENSE          ← Apache-2.0（与描述文件一致）
└── Agenthon2026-public/  track1…track4-*-public/   ← 上游官方仓（gitignore，见「环境搭建」）
```

### 阅读顺序

1. **README**（本节就够了，后面按需查）
2. **[WORKLOG.md](WORKLOG.md)** —— 完整来龙去脉
3. **[STATUS.md](STATUS.md)** —— 现在做到哪、下一步
4. **`t2-work/pack/README.md`** —— 从 CI 构建到上传 zip 的逐步操作手册（中文，每步写了正常输出和出错怎么办）
5. **`t4-work/STATUS.md`** —— Track 4 的状态与成绩（其中 CI 一节的 digest 已过时，以 `t4-work/submission/submission.json` 为准）

### 截止前必须由「人」来做的事（按顺序）

这些事 agent 不能、也不应该代做。T2 的每一步在 `t2-work/pack/README.md` 第 3–8 步有详细说明。

| # | 事项 | 谁 | 怎么做 |
|---|---|---|---|
| 1 | **把两个 GHCR 镜像包改成公开** | dapeipeipeipei GitHub 账号 | <https://github.com/users/dapeipeipeipei/packages/container/package/jinpei-t2> 和 `.../jinpei-t4` → Package settings → Danger Zone → Change visibility → Public。主办方不带账号拉镜像，私有包 = 整次提交白费 |
| 2 | **验证不登录也能拉到** | 同上 | `bash t2-work/pack/verify_anonymous_pull.sh --digest sha256:c558843f6826941ae2e9320417ffc02e036076e252c6717335183348df6b7d74`；T4：`bash t2-work/pack/verify_anonymous_pull.sh --repo dapeipeipeipei/jinpei-t4 --digest sha256:bf2a164756244a4ede12e5ea92bf164b60f47c2ba664f9cd955c117f8c68e01c`。最后一行要是 `PASS` |
| 3 | **队长批准 Wenshuo 的入队申请，并确认队伍编号** | 队长 Yuren | 网站上操作。上次检查（09-22）还是 Pending Approval。**不入队就不算参赛、不能上榜、不能获奖**。`pack` 需要 `--team-number`，记录是 299，以网站为准 |
| 4 | **打包，在隐藏提示符里输入 Team Key** | 持有 Team Key 的人 | T2：`bash t2-work/pack/pack.sh --team-number <N> --digest sha256:c558843f…7d74`（完整 digest 见第 2 行），出现 `Team Key (hidden):` 时输入，最后一行 `READY: ... t2-work/pack/dist/submission.zip`。T4：`t2-work/pack/.venv-docker/Scripts/qfbench2 submission pack --descriptor t4-work/submission/submission.json --team-number <N> --out <T4 的 zip 路径>`，同样在隐藏提示符输入。**Team Key 等同密码：不进命令行、不进文件、不贴聊天、不告诉任何 agent，agent 也不会问** |
| 5 | **在 CodaBench 上传两个 zip** | 队伍指定的 CodaBench 账号持有人 | 各赛道 CodaBench 页面（链接在 agenthon.net 参赛公告里），阶段选 Development。每赛道先用 **1–2 次** Dev 上传确认线上能跑、逐题全部可接纳（每天 5 次、共 20 次；平台标 `Failed` 的不扣）。**最后一次 Dev 运行必须在 10-12 20:00 UTC 前启动** |
| 6 | **Final 阶段（10-13 → 10-25）指定唯一一份 final 提交** | 同上 | 只交 Dev 上真实跑过且逐题可接纳的版本。T2：`bash t2-work/pack/pack.sh --team-number <N> --digest sha256:c558843f…7d74 --descriptor t2-work/submission/submission.final.json --out t2-work/pack/dist/submission-final.zip`；T4 目前只有 Dev 描述文件，需要先准备一份 `competition_id` 为 `agenthon2026-analysis-final` 的。同分时先上传者胜 |

---

> 以下是原来的详细文档（设计、评分、命令手册），10-06 已按最新规则修订过。

本仓库**只包含我们自己写的东西**。四个上游官方仓（约 900 MB）克隆在本仓库**根目录**下、已 gitignore，
需要时从 `github.com/Agenthon-2026` 重新克隆（见[环境搭建](#环境搭建)）。

**时间线**：Registration + Development 截止 **2026-10-12 23:59 AoE**（新 Dev 运行 10-12 20:00 UTC 后不再启动）·
Final + Verification **10-13 → 10-25**（每赛道一份 final）· NeurIPS Atlanta 12-09 → 12-13 获奖队展示。

---

## 目录

1. [五分钟跑通](#五分钟跑通)
2. [这个比赛在比什么](#这个比赛在比什么)
3. [Track 2 的题长什么样](#track-2-的题长什么样)
4. [评分规则](#评分规则)
5. [我们的引擎](#我们的引擎)
6. [成绩与消融结论](#成绩与消融结论)
7. [仓库结构](#仓库结构)
8. [环境搭建](#环境搭建)
9. [命令手册](#命令手册)
10. [想改进从哪下手](#想改进从哪下手)
11. [铁律](#铁律)
12. [未决事项](#未决事项)

---

## 五分钟跑通

前置：Python ≥ 3.13，Git，约 1.5 GB 磁盘。详细步骤见[环境搭建](#环境搭建)。

```bash
# 1. 克隆本仓库和上游工具包（track 仓放在本仓库根目录下，已 gitignore）
git clone https://github.com/dapeipeipeipei/agenthon-2026.git agenthon
cd agenthon
for r in Agenthon2026-public track2-forecasting-public; do
  git clone --depth 1 https://github.com/Agenthon-2026/$r
done

# 2. 建环境
python -m venv .venv
.venv/Scripts/python -m pip install \
  "qfbench2-common @ git+https://github.com/Agenthon-2026/Agenthon2026-public.git@v2.6.0#subdirectory=common"
.venv/Scripts/python -m pip install ./track2-forecasting-public

# 3. 跑一道题（Windows 上 PYTHONUTF8=1 是必须的，见环境搭建）
export PYTHONUTF8=1
cd t2-work && ../.venv/Scripts/python -m engine.forecast \
  --panels ../track2-forecasting-public/units/t2-F4-gbp-brexit-2016 \
  --text   ../track2-forecasting-public/units/t2-F4-gbp-brexit-2016/text \
  --asof 2016-05-31 --out out_one/forecast.parquet
```

跑完在 `t2-work/out_one/` 会看到三个文件。`forecast_rationale.md` 是人话版的推理过程，先看它。

---

## 这个比赛在比什么

一句话：**让 AI 真正完成量化金融任务，而且答案必须能被程序验证确实对，不只是看起来对。**

它和 Kaggle 那种「给数据、训模型、拼分数」完全不同。你提交的不是 notebook，是一个 **Docker 镜像**。
主办方把它放进离线沙盒跑，先过四道自动验证门，全过了才计算比赛指标。

**上传方式**（09-20 起）：镜像推到允许匿名按 digest 拉取的公开 registry → 把 digest 写进 `submission.json` →
`qfbench2 submission pack --descriptor submission.json --team-number <N> --out submission.zip`（在隐藏提示符里输入 Team Key，
它推出 `team_id` 并写入 `team-claim.json`，**Team Key 本身不进 zip**）→ 在赛道的 CodaBench 页面用队伍指定账号上传 zip。
T2 每天 5 次、Dev 期间共 20 次；平台标 `Failed` 的不扣次数。

四个赛道：

| 赛道 | 任务 | 指标 |
|---|---|---|
| T1 Coding | agent 解量化金融编程题 | pass@1 |
| **T2 Forecasting** | **概率时序预测（本仓库）** | **CRPS 复合分** |
| T3 Simulation | 加速 ABIDES 市场模拟器 | events/sec |
| T4 Explainability | 带证据引用的表格预测 | 质量 + 覆盖率 |

四个赛道的完整分析见 `research/T1-coding.md` 到 `research/T4-analysis.md`，包含各自的合约、资源限制、
指标定义、坑位、三周可行性评估。选 T2 的理由在那里。

### 评测机长什么样（所有赛道共用）

| 项 | 值 |
|---|---|
| GPU | 1 × NVIDIA B200，183 GB，compute capability 10.0（`sm_100`），CUDA ≤ 13.0 |
| 沙盒 | gVisor（`runsc`）。系统调用慢 85%，本地 socket 慢 75%，纯计算几乎无损 |
| 网络 | **只通 `$MODEL_ENDPOINT`**（经审计代理）。PyPI、HuggingFace、OpenAI、Anthropic 全部拒绝，也不注入任何第三方 API key |
| 模型 | 主办方托管的 House 模型（Nemotron 系列），`POST $MODEL_ENDPOINT/v1/chat/completions` + `Authorization: Bearer $MODEL_TOKEN`。**预算按请求计：每题 25 次被接纳的请求，每次最多 4000 输出 token**（原「每题 100 万输入 + 10 万输出 token」已于 09-21 撤销） |
| T2 资源 | 16 CPU 配额 / 128 GB / **每题 1800 秒**（含建容器和拉镜像） |
| T2 阶段钟 | Dev 摄取阶段整体 43,200 秒（12 小时）**顺序**跑完所有题；Dev 设置不代表 Final 资源 |
| 容器 | 任意非 root uid（平台用 `--user 65534:65534`）、**只读根文件系统**、64 MiB noexec `/tmp`、256 PID、每进程 1024 文件句柄、没有 HOME |
| 镜像 | 必须 `linux/amd64`、匿名可按 digest 拉取、带 `LABEL qfbench2.interface_version="2.0"` |
| 自带模型（BYO） | **已撤销**（09-18 裁定）。唯一类别 `api`；不调模型的提交写 `models: []` |

> **重要**：我们的 T2 引擎**不调用任何模型**，在没有 `MODEL_ENDPOINT` 的环境下也完整跑出合法结果。
> 将来若加文本方向层（v4 的 `house.py`），也必须保持「端点不可用时照样出合法结果」。

---

## Track 2 的题长什么样

用 `t2-F4-gbp-brexit-2016` 这道真题说明。

**题面**：你被扔回 **2016 年 5 月 31 日**。手里有：

- 英镑兑美元从 2000 年到当天的每日汇率，4125 行，当天收在 **1.453**
- 5 篇英国央行和欧央行官员的公开讲话，最晚一篇 4 月 26 日
- 题卡 `card.toml`，写着：预测资产 `GBP`、期限 21 个交易日、类型 `level`

**问**：21 个交易日后（2016-06-30）英镑是多少？

题目没告诉你、但翻日历就知道的事：**6 月 23 日英国脱欧公投，在这 21 天窗口里**。

**关键**：它不要一个数字，要 **2000 个「可能的世界」**，每个世界一个汇率值。这叫 draws（抽样）。
你交上去的是一张「可能性地图」，而不是一个预言。

真实答案是 **1.324**（英镑暴跌）。主办方藏着这个数，等评测时才拿出来。

### 输入输出合约

```
forecast --panels /input/panels/ --text /input/text/ --asof 2016-05-31 --out /output/forecast.parquet
```

（真实评测时题目是 staged 过的，parquet 在 `/input/panels/` 下；公开仓里 parquet 在题目根目录。引擎两种布局都支持。）

镜像必须接受 `forecast` 作为第一个位置参数（要么它在 `PATH` 上且不设 `ENTRYPOINT`，要么
`ENTRYPOINT` 程序把它当位置参数吃掉）。题目目录只读挂载在 `/input`。

**输入**

| 文件 | 内容 |
|---|---|
| `*.parquet` | 长表，列为 `date, asset, value, panel_id`。公开仓 104 道题的 parquet 都在题目根目录；**平台上 staged 的题在 `panels/` 子目录**，要传 `/input/panels/` |
| `text/corpus_index.json` + `*.txt` | 冻结语料。索引里每篇有 `doc_id`、`timestamp`、`source`、`doc_type`、`file`。**会议纪要的 timestamp 是公开发布日，不是开会日** |
| `card.toml` | `[targets]` 给出 `asset_ids`、`horizons`、`target_type`、`value_unit`；`[provenance] data_cutoff` 是 as-of |

**输出**：`/output` 里**正好三个文件，多一个都不行**（g0 会数文件个数并拒绝）。

| 文件 | 要求 |
|---|---|
| `forecast.parquet` | 列**正好**是 `draw, asset, horizon, value`；行数**正好**等于 抽样数 × 资产数 × 期限数；draw 编号 0..n-1 连续；每个 `(draw, asset, horizon)` 格子恰好一次；全部有限值；≤ 64 MiB |
| `forecast_meta.json` | `unit_id`（**不是 `card_id`**）必须等于题卡 `[task].id`；`asof` 精确匹配；`representation` 必须是 `"samples"`（`parametric` 会被拒）；`asset_ids` 和 `horizons` 的**顺序**也是合约的一部分；`n_draws` ∈ [200, 20000]；`target` 必须等于题卡的 `target_type` |
| `forecast_rationale.md` | 必须存在，**从不计分**（g1 只检查有没有非空白字符），但会作为榜首的人工筛查材料（格式见 `track2-forecasting-public/docs/RATIONALE-REVIEW.md`） |

**输出目录规则**（10-01 起写明）：容器退出后平台检查整个 `/output` 树，不能有符号链接/硬链接/特殊文件、
单文件和总量 ≤ 64 MiB、≤ 256 个文件、目录不能太深、不能有只差大小写的文件名等。exit 0 但树被拒 → 该题记 `no_output`。

### 题库构成（104 道公开题）

| 题族 | 数量 | 特点 |
|---|---|---|
| F1 常规宏观 | 23 | 平静期的利率、汇率。文本信号弱，靠统计 |
| F2 汇率与跨市场 | 27 | 含 5 道 transfer 题：目标资产没有近期历史，只给一个锚点行 |
| F3 多资产联合 | 22 | 多个资产 × 多个期限，联合结构占分 30% |
| F4 危机事件 | 31 | **最多**。2008、2013、2015、2016、2020、2022、2023 等著名时点 |

其他统计：88 道预测水平值 / 16 道预测对数收益（交错了直接判最差分）；60 道是单格（1 资产 × 1 期限）；
77 道标 hard；as-of 从 2003-08-12 到 2024-12-18；语料共 627 个 txt、28.3 MB、410 篇去重文档，
每题 2 到 15 篇。

104 道 = 71 道 `validation` + 33 道 `public-dev`。**Dev 排行榜打的是那 71 道 validation 题**，而且官方明说
Dev 榜是「练习板」不是排名：练习题彼此泄题（103 道里 75 道的答案原样出现在兄弟题面板里），名次没有意义，看逐题分数检查管线和校准即可。

**最终排名不用这 104 道题。** 用的是主办方封存的另一批题，时点在 2025 下半年到 2026 上半年，我们
永远看不到（官方实测练习数据对它 0 泄漏）。这 104 道只是我们验证方法通用性的沙盘。

---

## 评分规则

### 第一层：四道门（admissibility gates）

| 门 | 检查什么 | 失败典型原因 |
|---|---|---|
| `g0_integrity` | 跑的确实是你的镜像、输出目录干净 | 多写了一个文件；写到相对路径导致文件留在容器里 |
| `g1_schema` | 三个文件的格式 | `card_id` 写成 `unit_id`；列名错；行数不对 |
| `g2_cutoff_resource` | 声明的题号/as-of/target 与题卡一致，资源没超 | `target` 写 `level` 但题卡是 `log_return` |
| `g3_domain_semantics` | 网格齐全，每格恰好一次 | 有重复格子（**不会被平均，直接拒**） |

**任何一道门没过，这题直接记 4.0（最差分），不再往后算。** Dev 阶段可以重新上传（有次数限制），Final 阶段
只有一份提交，失败就是永久失去这道题。

### 第二层：分数

```
S = 0.5 × CRPS_marginal + 0.3 × variogram(p=0.5) + 0.2 × pinball_tail
```

越低越好。三项分别是：

- **CRPS 边际（50%）**：每条序列各自的分布离真实值多近。分布密集区靠近真实值得分好。
- **variogram 联合（30%）**：多资产/多期限之间的**依赖结构**对不对。四条收益率各算各的会被这一项抓住。
- **pinball 尾部（20%）**：在 1%、5%、95%、99% 四个分位上的 pinball loss 平均。

单格题（60/104）没有联合项，权重重新归一化为 `(0.714, 0, 0.286)`。

### 第三层：归一化

**每一项都要除以主办方官方基线 M0 的同一项，然后再加权。** M0 是一个「看得到面板、看不到任何文本」的
联合高斯随机游走，做法在 `track2-forecasting-public/docs/M0-BASELINE.md` 里完整公开（各题的缩放值不公开）。

所以 **1.0 = 和不读文本的随机游走打平**，0.9 = 比它好 10%。

**这就是为什么我们的所有内部指标都叫 ratio。** 但要注意两个口径差异（10-06 才发现，详见 WORKLOG §9）：

| | 排行榜 | 我们 09-13 的本地打分台（`score_local.py`） |
|---|---|---|
| 分母 | **M0** | 官方**参考 CLI**（`qfbench2_track_forecasting/cli.py`，也是 text-blind 随机游走，但不是 M0） |
| 归一化 | 三个分量**分别**除以 M0 的同一分量再加权 | 整个复合分相除 |
| 跨题聚合 | **算术**平均，裁剪到 [0, 4] | **几何**平均 |

所以 README 里 v1/v2/v3 的 0.9xx 只是内部指标，不等于榜单分数——按 M0 口径，v1–v3 在平静的 F1 题上反而输给基线（v3 = 1.139）。按榜单口径评估用 `t2-work/v4_eval.py`。

### 第四层：聚合

所有题等权平均。不可接纳、崩溃、没跑完的题记 **W = 4.0**，并且**留在分母里**。真实分数也被裁剪到
[0, 4]。

官方文档里记录了这条规则的由来：曾经有人发现「五道简单题各 0.10 + 一道难题 9.00」平均是 1.58，
而把难题故意搞成不可接纳后平均变成 0.10，**故意失败能让排名提升 15 倍**。现在堵死了。

**对我们的含义**：宁可交一份平庸但合法的答案，也绝不能崩溃或格式错。

---

## 我们的引擎

代码在 `t2-work/engine/`，只依赖 numpy / pandas / pyarrow。

```
engine/
├── io.py         读面板、题卡、forecast_spec.json（月度目标期），写三个输出文件、rationale 账本
├── v4.py         v4：M0 式骨架 + 按题族的常数（默认）
├── cardinfo.py   从题卡读题族、目标类型、月度观测月（不读题号、不读任何结果）
├── model.py      模拟核心与 profile 表：v1–v3 的块自助采样、尺度混合、情景混合；v4 → v3 → v2 回退
├── events.py     确定性语料事件检测器
├── assets.py     资产 → 压力方向映射表
├── house.py      House 模型调用层，默认关闭（`JINPEI_USE_HOUSE=1` 且注入 MODEL_* 才导入）
└── forecast.py   CLI、profile 选择、回退链、600 秒看门狗
```

四档 profile，用 `--profile` 选，**默认 v4**。下面先按历史顺序讲 v1–v3（09-13 的工作），最后讲为什么换成 v4。
⚠ v1–v3 小节里的分数都是旧的内部口径（除以参考 CLI），按榜单口径它们都输给基线。

### v1 — 统计骨架

对手（text-blind baseline）做的是：取最后一天的值当中心，用历史日变动标准差乘以根号 h 当宽度，
画正态分布。我们的 v1 改了四处：

1. **块自助采样代替正态**。把历史日变动切成 5 到 10 天的小块随机拼接出 h 步路径。真实市场的肥尾
   自动保留，正态画不出 2008 那种单日暴跌。
2. **波动用近期 0.6 + 全史 0.4 混合**。
3. **多资产共用同一组块索引**，经验联合依赖直接保留，不用估相关矩阵做 Cholesky。
4. **长期限是短期限路径的延续**（前缀关系），跨期限结构自然一致。

抽样数 2000。Transfer 题（目标资产没近期历史）用锚点水平 + G10 美元因子代理，beta 由早期窗口
标准差比推出，下限 0.3（人民币 1995–2005 是盯住汇率，原始 beta 只有 0.11）。

### v2 — 校准

v1 有一个系统性问题：**危机题被算窄了**。危机题站在危机**前夕**，最近 60 天风平浪静，
「近期占六成」的混合把宽度压到对手的 0.80 倍（F4 的 25 分位只有 0.60 倍）。真实值飞出区间，重罚。

实测：真实值落在 90% 区间外的比例，对手 23.7%，我们 26.3%，理想是 10%。两边都太自信，我们更甚。

v2 加了两件事：

- **逐路径尺度混合**：每条路径以概率 p 拿到一个 k 倍波动乘数（默认 p=0.2, k=2.0），整条路径的所有
  资产和步共用同一个乘数，所以联合结构不被破坏。
- **全局宽度旋钮** w（默认 1.15）。

参数从 18 组网格里选（`t2-work/grid_v2_table.csv`）。选的不是总分最好的那组，而是**四个题族全部
不超过 1.0** 的那组，因为封存题集的危机题比例未知，不能押一头。

### v3 — 事件感知（09-13 默认，10-06 被 v4 取代）

v2 解决了危机题不输，但还是赢不了。根本矛盾：**统一放宽无法同时服务 F1 和 F4**。同一个设置下
F4 仍然偏窄（|z|>1.64 占 47%）而 F1 已经偏宽（占 3%）。

v3 的思路：**只在语料说有事的时候才放宽**。这是一个**确定性的、不用大模型的**检测器。

**`events.py` 做什么**：读 `corpus_index.json`，只取 `timestamp ≤ as-of` 的文档（代码里强制，
即使 staging 已经保证了），统计六类关键词的加权密度，文档按 45 天 e-folding 做时间衰减：

| 类别 | 例子（共约 100 条正则） |
|---|---|
| `binary` 二选一 | referendum, plebiscite, ballot, election, ratify |
| `crisis` 危机 | contagion, default, downgrade, bankruptcy, bank run, emergency, pandemic, coronavirus, invasion, sanctions, panic, collapse, subprime, deleverage, flight to quality |
| `policy` 政策 | FOMC, hike, rate cut, taper, floor, peg, intervention |
| `uncertainty` 不确定性 | uncertain, volatile, tail, fragile, vulnerable, downside |
| `hawkish` 鹰派 | tighten, restrictive, elevated inflation, ongoing increases, expeditious |
| `dovish` 鸽派 | rate cuts, easing, accommodative, recession, deflation, stimulus |

另外从 FOMC statement 的日期间隔推断**窗口内是否有议息会议**（statement 约 6 周一次，若上一份已
超过 3 周且期限 ≥ 15 个交易日，则大概率窗口内有一次）。

**这些特征怎么用**：

1. **放宽**：压力分数越高，宽度乘数越大（上限 2.5）。议息会议在窗口内额外 ×1.1。
2. **尾部偏斜**：`assets.py` 是一张压力方向表。风险资产（股票因子、高贝塔货币 AUD/NZD/CAD/NOK）
   压力时向下；避险货币（JPY/CHF，按面板的报价方向）相反；国债收益率压力时向下（避险买入），
   **除非语料是通胀/加息主导**（hawkish 压过 dovish），那时向上。拿到 k 倍乘数的那些路径额外加一个
   负向漂移冲击，左尾就变重了。
3. **二簇混合**：binary 信号足够强且文档够新时，把抽样分成两簇 —— 现状簇（权重 0.6）和冲击簇
   （权重 0.4，中心偏移 1.5 个期限标准差，宽度 1.5 倍）。Brexit 那道题就会走这条路。
4. **月度宏观漂移**：CPI/NFP/失业率这类月度面板保留过去 24 个月的均值漂移，不再去均值。

**关键修正：cell damping**。多资产题有 8 个格子时，事件响应会被叠加 8 次，把 F3 搞坏。所以所有
事件响应按 `1/sqrt(n_cells)` 衰减。加上这一条之后，v2 的全局宽度 1.15 可以退回 1.0 —— 放宽完全
由事件层按需提供。

### v4 — 对齐 M0 的骨架 + 按题族校准（当前默认）

10-06 发现排行榜的分母是 M0（最近 300 行均值和协方差的联合高斯随机游走），不是参考 CLI。按这个口径 v3 = 1.139，
输在三处：M0 带漂移而 v3 没有；v3 的尺度混合和事件放宽让平静题过宽；单资产两期限题的联合项只有一个平方差，
跨期限离散度的任何系统偏差都会让比值爆炸（3 道 F1 题撞到 4.0 截断）。

v4 的思路：**先和 M0 用同样的信息，只在按题族交叉验证证明有用的地方偏离它。**

| 题族 | 宽度 | 尾部混合 | 往压力方向的位移 | 漂移比例 |
|---|---|---|---|---|
| F1 | 1.0 | 关 | 0 | 1.0 |
| F2 | 1.0 | 关 | 0 | 1.0 |
| F3 | 1.0 | 关 | 0 | **0.5** |
| F4 | **1.1** | **20% 路径 ×1.5 倍波动** | **1 个标准差**（方向来自 `assets.py`） | 1.0 |
| 未知题族 | 1.0 | 关 | 0 | 1.0 |

题族从题卡上印的 family 读；`log_return` 目标在 ln(1+r) 上建模；月度题按观测月数步数。
F1 原选 0.9、F4 原选 1.25，独立审计（`t2-work/AUDIT_T2.md`）的压力测试显示它们是在押「公开卡是事后挑过的」，于是往 1.0 拉。
F4 的剩余风险：如果封存的 F4 卡完全没有冲击，F4 会输给 M0（k=0 时 1.294）。
方法、全部实验、试过没用的东西见 `t2-work/V4_NOTES.md` 和 `v4_experiments.csv`。

### 永不崩溃

回退链 **v4 → v3 → v2 → 高斯随机游走 → 紧急 N(0, 0.01)**，另有 600 秒 SIGALRM 看门狗强制走快速回退。任何一层抛异常，下一层接手，并把原因写进 rationale 和
`forecast_meta.json` 的 `engine.fallback_reason`。最坏情况仍然写出三个格式合法的文件并 `exit 0`。

实测过：传一个不存在的资产 id 进去，exit 0，三个文件齐全，`"fallback": true`。

---

## 成绩与消融结论

### 榜单口径（10-06 起以这个为准）

每张卡的复合分 ÷ M0 的复合分（三个分量先各自除以 M0 再加权），单卡裁剪到 [0, 4]，90 张可本地还原真实值的卡**算术**平均，越低越好，
1.000 = M0。由 `t2-work/v4_eval.py` 计算，并被 `t2-work/audit_metric.py`（直接调官方 verifier）独立复核为逐卡零差。

| profile | 总体 | F1 | F2 | F3 | F4 |
|---|---|---|---|---|---|
| M0（自检） | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| v1 | 1.038 | 1.053 | 1.038 | 1.011 | 1.048 |
| v2 | 1.173 | 1.783 | 1.084 | 1.065 | 0.940 |
| v3 | 1.139 | 1.719 | 1.089 | 1.056 | 0.876 |
| **v4（默认，5 种子平均）** | **0.939** | **0.969** | **1.007** | **0.932** | **0.870** |

v4 的诚实估计：选参流程严格留出 **0.937**（留一时代）/ **0.993**（前向，≤2018 选、≥2019 测）；as-of ≥ 2019 的卡 0.949；
cluster bootstrap 下 v4 − M0 = −0.078，95% CI [−0.112, −0.044]（审计对首版）。真实期望大概在 0.94 到 0.99 之间。
F2 仍是 1.00 左右——没有能过验证的确定性文本方向信号。

### 旧的内部口径（09-13，仅作历史记录）

ratio = 我们的复合分 ÷ **官方参考 CLI**（text-blind 随机游走）的复合分，几何均值。样本同为 90 道题。
**这张表不能当榜单分数看**：同样的 v3 按榜单口径是 1.139。

| profile | 总体 | F1 宏观 | F2 汇率 | F3 多资产 | F4 危机 | 真实值出 90% 区间的比例 |
|---|---|---|---|---|---|---|
| 参考 CLI（text-blind） | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 23.7% |
| v1 块自助 | 0.925 | 0.712 | 1.009 | 0.849 | 1.076 | 26.3% |
| v2 加校准 | 0.939 | 0.842 | 1.006 | 0.874 | 0.999 | 11.4% |
| **v3 事件感知** | **0.900** | **0.804** | **1.002** | **0.872** | **0.907** | **13.1%** |

理想的「出区间比例」是 10%。当时的结论是 v3 两样都拿到了——但那是在错误的分母下。

**四档 profile 全部 104/104 可接纳，零回退**（v4 在 `--platform-env` 下复核过）。

### 消融跑出来的三条结论

v3 消融共 40 组（分 6 批），每组逐题分数在 `t2-work/scores_v3_<tag>.csv`，每批汇总在 `t2-work/ablation_*_log.txt` 末尾。
`t2-work/ablation_v3_table.csv` 每批会被覆盖，**只剩最后一批的 6 行**。逐批数字整理在 [WORKLOG.md §6](WORKLOG.md#6-引擎-v3-与-ablate_v340-组只在有事时放宽)。

1. **统一放宽是死路。** 每一个能把 F4 压到 0.93 以下的设置，都会把 F3 推到 0.90 以上。这个权衡
   是结构性的，参数调不出来。
2. **不对称尾部最划算。** 单独开尾部偏斜，F4 从 0.999 到 0.966，F1 只付出 +0.010，F3 +0.009。
   单独开全局放宽，F4 到 0.956 但 F1 +0.044、F3 +0.053，超出预算。
3. **cell damping 是破局点。** 加上它之后事件层可以放心放宽，因为多格题不会被重复放宽，
   全局宽度可以退回 1.0。这是 v3 拿到 0.900 的直接原因。

另外两条观察，留给下一版：

- **F2 在任何设置下都是 1.00。** 那些题是漂移/方向主导的，宽度动不了它们，只有真正读懂文本的
  方向判断才能动。
- **真实的左尾比任何对称设置都重**（mean z 恒为负）。v3 的偏斜缓解了一部分，按题族分别设不对称
  参数应该能更进一步。

---

## 仓库结构

```
agenthon/
├── README.md                  ← 你在读的这份
├── WORKLOG.md                 按时间顺序的完整工作记录
├── STATUS.md                  倒计时、现状、下一步
├── research/                  四个赛道的调研报告
│   ├── T1-coding.md           每份都含：合约、资源限制、指标定义、
│   ├── T2-forecasting.md      baseline 情况、坑位清单、三周可行性评估
│   ├── T3-simulation.md
│   ├── T4-analysis.md
│   └── F4-events.md           31 道危机题的历史背景（预填，待核对）
├── t2-work/
│   ├── engine/                我们的预测引擎（见上一节）
│   ├── Dockerfile             提交镜像（10-06 重写：基础镜像按 digest 钉死、适配只读根文件系统）
│   ├── requirements.lock      镜像和本地验收环境共用的一份精确依赖
│   ├── pack/                  build / 本地容器验收 / 填 digest / 校验 zip（见 pack/README.md）
│   ├── submission/            submission.json 提交描述文件
│   ├── v4_eval.py             按排行榜口径（除以 M0）的进程内快速评估
│   ├── run_all_gates.py       跑遍 104 题 + 检查四道门 + 输出目录规则
│   ├── realized.py            从兄弟题的面板里还原真实值
│   ├── score_local.py         用官方打分器算分、算比值
│   ├── grid_v2.py             v2 参数网格
│   ├── ablate_v3.py           v3 消融
│   ├── events_scan.py         把 104 题的事件特征打成一张表看
│   ├── *_table.csv            网格和消融的汇总表
│   └── scores_*.csv           每次运行的逐题分数（消融证据）
├── t4-work/                   Track 4 参赛代码（10-06 开工）
├── Agenthon2026-public/       ↓ 以下五个是上游官方仓，已 gitignore
├── track1-coding-public/         从 github.com/Agenthon-2026 克隆
├── track2-forecasting-public/
├── track3-simulation-public/
└── track4-analysis-public/
```

生成物（`t2-work/out*/`、`realized/`、日志）都已 gitignore，用下面的命令重新生成。

---

## 环境搭建

### 1. Python 3.13

官方工具包 `requires-python = ">=3.13"`。3.12 会在 pip 解析阶段直接拒绝安装，不是运行时报错。

### 2. 克隆

上游仓克隆在**本仓库根目录下**（与 `t2-work/` 同级，已 gitignore；脚本从 `t2-work/` 出发按 `../track2-forecasting-public` 找）。
T3 有 574 MB（Git-LFS 参考轨迹），不用可以不克隆。

```bash
cd agenthon
git clone --depth 1 https://github.com/Agenthon-2026/Agenthon2026-public
git clone --depth 1 https://github.com/Agenthon-2026/track2-forecasting-public
git clone --depth 1 https://github.com/Agenthon-2026/track4-analysis-public    # Track 4 用
```

**上游规则一直在改。每次开工先 `git -C <上游仓> pull` 并看 CHANGELOG**——我们就是因为没拉，晚了 20 天才知道截止延期了。

### 3. 装包

```bash
python -m venv .venv
.venv/Scripts/python -m pip install \
  "qfbench2-common @ git+https://github.com/Agenthon-2026/Agenthon2026-public.git@v2.6.0#subdirectory=common"
.venv/Scripts/python -m pip install ./track2-forecasting-public
.venv/Scripts/python -c "from qfbench2_common.contracts import CONTRACT_SET; print(CONTRACT_SET)"  # 应打印 1.1.0
.venv/Scripts/python -c "import importlib.metadata as m; print(m.version('qfbench2-common'))"     # 应打印 2.6.0
```

**一定要钉 `@v2.6.0`，不要装分支。** 装分支会让本地结果和评测结果在谁都没错的情况下分叉。
`qfbench2-common` 没有发布到 PyPI，只能用 git URL。（track2 仓的文字里还写着 v2.4.4，共享仓现行 pin 是 v2.6.0，我们跟共享仓。）
与镜像完全一致的本地验收环境由 `t2-work/pack/make_venv.sh` 建在 `t2-work/pack/.venv-docker`。

### 4. Windows 三个坑

| 坑 | 现象 | 解法 |
|---|---|---|
| **GBK 编码** | 读语料时 `UnicodeDecodeError: 'gbk' codec can't decode byte 0x94` | **每次跑脚本都设 `PYTHONUTF8=1`** |
| **`qfbench2-smoke` 拒跑** | `os.O_NOFOLLOW / os.O_DIRECTORY are unavailable on this platform` | 官方 smoke 工具在 Windows 上用不了。用我们的 `run_all_gates.py` 或 `t2-work/pack/check_outputs.py`（调的是同一个官方打分器和工具包的输出树策略），或者上 WSL / Docker |
| **换行符** | git 提示 `LF will be replaced by CRLF` | 无害，忽略 |

---

## 命令手册

全部从仓库根目录跑，且先 `export PYTHONUTF8=1`（Windows CMD 用 `set PYTHONUTF8=1`）。

### 跑一道题

```bash
cd t2-work && ../.venv/Scripts/python -m engine.forecast \
  --panels ../track2-forecasting-public/units/<题号> \
  --text   ../track2-forecasting-public/units/<题号>/text \
  --asof <题卡里的 data_cutoff> --out out_one/forecast.parquet
```

约 2–3 秒（含进程启动）。默认 v4；加 `--profile v1|v2|v3|v4` 换档，加 `--help` 看全部旋钮。

### 跑遍 104 题并检查四道门（约 5 分钟）

```bash
.venv/Scripts/python t2-work/run_all_gates.py --engine engine --platform-env --out-root t2-work/out_engine_v4
```

`--platform-env` 模拟平台的受限网络（`QFBENCH_NETWORK=restricted`、去掉所有 `MODEL_*`），并检查输出目录规则。
**期望输出：`admissible 104/104  fallbacks 0`。** 这是任何改动的底线，低于这个就是退步。

跑 text-blind 参考实现（生成 baseline 那一列）：

```bash
.venv/Scripts/python t2-work/run_all_gates.py --out-root t2-work/out
```

### 还原真实值（只需跑一次，约 1 分钟）

```bash
.venv/Scripts/python t2-work/realized.py
```

公开题不带真实答案，但一道题的答案往往藏在另一道题的面板里 —— 比如 2016-05-31 那题问 6 月 30 日的
英镑，而 `t2-F1-aud-on-hold-2016` 的面板一直延伸到 2016 年 7 月，里面就有 1.3242。

结果写进 `t2-work/realized/`，覆盖情况写进 `realized_coverage.json`。**90/104 题能覆盖**，
剩下 14 道是 EM transfer 题、月度宏观题和最晚的两个 as-of，只检查门槛不打分。

### 按榜单口径打分（以这个为准）

```bash
.venv/Scripts/python t2-work/v4_eval.py --out-dir t2-work/out_engine_v4   # 对 CLI 产出打分（除以重建的 M0、算术平均）
.venv/Scripts/python t2-work/v4_eval.py --profile v4                     # 进程内直接跑某个 profile
.venv/Scripts/python t2-work/v4_final_eval.py                            # v4 的 5 种子平均与压力测试
```

默认种子期望 0.9441，5 种子平均 0.9386。独立复核：`t2-work/audit_metric.py score|compare|noise|boot|variants|check-realized`。

### 旧口径打分（除以参考 CLI，约 4 秒，仅作历史对照）

```bash
.venv/Scripts/python t2-work/score_local.py \
  --out-dir t2-work/out_engine_v3 --baseline-dir t2-work/out --name v3
```

调的是官方打分器本体（`qfbench2_track_forecasting.scoring._main`），不是我们重写的。输出逐题
ratio、按题族的几何均值，写进 `scores_v3.csv`。

### 看每道题的事件特征

```bash
.venv/Scripts/python t2-work/events_scan.py
```

104 行一张表：每题的文档数、各类关键词密度、压力分数、是否判定有议息会议、判定的压力方向。
**改 `events.py` 之后先看这张表**，比直接看分数快得多。

### 看分布校准

```bash
.venv/Scripts/python t2-work/grid_v2.py zstats --out-dir t2-work/out_engine_v3
```

算真实值在我们分布里的 z 分数。`|z|>1.64` 的比例应该在 10% 到 15% 之间。远低于 10% 说明太宽，
远高于说明太自信。

---

## 想改进从哪下手

按投入产出排序。动手前先跑一遍基线确认 104/104，改完再跑一遍对比 ratio。

### 容易且有明确收益

1. **核对 `research/F4-events.md`。** 31 道危机题，我按公开历史预填了每道题窗口内发生了什么、
   是不是二选一、方向如何。**需要人核对**，尤其是「事件日期是否真的落在 as-of 到 target 的窗口内」。
   核对完就能验证 `events.py` 的判定准不准。
2. **扩充 `events.py` 的关键词表。** 现在约 100 条正则。看 `events_scan.py` 的输出，找压力分数
   明显偏低但实际是危机的题，去读它的语料，补词。
3. **`assets.py` 的压力方向表。** 现在覆盖股票因子、G10 货币、美债。遇到没覆盖的资产会退化成
   对称，不会出错但也不加分。

### 中等难度

4. **按题族分别设不对称参数。** 已知真实左尾比对称分布重，现在是一个全局偏斜量。F4 应该比 F1 偏得多。
5. **二簇混合的权重和偏移。** 现在是固定的 0.6/0.4 和 1.5 倍标准差。理论上应该由 binary 信号的
   强度决定。
6. **月度宏观题。** 只有 4 道，但现在的处理很粗糙（按 30.44 天折算步数）。

### 难但价值最高

7. **F2 的方向判断。** 27 道题，现在全是 1.00。需要从文本里读出「这个货币未来一个月倾向升还是贬」。
   这是唯一需要大模型的地方：House 模型每题只有 25 次请求。`engine/house.py` 已写好但默认关闭（没有端点可测）。注意官方警告：文本对中心的影响只敢是
   **标准差的几分之一**，不是几倍；乱加漂移会让分数比不读文本还差。
8. **镜像化与提交。** 10-06 已完成到「digest 已填」（`t2-work/Dockerfile`、`t2-work/pack/`、GitHub Actions）。剩下的人工步骤：
   把 GHCR 包改公开、用匿名 token 验证真的可拉取（`docker pull` 用的是你自己的凭证，验证不了任何东西）、
   由人运行 `pack.sh` / `qfbench2 submission pack` 打 zip 并上传。见本文开头「截止前必须由人来做的事」。

---

## 铁律

1. **Team Key 不进仓库、不进任何文件、不贴聊天、不告诉任何 agent。** 它等同于密码，只在 `qfbench2 submission pack`
   的隐藏提示符里由人输入。本仓库已扫描确认没有。
2. **永不崩溃。** 任何异常都要有兜底：写一份格式合法的保守答案，`exit 0`。崩溃记 4.0 且留在分母。
3. **绝对路径。** 写到 `--out` 给的路径。相对路径会落在容器里，随容器销毁，运行看起来成功但什么
   都没产出。
4. **schema 赢过文档。** 官方多份 README 已被证实与代码矛盾。裁决顺序：
   **scorer 源码 > card.toml > starter-pack AGENTS.md > 赛道 README**。
5. **`submission.json` 严格 12 个字段**（v2.6.0 schema：`schema_version, interface_version, competition_id, team_id,
   track, phase, category, image, image_access, models, license, descriptor_digest`，全部必填，`additionalProperties: false`）。
   官方文档早期说你「可以设 `house_endpoint_only`」，但 schema 不认这个字段，加了直接被拒。`category` 只能是 `api`
   （`byo-*` 已撤销）；`team_id` 由 `pack` 根据 Team Key 推出并写入，不要手填。
6. **不读 as-of 之后的任何东西。** 包括语料时间戳。代码里强制过滤，不依赖 staging 保证。
7. **不针对这 104 道题调参。** 最终排名用封存题集。我们的选参原则一直是「四个题族都不输」，
   而不是「总分最低」。
8. **不要把记住的历史结果写进分布。** 主办方会发布「闭卷回忆基线」（用主办方模型在没有面板和语料
   的情况下问同样的问题），专门抓「认出这是 2020 年 2 月所以把中心挪到崩盘位置」这种作弊。
   我们能做的是**在有事件信号时把分布放宽到包含那个方向**，不是把中心挪过去。

---

## 未决事项

| 事项 | 状态（2026-10-06 晚） |
|---|---|
| 规则合规（v2.6.0）、月度步数、rationale、看门狗 | **完成**，`t2-work/submission/CHECKLIST.md` |
| 引擎 v4 + 独立审计 | **完成**，默认 profile |
| 镜像构建与平台同等限制测试 | **完成**（GitHub Actions），T2 104/104、T4 11/11，digest 已填 |
| Track 4 | **完成**，`t4-work/STATUS.md` |
| 核对 `research/F4-events.md` | **完成**，31/31 行改写 |
| GHCR 包改公开 + 匿名拉取验证 | **人工待办** |
| 队长批准入队、确认队伍编号 | **人工待办** |
| 打包（Team Key）并在 CodaBench 上传 | **人工待办**，10-12 20:00 UTC 前 |
| T4 的 Final 描述文件 | 待准备（仿照 `t2-work/submission/submission.final.json`） |
| 本机 Docker | 起不来（WSL2 `HCS_E_SERVICE_NOT_AVAILABLE`），不影响提交 |
| F2 的文本方向层 / House 模型 | 未采用：没有过验证的信号，House 默认关 |
| 14/104 题无法本地打分 | EM transfer 题、月度宏观题、最晚两个 as-of。只检查门槛 |

---

## 参考

- 官网 <https://www.agenthon.net/> · 规则 `/rules/` · FAQ `/guides/faq/` · 公告 `/announcements/`（需登录；CodaBench 页面链接在这里）
- 基线定义 `track2-forecasting-public/docs/M0-BASELINE.md`、排行榜构成 `docs/CONCEPTS.md` §13
- 运行环境与提交次数 `Agenthon2026-public/docs/DEVELOPMENT-RUNTIME.md`；打包与 Team Key 说明 `Agenthon2026-public/starter-packs/track2/TEAM-CLAIM.md`
- 上游代码 <https://github.com/Agenthon-2026>
- 官方解题指南 `track2-forecasting-public/docs/SOLVER-PLAYBOOK.md` —— 主办方自己解了 103 道题反推
  出来的方法论，基本是答案，**强烈建议通读**
- 题族定义 `track2-forecasting-public/docs/CATEGORIES.md`
- 提交合约 `track2-forecasting-public/SUBMISSION_CLI.md`
- 联系主办方 admin@agenthon.net
