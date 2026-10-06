# STATUS — 进度存档

**最后更新：2026-10-06** · 说「agenthon 2026」即可从这里继续。
完整来龙去脉看 [WORKLOG.md](WORKLOG.md)，设计和命令看 [README.md](README.md)。

---

## 倒计时（以 2026-10-06 为准）

| 时间 | 事件 | 剩余 |
|---|---|---|
| **2026-10-12 20:00 UTC**（纽约 16:00） | **最后一次 Dev 运行必须在此之前启动**；之后上传的不会再跑（10-13 08:00–12:00 UTC 维护） | **约 6 天** |
| 2026-10-12 23:59 AoE（= 10-13 11:59 UTC） | Registration + Development 正式关闭 | 约 7 天 |
| 2026-10-13 → 10-25 23:59 AoE | **Final + Verification 合并阶段**：每赛道**只能交一份** final；主办方用新种子复跑榜首做复现审核，不需要另交；同分时先上传者胜 | |
| 2026-12-09 → 12-13 | NeurIPS Atlanta，获奖队展示 | |

> 旧版写的「09-28 截止、Final 09-29 → 10-12」已作废——上游 09-16 就改了，我们 10-06 才发现（教训见 WORKLOG §9）。

**Dev 上传次数**：T2/T4 每队每天 5 次、Dev 期间每赛道共 20 次；平台标 `Failed` 的不扣次数，held/cancelled 的扣。

---

## 一句话现状

T2 引擎 v3 早在 09-13 就完成（104/104 可接纳，相对参考 CLI 的 ratio 0.900），但**一次都还没上传过**。
10-06 开了五条并行线把它变成可上传的提交，同时做 v4 和 Track 4。**剩下 6 天里最要紧的是：在 10-12 20:00 UTC 前至少完成一次真实的 Dev 上传并拿到逐题结果。**

---

## 已完成

| 项 | 时间 | 证据 |
|---|---|---|
| 四赛道调研，定主攻 T2、副攻 T4 | 09-08 | `research/T1…T4-*.md` |
| F4 危机事件表（预填） | 09-08 | `research/F4-events.md`（核对中，见下） |
| 本地打分台：真实值还原 90/104、官方打分器算分 | 09-13 | `t2-work/realized.py`、`score_local.py`、`run_all_gates.py` |
| 引擎 v1 / v2 / v3，回退链 v3→v2→高斯 | 09-13 | commit `44968e8` |
| 58 组参数实验（grid_v2 18 + ablate_v3 40），v3 冻结为默认 | 09-13 | commit `a1d1c46`；`grid_v2_table.csv`、`ablation_*_log.txt`、`scores_*.csv` |
| 中文 README、STATUS | 09-13 / 09-22 | commit `a1d1c46`、`fdc39a4` |
| 上游规则重新拉取并梳理（截止延期、zip 上传、BYO 撤销、25 次请求、v2.6.0、M0、输出目录规则） | 10-06 | WORKLOG §8 |
| 本地 `.venv` 工具包升到 v2.6.0（`CONTRACT_SET` 仍 1.1.0） | 10-06 | `importlib.metadata.version('qfbench2-common')` = 2.6.0 |
| WORKLOG + README「给队友」入口 + 本文件 | 10-06 | 本次提交 |

### 成绩（截至 v3，内部口径）

90 道可本地还原真实值的题，ratio = 我们 ÷ **官方参考 CLI**，几何均值，越低越好：

| profile | 总体 | F1 | F2 | F3 | F4 | 出 90% 区间 |
|---|---|---|---|---|---|---|
| 参考 CLI | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 23.7% |
| v1 | 0.925 | 0.712 | 1.009 | 0.849 | 1.076 | 26.3% |
| v2 | 0.939 | 0.842 | 1.006 | 0.874 | 0.999 | 11.4% |
| **v3（默认）** | **0.900** | **0.804** | **1.002** | **0.872** | **0.907** | **13.1%** |

⚠ 排行榜的分母是 **M0**（不是参考 CLI），三个分量分别归一化、跨题算术平均。按 M0 口径的数字：`TODO(回填，v4 线)`。

---

## 10-06 冲刺：五条线（全部进行中）

| # | 线 | 负责文件 | 状态 | 完成标志 |
|---|---|---|---|---|
| 1 | T2 规则合规 | `engine/io.py`、`forecast.py`、gates/打分脚本、`t2-work/submission/` | **进行中**（工作区有改动，未提交） | 104/104 可接纳 + 输出目录规则全过；`submission/submission.json` 就位 |
| 2 | T2 模型 v4 | `engine/model.py`、`events.py`、`house.py`、`V4_NOTES.md`、`v4_experiments.csv` | **进行中**（`v4_eval.py` 等评估脚本已在工作区，未提交） | 按 M0 口径不劣于 v3、四个题族都不输；端点缺失时仍零回退 |
| 3 | Docker 打包 | `t2-work/Dockerfile`、`t2-work/pack/`、`requirements.lock` | **进行中**（Dockerfile 已重写、pack 脚本已在工作区，未提交） | 镜像 build 成功、容器内 104 题全过、推到 ghcr.io 并匿名拉取验证、digest 填进 submission.json、`pack/README.md` 写好 |
| 4 | Track 4 参赛 | `t4-work/` | **进行中**（`t4-work/agent/` 已有代码，未提交） | 11 道公开题门槛全过（含 NLI ≥ 0.80）、镜像与提交描述就位、`t4-work/STATUS.md` |
| 5 | F4 事件表核对 | `research/F4-events.md` | **进行中** | 31 条逐条核对完毕 |

