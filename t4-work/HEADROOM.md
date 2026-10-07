# Track 4 上限分析（HEADROOM）— 2026-10-07

依据：scorer 5.2.2 源码（`qfbench2_track_analysis/scoring.py::_composite` + 工具箱 `predictive_quality` /
`mean_interval_score`）、10 个 Development 练习 unit 的**已核实真实结果**（本次从一手公开数据源拉取，见 §4）、
我们 Dev 提交版本（d630d5f，镜像 bf2a16…）在本地的重算。脚本：`harness/headroom_truth.py`（拉真值）、
`harness/headroom_eval.py`（复刻打分 + 多种 naive 假设）、`harness/headroom_sweep.py`（区间宽度 / 收缩 / oracle 替换）。
真值缓存在 `harness/out/_headroom/`（git 忽略，镜像不含，agent 不读）。

---

## 0. 给构建 agent 的行动清单（按 Final 期望收益排序）

1. **推理理由（reasoning）是 Final 最大的、Dev 看不见的杠杆。** Final 总分 = −0.27 + 1.27×analysis
   **+ 0.25×reasoning**（不封顶）。reasoning 从 0.3 提到 0.55 ≈ 榜面 +0.06，相当于 analysis +0.05 ——
   比我们在任何 unit 上能现实拿到的预测提升都大。做法（通用，不按 unit 写死）：
   - 每个 unit **永远交满 3 条**（覆盖率分母是隐藏标准理由总数，少交只会更低）；三条讲**三个不同驱动因素**
     （例：CPI = 汽油价格走势 / 住房惯性 / 季节性；银行 EPS = 投行费用 / 净息差 / 拨备）。
   - premise 逐字引原文且含数字；mechanism 写"因为 X 所以 Y"的经济机制，而不是"我们的算法取了均值"；
     answer_implication 明确指向**本 unit 的答案字段**（标签/方向/幅度），并与 entity_predictions 一致。
   - House 模型最适合干这件事（写机制文字），**而不是**用它来"回忆"结果（见第 6 条）。
2. **分类题的标签准确率是单位收益最高的预测项。** 锚定公式下，naive 准确率 0.5 的 8 实体 unit，每多对 1 个
   标签 = pq +0.125 = unit 分 +0.0875（若有数值 y，区间封顶同时抬高，实际更多）。文本型家族（信用事件、
   财报反应）里 LLM 读文件判断（流动性、债务到期、亏损、诉讼/工会风险）是确定性规则做不到的；但必须
   **有置信门槛**，低置信时保持确定性/naive 标签。本次真值下我们错的标签：credit YELL、postearn AAPL、
   macrorev 4/12（PAYEMS×2、INDPRO-07、PI-08）。
3. **区间按单位内回测残差校准，半宽 ≈ 1.8σ，宁宽勿窄。** 正态下期望区间分（IS）在半宽 1.645σ 最小
   （4.12σ）；1.0σ 贵 29%、0.5σ 贵 116%；2.0σ 只贵 5%、2.5σ 贵 23%。我们现在的带宽"全局倍数"已近最优
   （k=1 时 10 个 unit 平均 IS 比 k=0.75 / 1.5 都好），但**逐 unit 两头错**：eps-growth / eps-yoy /
   fomc-2024 / EXAMPLE 偏宽（k≈0.5–0.75 更好），fomc-2022 / postearn / cpicomp 偏窄（k≈1.5 更好）。
   → σ 用"同一预测器在截止日前历史上的残差"（多实体合并、按量纲归一）估计，小样本再乘 1.2 左右。
   注意封顶：iq ≤ max(0.5, pq)，**只有 pq>0.5 的 unit 区间才值钱**——尤其"分类 + 数值 y"的 unit
   （eps-yoy、macrorev、postearn、EXAMPLE），标签对了以后区间可拿到 0.3×pq。
4. **点预测与标签必须一致。** postearn 我们三家都判 positive_reaction 却给 point_forecast = 0、区间
   ±8.2：数值区间腿白丢分，推理评审的 answer_consistency 也会扣。规则：分类题的点预测取"该标签条件下的
   期望值"（≥ 阈值一侧），区间覆盖该标签区域。
