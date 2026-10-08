# Track 4（可解释性）进度 — 2026-10-08（第四轮：E2 = E + 审查修复 + 范围护栏，决赛用 E2）

一句话：E2 取代 E 作为决赛候选。E 在 C 之上加了两类**通用**改动（政策路径锚、区间下限）；独立审查修了两个抽取 bug
（裸分数 "1/2"、"Effective federal funds rate" 表行），E2 再加范围护栏（只对国债/主权收益率单元触发）、gap 钳位 ±150bp、
持稳语句与零利率区间解析。11 道公开题预测与 E **逐位相同**：Dev10 均值 0.6353(C) → **0.6547**（榜面 0.5368 → **0.5615**）。
镜像由 CI 构建（run 37714167931，main 590884d）、匿名可拉；gate 11/11、异常用例 17/17、NLI 全量 11/11（0 虚假、0 矛盾）、
合成变体 17 个全部符合预期（细节与回测见 `EXP_T4NS.md`；常数与 issue #24 披露见 `submission/ARTIFACT_PROVENANCE.md`）。

| 候选 | 镜像 digest | Dev 描述文件 | 决赛描述文件 |
|---|---|---|---|
| **E2（推荐决赛）** | `sha256:7bb0beef49976b1a161a03be4005a77ebafc5abce410419c7d27ae63ab3b13c6` | `submission/submission.e2.json` | `submission/submission.e2.final.json` |
| E（已被 E2 取代；无护栏，含两个抽取 bug） | `sha256:ff0dd4b6ea64b921d23367a2ad10118e6301d2fcdbda88f3b70e35f647b30fbd` | `submission/submission.e.json` | `submission/submission.e.final.json` |

打包：`.venv\Scripts\python pack_all.py t4-e2`（Dev）/ `pack_all.py t4-e2.final`（决赛）。

### 第六轮（10-08，分支 exp/t4robust）：未见家族稳健性 —— 14 个从零合成的 unit，E3 的 11 题逐位不变

决赛 unit 的家族"比公开的多"，所以用 `harness/synth_units.py` 从零造了 14 个公开集没有的形状（排序/Spearman 的 EM 外汇横截面、
32 行商品大样本、2 标签概率题、5 标签评级题、全 spans 语料、unicode/标点名称+CRLF+孤立代理字符、纯散文无表、task.json 多余字段/
嵌套列/doc_id 不一致、200 篇语料、行里没有先验列、ECB/BoE/BoJ 政策路径、实体无文档/场外文档），每个都跑官方 gate
（`score_unit` 无真值→ `unrankable`）、claim 规则、`check_submitted_reasons`、3 条理由、区间合法。**改前 5/14，改后 14/14**；
`robustness.py` 现在 31/31（含这 14 个，`--synth` 只跑它们）；mock House 31/31；NLI 公开 11/11 + 合成 9/9 全 0 虚假 0 矛盾。

修的通用弱点（全部只在原来会出错/退零的路径上生效，11 道公开题预测/标签/区间/claims/理由**逐位相同**，Dev10 仍 0.6547）：

1. **政策路径扩到非美央行**（`signals._generic_policy_rate`）：语料里没有 "target range for the federal funds rate" 时，退而解析
   "deposit facility rate / Bank Rate / overnight call rate / policy rate … to|at X%" + 决策动词（lower/raise/remain…）+ 步长；
   带 expect/priced/would 等字样的句子不算决策。ECB/BoE/BoJ 合成题从全零变成有方向的路径锚。
2. **政策路径优先于共享快照里的收益率水平序列**，且 bps 目标配 percent 序列会 ×100（之前 Bund10Y 的区间是 ±0.92 "bp"——单位错）；
   共享横截面表的列名匹配必须有一个字母 token（"30Y" 列不再靠数字 "30" 匹配到 "30-year"）。
3. **概率型分类题的标签与概率一致**（`_prob_labels`）：词表不是 event/no_event 时，按 "probability of …" 短语和否定前缀
   （no_/non_/not_）定正负标签；p<0.5 给负标签（之前 ease/no_ease 题给 p=0.1 却标 ease）。House 合并层同样生效。
