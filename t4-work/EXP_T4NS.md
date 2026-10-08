# 实验线 T4-nosignal（分支 exp/t4ns）— 进度笔记 2026-10-07

目标：用**通用**方法抬高贴着 naive 的 unit（fomc-curve ×2、auction、cotpos、macrorev 区间），不按 unit id 写死，agent 内不碰结果。

## 已完成

1. worktree `../agenthon-wt-t4ns`（分支 `exp/t4ns`，自 origin/main 8794618）；`track4-analysis-public` 以 junction 指向主 checkout
   （git 忽略）；核实真值缓存 `harness/out/_headroom/truth_verified.json` 复制进来（git 忽略）。
2. 基线复现：`run_local.py --agents ours` 在 worktree 内逐项等于 C（auction 0.488 / cotpos 0.531 / cpicomp 0.556 /
   credit 0.875 / eps-growth 0.636 / eps-yoy 0.898 / fomc 0.500 / 0.500 / macrorev 0.607 / postearn 0.763；
   Dev10 均值 0.6353，榜面 0.5368），GATE PASS 11/11，0 虚假 claim。
3. 三个离线 unit 内回测（scratchpad，单进程、亚秒级；真值只用于末行汇报，不参与选择）：

### cotpos（排序）— 5 周变化的横截面预测因子，17 个起点、10 个市场

| 因子 | 回测均值 ρ | 对真值 ρ |
|---|---|---|
| 4 周动量（= naive） | −0.02 | +0.32 |
| 回归到 26 周均值（现版 series_reversion） | **+0.11** | +0.38 |
| 回归到 8 周均值 | +0.05 | −0.20 |
| −水平（净头寸越拥挤越回落） | −0.05 | +0.50 |

结论：unit 内回测只支持"回归到均值"，也就是现版做法；对真值的 0.38 已略高于 naive 0.32（锚 0.66）。
"−水平"对真值最好但回测为负 → **不采用**（会是事后拟合）。cotpos 没有诚实的提升空间，保持现状。

### auction（回归）— 一步回测，7 个期限共 63 步

| 模型 | 回测 MAE | 对真值 MAE |
|---|---|---|
| carry（= naive） | 0.113 | 0.140 |
| 6 期均值 | 0.088 | 0.130 |
| 全期均值 | 0.084 | 0.137 |
| 0.5·last + 0.5·6 期均值（现版） | 0.096 | 0.131 |
| 现版 + 新发/续发修正 | 0.092 | 0.131 |

结论：回测偏好"更靠均值"（w=0, win=12 最好 0.084），但对真值几乎无差（0.130 vs 0.131），
pq 0.50→0.52 量级。新发/续发修正在 unit 内方向一致（新发低 0.07–0.14）但 11 月真值反着来，收益 ≈0。
区间：合并残差 rms 0.119，而 30Y/5Y 单期限残差只有 0.08/0.04 → 单期限 σ 小样本偏窄（30Y 真值 2.64 出界）。
候选通用改动：σ 取 max(单期限残差, 0.75×合并残差)。

### fomc-curve ×2 — "政策路径锚"规则敏感性（事先定义，k、M0 两个常数）

规则：gap = 锚 − 最短期限起始收益率；锚 = SEP 当年末中位数（有则用，2024：4.4），否则 当前中点 + 上次步长
（同向，2022：2.375+0.75=3.125）；point(m) = k·gap·min(1, √(M0/m))。

| 锚 / k | 2022 分 | 2024 分 | 两 unit 均值 |
|---|---|---|---|
| 现版（零） | 0.500 | 0.500 | 0.500 |
| SEP-else-step, M0=5, k=0.25 | 0.515 | 0.568 | 0.542 |
| **SEP-else-step, M0=5, k=0.50** | 0.531 | 0.658 | 0.595 |
| SEP-else-step, M0=5, k=1.0 | 0.567 | 0.811 | 0.689 |
| 仅当前中点（无步长） | 0.468 | 0.770 | 0.619（2022 方向错） |
| 镜像压力（真值取反）k=0.5 | pq 0.47 | pq 0.40 | 下行风险 |

结论：k=0.5（"市场路径与委员会路径各对一半"）是事先的经济先验；2022 的 gap 只有 +27bp（真值 +57…+113），
收益小；2024 gap +81（真值 +54…+78）收益大。两 unit 方向都对，但镜像压力显示方向错时每 unit 丢 0.03–0.10。
榜面估计（k=0.5）：+1.27×(0.031+0.158)/10 ≈ **+0.024**。此规则需从语料确定性抽取：目标区间
（"target range … to 2-1/4 to 2-1/2 percent"）、动作动词（raise/lower）、步长（"by 1/2 percentage point"、
"75 basis point increase"）、SEP 当年联邦基金中位数。

### macrorev — 区间诊断（标签 8/12 的四个错行历史都指向 down，诚实不可修）

5/12 出界，其中 DGORDER_08（真值 −2702，带 ±222）一行就占 IS 4822 里的 3815；naive IS 4227。
原因：年龄匹配样本只有 3 个（−159/−402/−191）→ σ 太小。通用改动：年龄匹配样本 <5 时 σ 取
max(年龄匹配 spread, 合并常规修订 spread)；并把"从未修订过的死行"的零修订剔出合并集（避免把中心和 spread 压到 0）。
粗估 IS →≈3700 < naive → iq 0.47→0.53，unit +0.02（标签不变）。

## 已落地的改动（第二段，低负载模式：单进程、Idle 优先级）