5. **历史修订/序列统计要稳健。** macrorev 的 PI 点预测 25,090（最新值 24,803）是被 2024-09-27 年度综合修订
   （+3%）这一次性跳变拖偏的；用中位数/截尾均值，或剔除 |修订| > 5×MAD 的轮次（年度基准修订），通用且无害。
6. **不要相信 LLM"记得"的结果，也不要用 Dev 分数挑 A/B。** Dev 10 个 unit 全部是"memorization-exposed"
   （结果早于模型训练截止）；House 模型在 Dev 上可能凭记忆答对，Final 密封题在训练截止之后，记忆=幻觉。
   候选 B 若在 Dev 上分数更高，**不能**作为 Final 更好的证据；判断 B 用"去掉回忆后的推理/标签质量"。
   存储任务答案查表在 Final 明令禁止（TRAINING-POLICY："storing task-answer lookups"）。
7. **没有信号时就贴 naive。** 点预测等于 naive → pq 恰好 0.5、iq 封顶 0.5、unit 分 ≤0.5；无信号的偏离
   期望为负（pq<0.5 时区间腿也不再受保护）。现在的回退策略是对的，保持。
8. **in-corpus 高频代理 → 目标的映射在 unit 内回归。** 例：周度汽油价 → CPI 汽油 SA 环比，用语料里 2024-02…09
   的 8 对数据拟合（a=−0.86, b=0.60），10 月预测 −2.29（真值 −0.87；naive −4.10；我们 −3.27），误差 3.23→1.42。
   通用写法："若语料有比目标更新/更高频、且与目标同名或同类的序列，用截止前历史拟合映射"。样本小，收益中等。
9. **claims：保持逐字引用、每实体 1–3 条即可。** 不假的 claim 不加分（NEUTRAL）；逐字引用不送 NLI，Final
   的矛盾检查也伤不到。超过 3×E 条不再稀释罚分。现状 0 条虚假，别为了"更多证据"引入改写句。
10. **本地评估换成核实真值。** `approx_truth.py` 有错：AMGN 实际 **up**（2.57 vs 2.45，记忆写成 down 2.25），
    DOW 0.68（写成 0.27），TMO 3.51（写成 2.84）；auction / cotpos / macrorev 现在也有真值了。
    建议 run_local 改读 `out/_headroom/truth_verified.json`（我不改 builder 的文件）。若用这些真值调常数，
    按 issue #24 裁定在 ARTIFACT_PROVENANCE 记录来源和检索日期（§4 已列好）。

---

## 1. 榜面分数怎么来的（scorer 5.2.2 源码）

每个 unit：

| 部分 | 公式 | 说明 |
|---|---|---|
| 分类 pq | acc 锚定：acc≥a → 0.5+0.5(acc−a)/(1−a)；acc<a → 0.5·acc/a；a = naive 的 acc | a=1（naive 全对）时只有全对才得 1，否则 0.5·acc |
| 排序 pq | (Spearman+1)/2，同样锚定，a = max(naive 质量, 0.5) | 常数预测 = 0.5 |
| 回归 pq | nMAE/(nMAE+MAE) | 任一实体缺/NaN → 整 unit 0；naive 全对时非全对 = 0 |
| iq | min( nIS/(nIS+IS), max(0.5, pq) )，IS = 宽 + 20×出界距离（行均值） | 只在有数值真值且 interval_leg 未关时计；credit 关了 |
| 组合 | 0.7·pq + 0.3·iq；无区间腿时 = pq（不乘 0.7） | 域 [0,1]，W=0 |
| 忠实度 | × (1 − F/(F+min(T,3E)))，F=虚假 claim 数 | 无虚假 → ×1；Dev 不跑 NLI，确定性规则照跑 |
| 榜面 | −0.27 + 1.27 × mean(unit 分) | Final 另加 0.25×reasoning（离线评，Dev 不显示） |