4. **状态 quo 标签**：`affirm/retain/keep/stay/remain/pause/status quo…` 归为 middle；点恰在参考值且无 middle 标签时取语义未知
   标签而不是词表第一个（5 标签评级题之前全给 upgrade_2plus）。
5. **水平目标无锚**：行里没有先验列、文档里也没有表的行，用其他行预测的中位数+覆盖其离散度的区间（`peer_median`），不再预测 0。
6. **区间下限**：百分比增速/收益类锚的回退半宽 ≥1 pp；分类题的回退半宽 ≥ 阈值区间半宽（之前 4.5% 增速给 ±0.675 pp）。
7. **全 spans 语料也能出 3 条理由**：premise 可引用可读但不可引用（spans 格式/role 非 corpus/doc_id 与文件名不一致）的文档，
   只是不带 citations；claims 仍只引 flat text。文档内 `doc_id` 与文件名不一致的永不引用。
8. **答案字节上限**再加两档压缩（点偏 ≤15%/30% 带宽），25–35 行能过 3000 字节；**≥~37 行结构上不可能过**（每行 ≥77 字节裸 JSON），
   这种 unit 谁都拿不到推理分。
9. 方法文案去家族化：`series_reversion` 非持仓题不再说 "crowded positions"，`prior_probability` 非信用题不再说 "distress language"。

残余风险：非美政策路径只在 3 个合成样本上验证过，方向逻辑与 E3 的 FOMC 规则相同（锚=现行利率+上一步，gap 钳位 ±150bp）；
2 标签概率题无信号时仍是 0.1 先验；`_NOT_DECISION` 偏保守（句中带 "market" 就不算决策，退回零变化）。

建议：这些改动只动未见家族的路径，公开 11 题逐位相同，**值得重建为 E4**（CI 构建→镜像内 11/11→换 digest 重新封签）；
若时间不够，E3 照旧可用，差别只在决赛的未见家族上。

### 第五轮（10-08）：E3 = E2 预测 + 重写的推理理由 + ZLB 区间下限 —— **决赛用 E3**

- 11 题预测/标签/区间/claims 与 E2 逐位相同（Dev10 0.6547 / 榜面估 0.5615；E2 实测 0.5673）。改的只有决赛才计分的 `submitted_reasons`（+0.25·reasoning）和零利率下限 `ZLB_FLOOR_BP_30D=15`。
- 镜像 `ghcr.io/dapeipeipeipei/jinpei-t4@sha256:1c9af64ad9b293badae707f31a2e20134c7e03bdd3343261086917fbb85a100e`（CI run 37821710268，匿名拉取 PASS）；描述文件 `submission/submission.e3.json` / `submission.e3.final.json`；打包 `pack_all.py t4-e3` / `t4-e3.final`。
- 验证：gate 11/11、0 虚假；robustness 17/17；NLI 全量 11/11（maxP(contra)=0.000）。细节见 `ARTIFACT_PROVENANCE.md` 的 Candidate E3 与分支 exp/t4polish。

## E2 相对 E 改了什么

- 范围护栏：目标名/题干须提到 yield / Treasury / sovereign / government bond / gilt / bund / curve，且不含 mortgage /
  credit spread / CDS / swap spread / corporate / municipal；按揭利率、信用利差之类共用 FOMC 语料的 bps 单元不再触发。
- gap 钳位：|锚 − 前端收益率| 超过 150bp 按 150 计。
- 解析覆盖："maintain … at X to Y percent"（持稳 → 步长 0，不再去别的文档找步长）、"0 to 1/4 percent"。
- 审查修复（254d546）："by 1/2 percentage point" 正确解析为 0.50；SEP 表行须有 projection/median 上下文且非 "Effective"。

## 第三轮：候选 E（2026-10-07 晚，已被 E2 取代）

### E 相对 C 改了什么（全部通用，不按 unit 写死；常数 ≤3）

1. **政策路径锚**（`policy_path`）：目标是"收益率变化 bps"且行里有期限与起始收益率时，从语料可引用文档逐字抽联邦基金
   目标区间（中点）、上次步长与方向、以及（若有）SEP 当年中位数；锚 = SEP，否则 中点+上次步长；
   点 = 0.5 × (锚 − 最短期限起始收益率) × min(1, √(5/期限))。抽不到就照旧回零。两个 fomc unit：0.500/0.500 → 0.531/0.658。