各线结果出来后回填到 WORKLOG §11 和本表。

---

## 下次开工，按这个顺序

### 第 0 步 · 拉上游、确认环境（3 分钟）

```bash
cd ~/OneDrive/Desktop/01-项目代码/agenthon
export PYTHONUTF8=1
for r in Agenthon2026-public track2-forecasting-public track4-analysis-public; do git -C $r pull --ff-only; done
#   ↑ 规则一直在变，先看各仓 CHANGELOG 有没有新条目
ls track2-forecasting-public/units | wc -l          # 104
.venv/Scripts/python -c "from qfbench2_common.contracts import CONTRACT_SET; print(CONTRACT_SET)"   # 1.1.0
.venv/Scripts/python -c "import importlib.metadata as m; print(m.version('qfbench2-common'))"     # 2.6.0
git log --oneline -15                                # 看五条线提交到哪了
```

### 第 1 步 · 确认引擎没坏（5 分钟）

```bash
.venv/Scripts/python t2-work/run_all_gates.py --engine engine --out-root t2-work/out_engine_v3
# 期望：admissible 104/104  fallbacks 0
.venv/Scripts/python t2-work/score_local.py --out-dir t2-work/out_engine_v3 --baseline-dir t2-work/out --name v3
# 09-13 的数字：overall 0.900 / F1 0.804 / F2 1.002 / F3 0.872 / F4 0.907
# （上游 09-27 改过练习语料、合规线改过 io.py，数字可能小幅变化；按 M0 口径另用 t2-work/v4_eval.py）
```

### 第 2 步 · 最高优先：完成第一次真实 Dev 上传

按 `t2-work/pack/README.md`（Docker 线写）：build → 容器内验收 → 推 ghcr.io → 匿名拉取验证 → digest 填进
`t2-work/submission/submission.json` → **由人**运行

```bash
qfbench2 submission pack --descriptor t2-work/submission/submission.json --team-number <队伍编号> --out submission.zip
# Team Key 在隐藏提示符里输入；不要放进命令行、文件或聊天
```

→ **由人**用队伍指定的 CodaBench 账号在 T2 页面上传 zip。看逐题结果，确认没有不可接纳的题。

### 第 3 步 · 若还有时间

1. 合入 v4（前提：按 M0 口径四个题族都不输），再上传一次对比。
2. Track 4 第一次上传。
3. Final 阶段（10-13 起）选定唯一一份 final 提交——**选上传过、逐题全部可接纳的那一份**，别交没在平台上跑过的版本。

---

## 阻塞项（只有人能做）

| 事项 | 谁 | 状态 |
|---|---|---|
| **批准 Wenshuo 入队** | 队长 Yuren | 09-22 时 Pending Approval，**需再确认**。不入队不算参赛 |
| 确认队伍编号（`--team-number`） | 任一队员 | 记录为 299，打包前在网站核对 |
| 在 `pack` 隐藏提示符输入 Team Key | 持 Key 的人 | 待第一次打包 |
| CodaBench 上传 | 队伍指定账号持有人 | 待第一次打包 |

---

## 已知的结构性问题（不是 bug）

- **本地 ratio 的口径和排行榜不同**（分母参考 CLI vs M0、几何 vs 算术均值）。v4 线在用 `v4_eval.py` 按榜单口径重评。
- **F2 的 27 道汇率题恒为 1.00 左右。** 宽度调不动，只有读懂文本判断方向能动；House 模型每题只有 25 次请求。
- **真实的左尾比任何对称设置都重。** v3 的 F4 仍有 43.8% 出 1.64σ。
- **14/104 题无法本地打分**（EM transfer、月度宏观、最晚两个 as-of），只检查门槛。
- **Dev 榜是练习板**：打 71 道 validation 题，练习题之间互相泄题，名次没意义，看逐题分数。

---

## 环境坑（每次都会踩）

| 坑 | 解法 |
|---|---|
| 读语料 `UnicodeDecodeError: 'gbk' codec` | **每次跑脚本都 `export PYTHONUTF8=1`** |
| 官方 `qfbench2-smoke` 报 `O_NOFOLLOW unavailable` | Windows 上用不了，用 `run_all_gates.py` / `t2-work/pack/check_outputs.py`，或在容器里跑 |
| 上游仓不在 / 过时 | 克隆在**本仓库根目录**；每次开工先 `git pull` |
| 装 `qfbench2-common` | 只能 git URL，钉 **`@v2.6.0`**，Python ≥ 3.13 |
| 容器只读根文件系统、uid 65534、256 PID | Dockerfile 里 `HOME=/tmp`、限线程池（Docker 线已处理） |

---

## 纪律

- **Team Key 等同密码**，不进仓库、不进文件、不贴聊天、不告诉任何 agent。只在 `pack` 的隐藏提示符里由人输入。
- **schema 赢过文档**。裁决顺序：scorer 源码 > `card.toml` > starter-pack `AGENTS.md` > 赛道 README。
- **不针对这 104 道公开题调参**。最终排名用封存题集，选参原则一直是「四个题族都不输」。
- **不能把记住的历史结果写进分布中心**。主办方有闭卷回忆基线专门抓这个；rationale 会被人工筛查。
- **Final 只有一份**：只交在 Dev 平台上真实跑过、逐题全部可接纳的版本。
- commit 只署用户一人，不加 Co-Authored-By。