**Dev 榜是哪些 unit**：README 说"`units/`（ten more）由组委会在 Development 榜上打分"——即除 EXAMPLE 外的
10 个练习 unit。核实：我们 Dev 版答卷在核实真值 + 三种 naive 假设下，10 个 unit 均值 0.630–0.642 → 榜面
**0.530–0.545**，实际 **0.5394**（analysis 0.637）；含 EXAMPLE 的 11 个则是 0.556–0.569。**本地复刻与
真实榜面误差 ≤0.01，且更支持"10 个 unit 取均值"**。

**0.988 意味着什么**：analysis 均值 0.9906，10 个 unit 每个都要 ≳0.95。回归题需要 MAE ≤ naive 的 ~3%
**且** IS ≤ naive 的 ~3%（几乎精确值 + 几乎零宽区间）；分类题全对且数值区间几乎贴死。我们实验：
"所有点/标签换成真值、保留我们的带宽" → 0.924（榜面 0.904）；要到 0.988 还得把带宽压到近零。
**结论：前 3 名（0.988 / 0.930 / 0.877 ≈ analysis 0.99 / 0.945 / 0.90）只能来自已知答案**（LLM 记忆这些
memorization-exposed 的 2022–24 事件，或查表）。第 4 名 0.772（analysis 0.82）也远超任何诚实预测的现实
上限。Final 密封题在训练截止之后，这些方法会塌到真实预测能力，**Dev 排名对 Final 几乎没有信息量**。

## 2. 逐 unit 上限（我们 Dev 版 vs 完美预测；naive 取"上期值"假设）

| unit | 我们 | pq | iq（封顶） | 完美点/标签+我们的带 | 主要限制 |
|---|---|---|---|---|---|
| auction-btc（回归） | 0.488 | 0.504 | 0.449 | 0.882 | pq≈naive；带比 naive 差 |
| cotpos（排序） | 0.531 | 0.545 | 0.498 | 0.874 | pq：ρ=0.38，naive 若是 4 周动量则锚 0.66 |
| cpicomp（回归） | 0.534 | 0.534 | 0.534（raw 0.57） | 0.912 | pq；服装/二手车/汽油大误差 |
| credit（分类，无区间腿） | 0.875 | 0.875 | — | 1.000 | YELL 漏判 |
| eps-growth（回归） | 0.636 | 0.636 | 0.636（raw 0.68） | 0.905 | pq；JPM/C/WFC 方向偏乐观；带偏宽 |
| eps-yoy（分类+数值） | 0.898 | 1.000 | 0.660 | 0.898 | **区间**：6/6 全对，带偏宽（k=0.75 IS −25%） |
| fomc-2022（回归） | 0.500 | 0.500 | 0.500（raw 0.72） | 0.954 | 无信号；+57…+113bp 不可预测 |
| fomc-2024（回归） | 0.500 | 0.500 | 0.500（raw 0.68） | 0.905 | 同上（+54…+78bp） |
| macrorev（分类+数值） | 0.608 | 0.667 | 0.472 | 0.949 | 标签 8/12；PI 点被年度修订拖偏 |
| postearn（分类+数值） | 0.753 | 0.833 | 0.565 | 0.963 | AAPL 错；点=0 与标签矛盾 |
| EXAMPLE（不在 Dev 榜） | 0.850 | 1.000 | 0.500 | 0.850 | naive（inline）本身全对 → 只能靠区间 |
| **Dev 10 均值** | **0.632** | | | **0.924** | 榜面 0.533 vs 0.904 |

- 三种 naive 假设（上期值 / 零 / 6 期均值）下 10 unit 均值 0.630 / 0.632 / 0.642，结论不变；naive 的**区间宽度**
  未公开，iq 的绝对值是推断，**只看相对**。
- 忠实度在所有 unit 都不是限制项（0 虚假，factor=1）。
- 候选 A（7c18403）在 11 个 unit 的预测与 Dev 版逐项相同（只改了理由/检索），本地分数一样。

## 3. 到 0.9+ 需要什么（以及为什么 Final 不需要追它）