2. **vintage 区间**：年龄匹配修订样本 <5 时 spread 取合并常规修订的 spread；从未修订过的行不进合并集合。macrorev 0.607 → 0.613（标签不变 8/12）。
3. **序列模型 σ 下限**：单行残差 ≥ 0.75 × unit 合并归一残差。cpicomp 覆盖 0.82→0.91（分不变），auction −0.002。

其余 unit 逐位不变。三种 naive 假设下相对 C 均 +0.024~0.025。k=0.5、M0=5 为事先经济先验（未按 Dev 调大）。
风险：政策路径锚方向错时每 unit 丢 0.03–0.10（镜像压力测试）；是有理由的对称下注。

### 建议

- **决赛用 E**（E ⊇ C，其余 unit 逐位相同；多出来的只有 rate_curve 家族的方向下注和更稳的区间）。
- Dev 可传 `t4-dev-e.zip` 确认平台分（预期榜面 ≈0.56）。

---

## 第二轮：候选 C / D（2026-10-07 下午）

一句话：本地改用**核实过的首发真实结果**打分（与真实榜面误差 ≤0.01）；新做了 C（无大模型）和 D（C + House），
都由 CI 构建、镜像内 11/11 合格、可匿名拉取；**决赛推荐 C**（D 的 House 效果本地测不了，且 Dev 分会被模型"记忆"美化）。

| 候选 | 镜像 digest | Dev 描述文件 | 决赛描述文件 |
|---|---|---|---|
| **C**（无大模型，推荐决赛） | `sha256:c9b5ddcd2b028f12224858d74b13a5f7c76c816ad436bd73ab55ccf6dd05acee` | `submission/submission.c.json` | `submission/submission.c.final.json` |
| **D**（C + House） | `sha256:140251fbcc42cbbcfe210910c1d31c10f23f705c7781ed2b7c5c2379399200ad` | `submission/submission.d.json` | `submission/submission.d.final.json` |

CI：cand/t4-c run 37589421609、cand/t4-d run 37589424854 全绿（镜像内 11/11、异常 17/17、假 House 全模式、
House 开着 17/17、镜像内 House 联调 4/4）。描述文件都已封签并用工具箱校验；决赛版 competition_id=agenthon2026-analysis-final、phase=final。

### 核实真值下的本地分（naive = 带上期值；Dev 榜 = 10 个非 EXAMPLE unit 均值）

| unit | 现版/A/B（预测相同） | C | 说明 |
|---|---|---|---|
| auction-btc | 0.488 | 0.488 | |
| cotpos | 0.531 | 0.531 | |
| cpicomp | 0.534 | **0.556** | 周度汽油价→CPI 汽油的 unit 内映射（−3.27→−2.29，真值 −0.87） |
| credit-event | 0.875 | 0.875 | YELL 仍漏判 |
| eps-growth | 0.636 | 0.636 | |
| eps-yoy | 0.898 | 0.898 | |
| fomc-2022 / 2024 | 0.500 / 0.500 | 0.500 / 0.500 | 无信号，贴 naive |
| macrorev | 0.608 | 0.607 | 标签仍 8/12（PI-08 改对、PI-07 改错）；剔除年度修订跳变 |
| postearn | 0.753 | **0.763** | 财报窗口用更宽的波动；点值放到标签一侧 |
| **Dev 10 均值** | 0.6322（榜面 0.5329，真实 0.5394） | **0.6353（榜面 0.5368）** | |
| EXAMPLE | 0.850 | 0.850 | 不在 Dev 榜 |

D：House 只在平台上能连，本地分 = C（House 不可用时完全退回 C）；假服务器只验证管道。B 已上传（ID 966354，待出分），
它的 Dev 分**不能**用来判断决赛好坏（练习题在模型训练截止之前，可能靠记忆）。

### C 相对 A 改了什么（全部通用，不按 unit 写死）

1. **推理理由（决赛 +0.25×reasoning，最大的隐形杠杆）**：每个 unit 一定交满 3 条；三条讲不同的驱动因素
   （题目点名的优先）；前提逐字引原文且含数字；机制写经济含义（"因为 X 所以 Y"），不再写"算法取了均值"；
   结论列出本 unit 的提交值，和答案一致；超字节上限会自动补一条。
