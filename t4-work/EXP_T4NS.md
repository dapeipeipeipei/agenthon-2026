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

## 待做（恢复后）

1. 在 `agent/predict.py` 加"政策路径锚"（k=0.5, M0=5），抽取器放 `agent/signals.py`（带 verbatim span 作 claim/premise），
   `explain.py` 加对应 _METHOD_TEXT；仅当 target 是 bps 变化、实体行有 start yield 且语料能抽到目标区间时触发，否则回退零。
2. vintage 区间：小样本 σ 下限 + 剔除死行零修订；检查 12 行标签不变。
3. auction：fit_level_weight 的网格已含 w=0/win=12，但现版选到 (0.5,6)？核对 `_robust_scale` 归一是否让 w=0 吃亏；
   σ 加合并残差下限。
4. 跑 `run_local.py --gate`、`robustness.py`、`nli_check.py`（11 unit，模型权重在 C:/Users/wensh/hf-model-cache）；
   逐 unit before/after 表；留一 unit 检验 k（只有 2 个 fomc unit，能做的是报告 k 的敏感性）。
5. 提交、推 `exp/t4ns`，中文汇报。