- **数学上**：0.9 analysis ≈ 每个回归 unit MAE ≤ naive 的 1/9、分类几乎全对、区间宽度 ≤ naive 的 ~1/9 且覆盖。
  对收益率、CPI 分项、拍卖认购倍数这类噪声目标，诚实预测做不到；语料是有意不含答案的（抽查：JPM/USB/GS 的 8-K 只见封面与证券列表，C 的 8-K 是会计主管变动，
  META 8-K 是董事会变动；拍卖、COT 表只到截止日）。**LLM 读语料抽不出答案**，
  只能抽**判断依据**（信用风险措辞、管理层指引）——这对分类题和理由有用，对回归题点值帮助有限。
- **现实目标**：Final 上诚实 agent 大致在 analysis 0.55–0.70（榜面 0.43–0.62）+ reasoning 奖励。拉开差距的是
  ①reasoning ②分类标签 ③有信号 unit 的区间校准，而不是追 Dev 的 0.988。
- **期望收益排序（Final 榜面，粗估）**：reasoning +0.03~0.06 ＞ 文本型分类标签（LLM 带门槛）+0.01~0.03 ＞
  区间残差校准 +0.01~0.02 ＞ 稳健统计 / 点-标签一致 / 高频代理映射 各 +0.005~0.01。
  风险最大的是"LLM 预测数值/标签不设门槛"——Dev 上会被记忆美化，Final 上可能负收益。

## 4. 真实结果来源（检索日期 2026-10-07；全部为首次发布值）

| unit | 来源 | 状态 |
|---|---|---|
| fomc-curve-20220728 / 20240918 | FRED DGS2/3/5/7/10/30 日度（起点值与 task 表 start_yield_pct 一致） | 已核实 |
| cpicomp-202410 | ALFRED vintage 2024-11-13（首发），(10月/9月−1)×100 | 已核实 |
| macrorev-20240930 | ALFRED vintage = 各行 resolving_release_date | 已核实（7 up / 5 down） |
| auction-btc-202411 | TreasuryDirect TA_WS securities/search（2024-11 拍卖） | 已核实 |
| cotpos-202411 | CFTC Socrata 6dca-aqww（legacy futures-only）2024-11-26 报告 | 已核实 |
| eps-growth-2024Q3 / eps-yoy-2023Q2 / EXAMPLE | SEC XBRL companyconcept EarningsPerShareDiluted，取最早 filed 的季度值 | 已核实 |
| credit-event-2023 | 公开记录：BBBY 2023-04-23、YELL 2023-08-06、RAD 2023-10-15、WE 2023-11-06 申请破产 | 记录，未下载 |
| postearn-20240201 | 公开收盘价记录（stooq 未取到）：AAPL −1.59、AMZN +6.81、META +19.27（相对 SPY，%） | 记录，标签方向确定 |

与 `approx_truth.py`（记忆版）的差异：eps-yoy AMGN 方向错（实为 up）、DOW/TMO 数值错；fomc 各期限差
1–5bp；cpicomp 交通服务 0.44（记忆 0.3）、新车 −0.05、食品 0.16 等小差；其余一致。
跨 unit 互证：cotpos 的 MKT_SNAPSHOT（10-30：2Y 4.15、10Y 4.29）与 FRED 一致，可作 fomc-2024 的中途核对，
但只能用于本地评估，**agent 内绝不跨 unit 取数**。

## 5. 复现

```bash
cd t4-work
PYTHONUTF8=1 ../.venv/Scripts/python harness/headroom_truth.py                 # 拉真值（有缓存）
git archive d630d5f t4-work/agent t4-work/bin | tar -x -C <scratch>/ag_dev
PYTHONUTF8=1 ../.venv/Scripts/python harness/headroom_eval.py --agent-root <scratch>/ag_dev --tag dev
PYTHONUTF8=1 ../.venv/Scripts/python harness/headroom_sweep.py --tag dev
# 评当前工作区答卷：headroom_eval.py --answers harness/out/ours --tag cur
```