2. **点和标签一致**：分类题的点值放在所判标签那一侧（例如判 positive_reaction 就不再给 0）。
3. **修订表稳健统计**：自动识别"年度/基准修订"这类一次性跳变并剔除；用中位数/截尾均值；
   按"截止前最新版本→结算版本之间有几次发布"累加预期修订；区间 1.8σ（宁宽勿窄）。
4. **高频代理映射**：语料里若有与目标同名、更高频的序列（如周度汽油价），在 unit 内用截止前数据回归映射（相关 ≥0.7 才用）。
5. **财报窗口收益**区间用事件波动（6.5%）。
6. House 层（只在 D 开启）：提示词明确"截止日之后的结果未知、只能用给出的段落推理"；合并权重降到 0.5/0.3；
   标签门槛 0.6/0.75；合并后再做点/标签一致；House 写的理由不足 3 条时用确定性理由补满。
7. 决赛限额可用环境变量调整：`T4_HARD_ALARM`、`T4_HOUSE_BUDGET`、`T4_HOUSE_CALL_TIMEOUT`、`T4_HOUSE_PARALLEL`、`T4_HOUSE_MAX_REQUESTS`。

### 决赛才有的两项检查（Dev 看不到）——本地已跑，全部干净

- 完整 NLI 评审（两个 DeBERTa 模型，矛盾检查开启）：现版、C、D（假 House 输出）三套答卷 × 11 个 unit，
  虚假 claim 0、矛盾 0（claim 全是原文逐字，不送 NLI）。脚本 `harness/nli_check.py`（模型权重在本机 `C:/Users/wensh/hf-model-cache`）。
- 严格 reasons 检查（`check_submitted_reasons`）：全部 0 条发现。

### 打包命令（仓库根目录，Team Key 隐藏输入）

```
.venv\Scripts\python pack_all.py t4-c t4-d                 # Dev：../agenthon-submissions/t4-dev-c.zip、t4-dev-d.zip
.venv\Scripts\python pack_all.py t4-c.final                # 决赛（10-13 起）：t4-final-c.final.zip
```

### 建议

- **决赛用 C**：本地核实真值分最高（0.6353）、0 虚假、NLI/理由检查全过、不依赖 House 延迟和次数上限。
  D 只有在 B（966354）出分后、并且我们能确认 House 的提升不是来自"记忆"时才考虑——Dev 分做不到这一点，所以默认不选 D。
- Dev 上传顺序：先等 B 出分；然后传 **t4-dev-c.zip**（预期榜面≈0.537，用来确认 C 在平台上跑通）；D 可选（只看 House 管道是否正常）。

## 第一轮：候选 A / B（2026-10-07 上午）

一句话：**首次 Dev 真实成绩 0.5394（榜面，第 7/83）**；本地估计 0.596，偏乐观但同方向。
为冲前四做了两个候选镜像，都已由 CI 构建、在平台同等限制下 11/11 合格、0 虚假引用、可匿名拉取：

| 候选 | 镜像 digest | 描述文件 | 和现版差别 |
|---|---|---|---|
| **B（推荐先传）** 确定性 + House 大模型 | `sha256:8ab8388e7cf5bfa441c4902b2cfb914bd27c5ebaf440f1358d3c5718b6fc5470` | `submission/submission.b.json`（`models[]` 已填 House 那一行并重新封签） | 平台注入 MODEL_* 时调 House：读按截止日过滤后的语料段落，给每个实体出预测/区间/标签/证据，再写 3 条推理理由；任何出错退回 A |
| **A** 确定性（无大模型） | `sha256:8f4f8be048a10b3b279e7e3425157c89160bd7b5ad7cddcd6adfb7ea0a8ceef5` | `submission/submission.a.json`（`models: []`） | 预测与现版**逐位相同**；改的是推理理由（按题目点名的驱动因素逐条找原文前提）、证据段落质量 |

CI：`cand/t4-a` run 37586931980、`cand/t4-b` run 37586934864，全绿（11/11 在镜像里合格、异常用例 17/17、
House 假服务器全模式通过、House 开着再跑一遍 17/17、镜像内 House 联调 4/4）。

### 本地分数（近似真实结果 + 猜的 naive，只看相对）