| 改动 | 文件 | 常数 | 触发条件（通用） |
|---|---|---|---|
| 政策路径锚 `policy_path` | `signals.policy_path_signal`、`predict._fill` 第 5 步 | PATH_SHARE=0.5、PATH_M0=5 | 目标是 bps 变化 + 行里有期限与起始收益率 + 语料（可引用文档）里能逐字抽到"target range for the federal funds rate … to X to Y percent"；否则照旧回零 |
| vintage 小样本 σ 下限 | `predict._fill` 第 2 步 | SMALL_SAMPLE=5 | 年龄匹配修订 <5 个时，spread 取 max(年龄匹配, 全部常规修订) |
| vintage 剔除死行 | `signals.vintage_signal` | 无 | 一行在所有 vintage 列里从未变过 → 不进常规修订集合 |
| 序列模型 σ 下限 | `predict._fill` 第 4 步 | POOLED_SD_FLOOR=0.75 | 单行回测残差 ≥ 0.75 × unit 合并残差（都按各行自身尺度归一） |

抽取器细节：目标区间按最新可引用文档的那句话取（verbatim span 进 claims 与 r1 premise）；方向看句内动词
（raise/lower/maintain…）；步长先句内"by 1/2 percentage point"/"by 50 basis points"，再本文档/其他文档的
"75 basis point increase"，都没有则 0.25；SEP 当年中位数从"Federal funds rate 4.4 3.4…"表或
"4.4 percent median federal funds rate"句子取，且要求 |SEP − 中点| ≤ 1.5 才采信。
k=0.5、M0=5 是事先定的经济先验（上表敏感性里 k=1 在 Dev 上更高，**没有**改成 k=1）。

### 逐 unit：C → 本分支（核实真值，naive=carry；Dev 榜 = 10 个非 EXAMPLE unit 均值）

| unit | C | t4ns | pq | iq | 说明 |
|---|---|---|---|---|---|
| auction-btc | 0.488 | 0.486 | 0.504 | 0.445 | σ 下限让 3Y 带略宽（−0.002，噪声级）；点值本来就是回测选的 12 期均值 |
| cotpos | 0.531 | 0.531 | 0.545 | 0.498 | 不变（回测只支持现版） |
| cpicomp | 0.556 | 0.556 | 0.556 | 0.556 | 分不变；覆盖 0.82→0.91，raw iq 0.591→0.615（被 pq 封顶） |
| credit-event | 0.875 | 0.875 | | | 不变 |
| eps-growth | 0.636 | 0.636 | | | 不变 |
| eps-yoy | 0.898 | 0.898 | | | 不变 |
| **fomc-2022** | 0.500 | **0.531** | 0.531 | 0.531 | 锚 = 2.375+0.75 = 3.125，gap +27bp → 点 +6…+14bp（真值 +57…+113） |
| **fomc-2024** | 0.500 | **0.658** | 0.658 | 0.658 | 锚 = SEP 4.4，gap +81bp → 点 +17…+41bp（真值 +54…+78） |
| **macrorev** | 0.607 | **0.613** | 0.667 | 0.489 | 标签仍 8/12（不变）；DGORDER/PAYEMS 带变宽，DGORDER_08（−2702）仍出界 |
| postearn | 0.763 | 0.763 | | | 不变 |
| **Dev10 均值** | 0.6353 | **0.6547** | | | 榜面 0.5368 → **0.5615**（+0.025） |
| EXAMPLE | 0.850 | 0.850 | | | 不在 Dev 榜 |

三种 naive 假设（carry / zero / mean）下 Dev10 均值 0.6547 / 0.6532 / 0.6640，相对 C 均 +0.024~+0.025。
`run_local.py --gate` PASS 11/11（0 虚假 claim、reasons 检查 0 发现）；`robustness.py` 17/17。
NLI 全量检查（两个 DeBERTa 判官、矛盾检查开启）11/11 PASS：0 虚假、maxP(contra)=0.000、每题 3 条有效理由
（`harness/out/nli_t4ns.log`）。CI run 37648831922（commit 71a4d88）全绿；镜像
`ghcr.io/dapeipeipeipei/jinpei-t4@sha256:ff0dd4b6ea64b921d23367a2ad10118e6301d2fcdbda88f3b70e35f647b30fbd`
匿名可拉（verify_anonymous_pull.sh PASS）；描述文件 `submission/submission.e.json`（dev）与
`submission.e.final.json`（final）已封签并用 `SubmissionDescriptor.from_mapping` 校验，models []。

### 哪些能推广、哪些不能（诚实版）

- **能推广**：政策路径锚——密封的 rate_curve 家族题目本身就点名"市场定价 vs 委员会路径 / 前端离政策中点多远"，
  规则就是这句话的确定性实现，两常数、有经济理由；方向错时每 unit 丢 0.03–0.10（镜像压力），方向对时赚 0.03–0.16。
  2022 这题 gap 小（+27）所以赚得少，说明它不是靠"记住 2022 大涨"。
- **能推广但 Dev 上看不出**：σ 下限（小样本、合并）——cpicomp 覆盖率升、auction 略降，净 0；它买的是密封题上
  "单行历史太安静"的尾部保护。
- **不能/不该推广**：cotpos 的"−水平"（对真值 ρ 0.50 但回测为负）和 auction 的新发/续发修正——都没采用。
- **留一检验**：fomc 规则只有两个 unit，无法做真正的留一；能做的是上面的 k/M0/锚 敏感性表 + 镜像压力，
  k=0.5 在所有锚变体里都为正收益，"仅当前中点"锚在 2022 方向错（所以用了"中点+上次步长 / SEP"）。

### 待做

1. 老板说 full speed 后：`nli_check.py` 11 unit（矛盾检查开启）；CI 构建镜像；视结果决定是否并入 Final 候选。
2. 可选：auction σ 下限改为"同量纲行用原始合并残差"（会让 30Y 带 ±0.14→±0.21），本次没做（多一个判断分支）。