| | 8 个可打分 unit 平均 | 榜面换算 | 虚假 claim |
|---|---|---|---|
| 现版（bf2a1647…，已上榜 0.5394） | 0.682 | 0.596 | 0 |
| A | 0.682（预测完全相同） | 0.596 | 0 |
| B | **本地测不了**（House 只在平台上能连）；假服务器只验证"管道正确、出错能回退" | — | 0（假服务器各种恶意回复下都是 0） |

所以 A 在 Dev 榜上应当≈0.539（推理理由 Dev 不评分），**A 的价值在决赛后的推理分**（final = −0.27 + 1.27×analysis + 0.25×reasoning）。
真正可能拉开差距的是 B，只能靠上传看。

### B 是怎么用 House 的（为什么不会出虚假引用）

1. 代码先用 BM25 从**截止日前、可引用**的文档里挑段落，编号 P1、P2…（短文档整篇读，长申报文件按题目驱动因素+通用"业绩/指引"词检索）。
2. 每次请求最多 6 个实体、≤48k 字符；给模型看：题目、实体表、我们的确定性基线（点/区间/标签/方法）、编号段落。
   要求只回 JSON：点预测、90% 区间、标签、置信度、证据段落编号、一两句理由。
3. 合并规则（全在代码里卡死）：无信号的行向模型值移动 0.6×(0.5+0.5×置信度)，有信号的行 0.35×…；
   模型值超出基线 8 个半宽就丢弃；区间半宽和模型的混合但不低于基线的 40%，且一定包住基线点和模型点；
   标签只能是题目词表里的，置信度 ≥0.5（无信号）/≥0.75（有信号）才换；概率型题标签和概率强制一致。
4. 引用：模型**只能报段落编号**，代码把编号映射回 (doc_id, 起止) 再从原文切片——claim 永远是原文逐字，且属于该实体或共享文档。
5. 推理理由：再发 1 次请求让模型写 3 条（前提=它选的段落编号→原文逐字；机制/含义=模型文字，过滤成 ASCII、
   去网址、去禁用词、提到截止日之后年月的整条丢掉、限长），末尾由代码写上实际提交的数值。
6. 时间/次数：并行 3 路、守护线程；单次 200 秒超时，House 阶段从启动起 330 秒内一定结束（另有 420 秒硬闹钟）；
   每 unit 最多 9 次请求（上限 25），仅 429 时重试一次；关闭 thinking、temperature 0、固定 seed、max_tokens 3500；
   代理用平台注入的 http(s)_proxy（假服务器测过带账号密码的代理）。

### 风险（老板需要知道）

- **Dev 分可能虚高**：Dev 练习题都在模型训练截止前（卡片原话"memorization-exposed"），模型可能"记得"结果；
  决赛题是训练截止之后的，记忆帮不上忙。提示词明确要求"不使用截止日之后的知识"，但无法保证。
  所以 B 在 Dev 上涨分 ≠ 决赛一定涨；合并权重故意偏保守（最多 0.6）。
- 组委会在 T4 #21/#22 里说 House 回答 12–52 秒（中位 36 秒）；我们单次超时 200 秒，够用。
- B 用 House 必须在描述文件里披露模型行——`submission.b.json` 已填好。决赛若选 B，`submission.final.json` 也要换 digest + 加这行再封签。

### 怎么打包、建议上传顺序

```
.venv\Scripts\python pack_all.py t4-b t4-a      # 生成 ../agenthon-submissions/t4-dev-b.zip、t4-dev-a.zip（Team Key 隐藏输入）
```

1. 先传 **t4-dev-b.zip**：看 House 在平台上是否真的被调用、分数是否高于 0.5394。
2. B 若 ≥0.54：A 可以不传（Dev 上 A 预期≈0.539，只是推理理由不同，Dev 不评推理）；
   B 若低于 0.5394 或失败：再传 **t4-dev-a.zip** 确认 A 没回退，决赛用 A。
3. 决赛描述文件 `submission.final.json` 目前仍指向旧镜像 bf2a1647…，等 Dev 结果出来后再定换成 A 还是 B。

## 它在做什么（白话）

每道题给一张小表（1–12 行，比如几家银行、几个国债期限），要我们给每行一个预测值或标签、一个 90% 区间，
外加"证据"引用。我们的程序（`agent/`）：

1. 先把截止日之后的文档全部扔掉，再读剩下的（防"未来文件"陷阱）。
2. 从文档里找能用的数字：最近一季 EPS 同比、修订历史表、历史序列表格、"持续经营存在重大疑虑"之类的措辞。
3. 用这些数字做保守的预测；找不到就把上期值原样带过去。
4. 每条证据都是**原文逐字截取**，并且只从标给这家实体的文档里取 —— 这样官方的虚假引用检查必过。
5. 写最多 3 条推理理由（给决赛后的 LLM 评审看，加分项）。
6. 任何一步出错都会退回一份"最小合法答案"，永远写出文件、永远 exit 0。

## CI（镜像在平台同等限制下实跑）

GitHub Actions `t4-image`（最终版：main d630d5f 手动触发，run 37533228853；首版 run 37531031242 已被取代）全绿：linux/amd64 构建 + 构建时自测，
11 个公开 unit 在镜像里跑（只读根目录、uid 65534、无网络、pids 256、nofile 1024），官方 schema / 引用规则 /
推理理由检查 / 打分器全过，结果与本机逐项一致；异常用例 17/17、House 假服务器 4/4。最终镜像（GHCR，需改为公开），已填入并重新封印进 `submission/submission.json` 与 `submission.final.json`：

`ghcr.io/dapeipeipeipei/jinpei-t4@sha256:bf2a164756244a4ede12e5ea92bf164b60f47c2ba664f9cd955c117f8c68e01c`

## 本地成绩

真实结果是**凭记忆写的近似值**、naive 规则是**猜的**（官方都不公开），所以只看"我们 vs 基线"的相对差距。

| unit | 我们 | 官方基线 | 说明 |
|---|---|---|---|
| EXAMPLE（苹果 EPS beat/inline/miss） | 0.82 | 0.70 | 都判 inline，我们区间更紧 |
| credit-event（8 家信用事件） | 0.88 | 0.31 | 7/8 对（YELL 漏判）；基线 3 条虚假引用 |
| eps-growth（8 家银行 EPS 增速） | 0.64 | 0.38 | MAE 9.2 vs 16.0 |
| eps-yoy（6 家 EPS 升降） | 0.84 | 0.32 | 5/6 对（AMGN 错） |
| postearn（财报次日反应） | 0.75 | 0.39 | 2/3 对 |
| cpi-comp（CPI 分项） | 0.53 | 0.56 | 唯一略输：那个月各分项都回到 0 附近，"预测 0"碰巧更准 |
| fomc 2022 / 2024（国债收益率变化） | 0.50 / 0.50 | 0.50 / 0.50 | 无信息时零变化就是最优；实际涨了 60–110bp，谁也猜不到 |
| auction / cot / macrorev | 合格 | 合格 | 我不确定真实结果，未打分 |
| **8 个可打分 unit 平均** | **0.68**（榜面约 0.60） | **0.46**（榜面约 0.31） | 榜面 = −0.27 + 1.27 × 分 |

另外：我们 202 条 claim **0 条虚假**；基线 78 条里 17 条虚假。异常输入 9 个用例全过
（未来文件陷阱、spans 格式、坏文件、无 manifest、无语料目录、陌生家族、陌生标签、单行排序、8MB 大文件）。
House 模型层用本地假服务器测了 4 种情况（正常 / 胡言乱语 / 401 / 连不上），都不影响答卷合法性。
同一输入换不同 `QFBENCH_SEED` 输出完全一致。

## 文件在哪

| 路径 | 是什么 |
|---|---|
| `PLAN.md` | 一页可行性结论（评分规则、做法、预期、风险） |
| `agent/` | 提交的程序，纯标准库 |
| `bin/analyze` | 镜像里的命令入口 |
| `Dockerfile` | linux/amd64，非 root，带 `qfbench2.interface_version="2.0"`；两阶段：工具箱只装在自测阶段，运行镜像只有标准库 |
| `submission/ARTIFACT_PROVENANCE.md` | 规则要求的来源记录（不进 zip） |
| `harness/run_local.py` | 跑全部公开 unit + 官方检查 + 官方打分器（`--docker-image` 可在镜像里跑） |
| `harness/robustness.py` / `mock_house.py` | 异常输入 / House 层测试 |
| `harness/approx_truth.py` | 近似真实结果和猜的 naive 规则（**仅本地诊断**，程序不读） |
| `agent/retrieve.py` | BM25 段落检索 + 从题目里解析"驱动因素"（A、B 共用） |
| `agent/house.py` | House 大模型层（B 镜像开启，A 镜像关闭） |
| `harness/house_in_image.py` | 用假 House 服务器跑**构建好的镜像**（CI 里跑） |
| `submission/submission.a.json` / `submission.b.json` | 两个候选的 Dev 描述文件（已封签、已用工具箱校验） |
| `submission/submission.json` | 描述文件草稿（已用工具箱校验并封签；镜像地址、team_id 是占位） |
| `../.github/workflows/t4-image.yml` | CI：构建、在镜像里跑全部 unit、推 GHCR |

## 怎么复现

```bash
cd t4-work
PYTHONUTF8=1 ../.venv/Scripts/python harness/run_local.py      # 约 30 秒，最后有汇总表和 GATE
PYTHONUTF8=1 ../.venv/Scripts/python harness/robustness.py
PYTHONUTF8=1 ../.venv/Scripts/python harness/mock_house.py
```

（本机已装：`pip install -e track4-analysis-public --no-deps`、`transformers sentencepiece protobuf` 和两个
judge 的分词器文件（几 MB，没下 3.5GB 权重）。Windows 上官方打分器要靠 `harness/winshim.py` 补 `O_DIRECTORY`，
并把 git 自动转换的 CRLF 还原，否则 manifest 校验不过。）

## 2026-10-06 对抗审查（按 scorer 5.2.2 源码逐条核对后修的）

1. **只引用有扁平 `text` 的文档**：打分器 `_span_text` 只读 `document["text"]`，`spans[]` 格式的文档
   在打分器眼里是空串，引用它的 claim 会被判越界（虚假）。现在这类文档只读不引。
2. **manifest 里 `role` 不是 `corpus` 的文件不引用**：打分器不认识它，引用会被判"未解析"→ 整个 unit 判死。
3. **目标类型以 card.toml 为准**（打分器就是这么取的），不再在答案里写 `target_type`（可选字段，写错直接判死）；
   分类题无论有没有标签表，每行都一定有 label。
4. 引用文本过滤：非 NFC 文本、"内容为空"（按打分器 5.2.2 的词表，先抹掉实体名）、canary/GUID 一律不引。
5. 时间：600 秒包含拉镜像，硬闹钟 520→420 秒、软预算 420→300 秒；写文件前先关闹钟；超长表只取最近 240 行；
   检索按文档缓存（120 行 × 12 个大共享文档 3.4 秒）。运行镜像去掉 numpy/scipy（只在自测阶段装），拉取更快。
6. 推理评分的 3000 字节"逐实体答案"上限：超了就自动压缩小数位（误差 ≤ 区间宽 0.5%）。
7. 输出改成 ASCII 转义 JSON（语料里有孤立代理字符也不会写坏文件）。
8. 推理理由的 premise 优先选可读的文字段落、三条理由不重复同一段，引文不再从半个单词开始。
9. 补了规则要求的 `submission/ARTIFACT_PROVENANCE.md`（不进 zip）。

异常用例从 9 个增加到 17 个（spans 文档、role、无标签表无 card、两万行表、canary、35 行答案字节、
大语料宽名单、种子/哈希种子不变性），全过；11 个公开 unit 仍全部合格、0 条虚假，本地分数不变。
（这些修改已包含在 bf2a1647… 及之后的镜像中。）

## 接下来要做的（按重要性，2026-10-07 更新）

1. ~~镜像公开~~、~~填 digest~~、~~首次 Dev 上传~~：已完成（0.5394）。
2. 上传 `t4-dev-b.zip`（House 候选）看平台真实效果；按上面的顺序决定是否再传 A。
3. 根据 Dev 结果把 `submission.final.json` 换成 A 或 B 的 digest（B 还要加 House 行）并重新封签。
4. 10-13 前组委会会公布决赛的单 unit 时限 / House 次数上限（T4 #25）；公布后核对 330 秒 / 9 次的设置是否仍在限内。
