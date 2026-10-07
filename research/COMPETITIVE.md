# 竞争情报 — T2 预测 / T4 可解释性（2026-10-07）

只用公开来源：三个上游仓库的全部 issue（含已关闭）及评论、上游 README/docs、agenthon.net 公开榜、
CodaBench 公开 API、GitHub/网页搜索。没有拉取或逆向任何其他队伍的镜像或代码。

证据等级：**A** = 主办方原话（附链接）；**B** = 由公开数字/规则强推断；**C** = 推测。

---

## 0. 一页结论

1. **两个 Dev 榜的头部都不能当 Final 的预期。**
   - T2：主办方自己写明练习卡"大部分能从自身读出来"（103 张里 75 张的答案原样出现在兄弟卡的面板里），
     并说 Dev 榜"是练习，不是排名"（A）。Final 是"更晚的窗口，练习数据一条都碰不到"（A）。
   - T4：主办方裁定可以用练习题的事后结果调常数，但这样得到的 Dev 分"是样本内的"（A）；Final 的
     题型家族"大多没有公开对应"（A）。榜首 0.988 相当于 analysis ≈ 0.99，10 道不同题型几乎全对，
     不知道答案很难做到（B）。
2. **Final 的分数构成和 Dev 不同。** T4 Final 多两项 Dev 没有的东西：NLI 矛盾检查（只在 Final 跑），
   以及 submitted_reasons 推理加分，每道被评推理的题最多 +0.25（A）。T2 Final 的规则和 Dev 新规则一样，
   但前几名会被人工审 rationale，并用新种子复跑（A）。
3. **T2 最大的未决变量：Final 的 F4/F2 窗口是事前选的还是事后选的**（[T2#26](https://github.com/Agenthon-2026/track2-forecasting-public/issues/26) 第 2 问，
   主办方说"在和赛道负责人确认"）。我们 v4 把 F4 放宽到 2.0，赌的是封存的 F4 卡真有冲击。如果窗口是事前选的，
   这个赌注会亏（平静世界压力测试里 F4 +0.42）。**Final 上传前必须看这个回答**，建议准备两个 F4 档位。
4. **同分时先上传的排前面**（A）。Final 开放（10-13 12:00 UTC）后，确定版本就尽早传。
5. **我们的仓库 `dapeipeipeipei/agenthon-2026` 是 PUBLIC**，网页搜索 "Agenthon 2026 forecasting github" 第一条就是它。
   引擎和全部调参记录对所有对手可见。要不要在 10-25 前转私有由老板定（主办方要的
   ARTIFACT_PROVENANCE 只需"随源码保存、可供核验"，没要求公开）。

---

## 1. 主办方裁定/澄清（按主题）

### 1.1 House 模型

| 事项 | 内容 | 来源 |
|---|---|---|
| 模型 | Nemotron 3 Super 120B-A12B FP8，快照 `rl-030326-fp8`，**训练截止日未公布**；默认开 thinking，可用 `chat_template_kwargs.enable_thinking=false` 关 | [T2#14](https://github.com/Agenthon-2026/track2-forecasting-public/issues/14)、[T4#3](https://github.com/Agenthon-2026/track4-analysis-public/issues/3) |
| 调用 | `POST $MODEL_ENDPOINT/v1/chat/completions`，`Bearer $MODEL_TOKEN`；缺 `/v1` 或缺 bearer 会被拒，不计次数 | [Hub#6](https://github.com/Agenthon-2026/Agenthon2026-public/issues/6) |
| 配额 | 每 unit 最多 25 次被接纳的请求，每次输出最多 4,000 token；**没有 token 总预算**（1M/100k 已撤销），**没有每分钟限速**；接纳即扣，接纳后上游失败不退，重试算新请求 | [T2#2](https://github.com/Agenthon-2026/track2-forecasting-public/issues/2)、[T2#17](https://github.com/Agenthon-2026/track2-forecasting-public/issues/17)、[Hub#2](https://github.com/Agenthon-2026/Agenthon2026-public/issues/2) |
| 上下文 | prompt 加输出上限超出上下文窗口的请求在接纳前被拒，不扣次数；服务端上下文窗口**未公布**（262,144 是 tokenizer 元数据，不是服务窗口） | [Hub#2](https://github.com/Agenthon-2026/Agenthon2026-public/issues/2)、[T4#25](https://github.com/Agenthon-2026/track4-analysis-public/issues/25) |
| 推理控制 | 代理原样转发 `low_effort`、`reasoning_budget`，但主办方没测过效果 | [Hub#8](https://github.com/Agenthon-2026/Agenthon2026-public/issues/8) |
| 响应时延 | 实测 House 回答 18–52 秒（中位 36 秒）；有队客户端 40 秒超时，15/20 个请求白扣 | [T4#21](https://github.com/Agenthon-2026/track4-analysis-public/issues/21)、[T4#22](https://github.com/Agenthon-2026/track4-analysis-public/issues/22) |
| 故障 | 09-27 09:20–12:05 UTC 过载（超时/502），受影响的上传照常计次；09-23 00:23 重启导致的 Failed 不计次 | [T2#19](https://github.com/Agenthon-2026/track2-forecasting-public/issues/19)、[Hub#2](https://github.com/Agenthon-2026/Agenthon2026-public/issues/2) |
| BYO | 09-18 起**彻底取消**：不能带 LoRA、权重或第二个 LLM，`category` 只能是 `api` | [Hub#7](https://github.com/Agenthon-2026/Agenthon2026-public/issues/7)、[T4#8](https://github.com/Agenthon-2026/track4-analysis-public/issues/8) |
| T4 嵌入模型 | 只允许 starter pack 推荐的 **NeMo Retriever 嵌入模型**（构建时打进镜像）；BGE-M3、reranker 等一律不行；BM25 可以 | [T4#20](https://github.com/Agenthon-2026/track4-analysis-public/issues/20) |
| Final 资源 | Dev 的设置"不代表 Final"；Final 的单 unit 时限、阶段时钟、请求和输出上限会在 **10-13 前**按赛道公布；Final 题目数不公布 | [T4#25](https://github.com/Agenthon-2026/track4-analysis-public/issues/25)、[T2#17](https://github.com/Agenthon-2026/track2-forecasting-public/issues/17) |

### 1.2 T2：Final 的 /input 里有什么、哪些字段能用

| 事项 | 裁定 | 来源 |
|---|---|---|
| 文件 | Final unit 和 Dev 一样：`card.toml`、`forecast_spec.json`、`forecast_card.md`、`manifest.json`、`panels/`、`text/`（含 `corpus_index.json`）。**不要把练习卡当完整 schema**，只依赖参考 CLI 读的键：`[task].id`、`[targets]` 的 `asset_ids/horizons/target_type/target_frequency`、`[scoring.params].n_draws_min`；其余键都按可选处理 | [T2#20](https://github.com/Agenthon-2026/track2-forecasting-public/issues/20) |
| `[metadata].category`（如 `T2-F4`） | **可以用**，包括 F4 加宽，也可以写进 rationale；按卡声明的 horizons 调整也可以 | 同上 |
| `[metadata].difficulty`、`design_note` | **不能用**，已从卡片格式中删除，Final 卡上没有 | 同上 + [T2#4 10-06 公告](https://github.com/Agenthon-2026/track2-forecasting-public/issues/4) |
| `forecast_card.md` 的 "Text corpus role" 段里写的方向/尾部 | **不能用**，Final 卡不会有这类措辞，练习卡上也已删掉 | [T2#20](https://github.com/Agenthon-2026/track2-forecasting-public/issues/20) |
| 在 agent 里重建 M0 | **Dev 和 Final 都可以**：从该 unit 自己的面板重建，用来比较我们的离散度和相关结构 | [T2#26](https://github.com/Agenthon-2026/track2-forecasting-public/issues/26) |
| Final 的 F2/F4 窗口怎么选的 | **未答复**（"在和赛道负责人确认"） | [T2#26](https://github.com/Agenthon-2026/track2-forecasting-public/issues/26) |
| 按家族/卡形聚合 Dev 分来决定组件开关 | 可以，不算刷榜；但不能把任何 unit 的答案带进镜像 | [T2#24](https://github.com/Agenthon-2026/track2-forecasting-public/issues/24) |
| 用练习数据校准常数 | 可以（预先登记规则、记入 provenance、数据在截止日前可得） | 同上 |
| 把自己的预测（水平、分位数）写进 House prompt | 可以 | 同上 |
| 跨期/跨资产重排 draw、单 unit 内拟合依赖模型 | 可以，没有额外的一致性约束 | [T2#25](https://github.com/Agenthon-2026/track2-forecasting-public/issues/25) |
| 尾部项 | 是 pinball（`tail_metric` 默认值，没有卡覆盖），Final 一样 | 同上 |
| 分项诊断 | Dev 页面只给每卡 composite，不给 marginal/joint/tail 分项 | 同上 |
| 带标签的校准数据集 | 主办方不提供也不推荐 | [T2#3](https://github.com/Agenthon-2026/track2-forecasting-public/issues/3)、[T2#25](https://github.com/Agenthon-2026/track2-forecasting-public/issues/25) |

### 1.3 T2 计分（10-06 20:11 UTC 起，Final 同规则）

- 每个分项除以 **M0 对这张卡自己预期的误差**（闭式，见 `docs/M0-BASELINE.md` §5），不再除以 M0 的实际误差。
  1.0 = 误差等于 M0 的自我预期。**上限和失败值都是 8.0**，失败卡留在平均里（A，[T2#4](https://github.com/Agenthon-2026/track2-forecasting-public/issues/4)）。
- 旧的 Dev 上传都按新规则从**已有输出**重算，没有重跑（A）。所以 10-06 之前跑出的榜单行，用的还是**旧语料**，
  当时卡上还有 difficulty 和带方向的 "Text corpus role" 措辞。
- 榜面 = −(平均分)，越高越好。M0 参考行 −2.6412（未排名），说明练习卡的实际波动远大于 M0 用 300 行历史预期的波动（B）。
- 20 张练习卡改了 id：新 id 直接点出事件和日期（如 `t2-F4-mkt-debt-ceiling-2011`）。

### 1.4 T4 计分

| 事项 | 裁定 | 来源 |
|---|---|---|
| 版本 | 5.2.2 是 **Final 前最后一次计分规则变更**，之后只修主办方自己的故障 | [T4#2](https://github.com/Agenthon-2026/track4-analysis-public/issues/2)、[T4#19](https://github.com/Agenthon-2026/track4-analysis-public/issues/19) |
| 榜面 | −0.27 + 1.27 × analysis；−1e9 = 没有一个 unit 被计分 | [T4#19](https://github.com/Agenthon-2026/track4-analysis-public/issues/19) |
| **只在 Final 生效** | ① NLI 矛盾检查（Dev 上 claim 检查不跑 NLI judge）；② `submitted_reasons` 推理评分，被评推理的题每题最多 +0.25，Final 结束后才评，**任何 CodaBench 榜上都看不到**；③ 长段落取最匹配部分给 judge；④ 超限的 reason 跳过，不再整题推理记 0 | [T4#2](https://github.com/Agenthon-2026/track4-analysis-public/issues/2)、[T4#19](https://github.com/Agenthon-2026/track4-analysis-public/issues/19) |
| claim 规则 | 每个 claim 只引一个文档的一段；引用超过 8,000 字符记为虚假；只有功能词/填充词的 claim 记为虚假；网址里的数字不算数字；claim 里出现的数字必须在所引段落里（自己提交的数值原样写出可豁免） | [T4#2](https://github.com/Agenthon-2026/track4-analysis-public/issues/2) |
| 区间 | 区间分不能超过点预测挣到的分（5.2.1）；没有宽度/锐度惩罚项（裁定，不再审议） | [T4#2](https://github.com/Agenthon-2026/track4-analysis-public/issues/2)、[T4#1](https://github.com/Agenthon-2026/track4-analysis-public/issues/1) |
| 分类题的区间 | prompt 要数值的，按 prompt 的单位对那个数值打分；不要数值的没有区间分，但 schema 仍要求必须给区间 | [T4#25](https://github.com/Agenthon-2026/track4-analysis-public/issues/25) |
| naive 规则怎么构造、恰好等于参考值算哪类 | **未答复** | [T4#25](https://github.com/Agenthon-2026/track4-analysis-public/issues/25) |
| 字段优先级 | 数值含义（单位、期限、排序方向）以 `task.json` 的 prompt、`target.name`、`label_assertions`、实体表为准；拒答拿该题最差分，最好按 prompt 作答 | [T4#23](https://github.com/Agenthon-2026/track4-analysis-public/issues/23) |
| 隐藏集范围 | "情况 2"：**题型家族更多**，大多数没有公开对应，三种目标类型都有；"不要按公开题的形状调参" | [T4#17](https://github.com/Agenthon-2026/track4-analysis-public/issues/17) |
| 用练习题事后结果 | 调常数（a）、做设计选择（b）**都可以**，对**所有** Final 题都成立；条件是用首次公布的值、在 ARTIFACT_PROVENANCE 里记来源和取数日期、写明练习题是样本内例外；练习题本身的 Dev 分属于样本内，不处罚 | [T4#24](https://github.com/Agenthon-2026/track4-analysis-public/issues/24) |
| 预训练截止日 | 只有 House 底座（和 NeMo Retriever 嵌入）的通用预训练豁免截止日规则；参与者自己的选择/校准不豁免 | [T4#3](https://github.com/Agenthon-2026/track4-analysis-public/issues/3)、[T4#20](https://github.com/Agenthon-2026/track4-analysis-public/issues/20) |
| 镜像 | 必须有 `LABEL qfbench2.interface_version="2.0"`，否则跑完 10 个 unit 才 Failed；macOS 的 xattr 会让镜像层解包失败；6 GB 的镜像预拉取超时，拉取时间算进每个 unit 的时限 | [T4#20](https://github.com/Agenthon-2026/track4-analysis-public/issues/20)、[T4#16](https://github.com/Agenthon-2026/track4-analysis-public/issues/16) |

### 1.5 通用：日程、计次、同分、核验

- Dev 最后一次运行必须在 **10-12 20:00 UTC 前开始**；10-13 08:00–12:00 UTC 维护；**Final + Verification 10-13 12:00 UTC → 10-25 23:59 AoE**；
  每队每赛道**只交一份** Final（A，[T2#18](https://github.com/Agenthon-2026/track2-forecasting-public/issues/18)、[Hub#2](https://github.com/Agenthon-2026/Agenthon2026-public/issues/2)）。
- Dev：T2/T4 每天 5 次、总共 20 次；Failed 不计次，held/cancelled 计次（A）。
- **同分：先上传的 Final 排前**（A，[T2#4](https://github.com/Agenthon-2026/track2-forecasting-public/issues/4)、[T4#2](https://github.com/Agenthon-2026/track4-analysis-public/issues/2)）。
- 核验：主办方用**新种子、重抽样**复跑 Final 榜前列，查复现和披露，不用另交（A，T2 README）。
  `models[]` 和 ARTIFACT_PROVENANCE 会被核对。
- Dev 和 Final "目前是同一个计分镜像、不同队列"，但不保证字节完全一致（A，[Hub#1](https://github.com/Agenthon-2026/Agenthon2026-public/issues/1)）。

### 1.6 防泄漏 / 背答案的机制（T2）

- **跨 unit 查答案**被禁止，但**没有任何 gate 能检测**，主办方明说"靠自觉，你的对手读的是同一段话"（A，T2 README "No cross-unit lookup"）。
- 2026-08-28 实测：**103 张练习卡里有 75 张，答案全部原样出现在兄弟卡的面板里**；Dev 榜"是练习，不是排名"（A）。
- 封存集：README 的 Firewall 写评估窗口是 **H2 2025 – Q2 2026**，同时又写"截至 9 月 3 日只有一小部分封存 unit 有结果，其余要将来才揭晓"，
  而且"在 Final 打包前重跑"重合检查，当前 0 条重合（A）。这两句话互相矛盾：如果窗口真是 H2 2025–Q2 2026，到 9 月早该全部揭晓。
  所以至少有一部分 Final 目标在 2026-09 之后，是**真正的前瞻预测**（B）。
- **"闭卷/回忆"检查**：公开计分器**不做**闭卷回忆对比（`docs/NVIDIA-STACK.md`："a score alone does not establish whether the agent used text or recalled an outcome"）（A）。
  替代手段是 `forecast_rationale.md`（必交、不计分），用来**筛查榜单前列**。`docs/RATIONALE-REVIEW.md` 列了 6 个信号：
  验证门槛恰好等于答案、为凑总数增删项、关键统计量在去重叠后反转、选择性引用语料、方向错误一律偏向答案、
  中心值落在面板自身历史的远尾。被标记只会触发人工复核，不会直接改分（A）。
- `SOLVER-PLAYBOOK.md` 提到 `g4_pit_concentration`（"太紧的靶心会被 g4 判不合格"），但 README 说 gate 只有 g0–g3，
  公开代码里也找不到 g4。只能当作潜在的 Final 筛查，**不要交出异常窄的分布**（A，文档自相矛盾）。

---

## 2. 公开榜单快照（agenthon.net，2026-10-07 抓取）

### T2（榜面 = −平均分；M0 = −2.6412；共 101 队）

| 名次 | 队 | 榜面 | 相对 M0 |
|---|---|---|---|
| 1 | Yan Su | −1.340 | 0.51 |
| 2 | Ywin | −1.548 | 0.59 |
| 3 | Made in Heaven | −1.635 | 0.62 |
| 4 | Samoyed | −1.779 | 0.67 |
| 5 | F1_1 | −1.837 | 0.70 |
| 8 | CMCCGDYDDICT | −1.876 | 0.71 |
| 10 | Apex | −2.000 | 0.76 |
| 25 | LastDigitsOfPi | −2.234 | 0.85 |
| — | **M0 参考** | −2.641 | 1.00 |

我们 v4 新规则下的本地估计：val65 1.773 / M0 2.317 = 0.765，折算榜面 **约 −2.0 到 −2.15，Dev 第 10–17 名**（`t2-work/V4_NOTES.md`）。
抓取时 "Jin & Pei" 在 T2、T4 两个榜上都显示 not scored（可能是榜单刷新滞后，也可能是这一轮还没有计分上传）。

### T4（榜面 = −0.27 + 1.27 × analysis；共 83 队）

| 名次 | 队 | 榜面 | 反推 analysis |
|---|---|---|---|
| 1 | CMCCGDYDDICT | 0.988 | 0.991 |
| 2 | Lucky | 0.930 | 0.945 |
| 3 | Nietzsche | 0.877 | 0.903 |
| 4 | GANisok | 0.772 | 0.820 |
| 5 | Autonomous Alpha | 0.592 | 0.679 |
| 6 | Warkop PuteraPrakoso | 0.567 | 0.659 |
| 7 | abc123 | 0.533 | 0.632 |

我们第一次上传 0.5394（`t4-work/STATUS.md`，analysis ≈ 0.637），本地估计 0.596。

### 头部队伍的公开足迹

- GitHub 仓库搜索（`agenthon` 约 100 个结果）和代码搜索、网页搜索，都**找不到** Yan Su、Ywin、Made in Heaven、Samoyed、
  CMCCGDYDDICT、Lucky、Nietzsche、GANisok 的任何代码、文章或帖子。
  有公开仓库的都是中游或下游队：Cornfield Chase（Yif-design）、hnhparitosh、OffTheTape、Money Miner、iMak AI Lab、Optivex、
  chilli（youxuanxue 的 T4 fork）、Felix772 等。
- **CodaBench 不公开别队的 submission.json。** 匿名访问 `api/phases/{29643,29649}/get_leaderboard/` 和 `api/submissions/?phase=…`，
  返回的都是空列表（主办方把成绩同步到 agenthon.net，CodaBench 榜单本身是空的）。所以 hub README 说的"上榜后 zip 可下载"
  目前并不成立，拿不到别队镜像的 digest，也看不到 `models[]` 声明（A，实测）。
  竞赛 id：T2 17766（phase 29643 Dev / 29644 Final），T4 17768（29649 / 29650）。
- 跨赛道画像（B/C）：**CMCCGDYDDICT** 四个赛道都在前列（T1 23、T2 8、T3 12、T4 1），像是一支资源充足的综合队，名字疑似中国移动广东 DICT 团队（C）。
  **Nietzsche** T4 第 3，但 T1 48、T2 65、T3 32，单科突出的形状和"T4 样本内调参"一致（C）。
  **Yan Su** T2 第 1，但 T4 第 53、T1 第 7（C）。

---

## 3. 头部队伍大概率在做什么（及证据等级）

### T2：−1.34 到 −1.64 的来源

诚实方法很难在**真实冲击卡**上把 M0 的误差砍掉一半。下面几条渠道都存在，而且都**带不进 Final**：

1. **大模型回忆历史事件（B）。** 练习卡全是有名的历史片段（2011 债务上限、2013 taper、2020、2022 加息……），
   Nemotron 认识它们；卡 id、标题、description 和语料都点名了事件。用 House 读卡、问"之后发生了什么"，能稳定猜对方向。
   10-06 改名后 id 更直白。计分器不做闭卷对比（A），所以 Dev 上看不出来。
   Final：如果窗口在模型训练截止之后（前瞻部分肯定如此），回忆为零；如果落在 H2 2025 并且模型见过，也会被 rationale 筛查盯上（A）。
2. **10-06 之前的方向性提示（B）。** 旧练习卡的 "Text corpus role" 写着"needs a left tail"之类，还有 difficulty。
   重算只换了除数，不重跑输出，所以**旧输出里吃到的提示仍然算在现在的榜上**。Final 卡上没有这些（A）。
3. **跨 unit 查答案（C）。** 75/103 张卡能直接查到答案（A），没有检测（A）。不过完全查表的话分数会远低于 1.34
   （大约 ¾ 的卡接近 0），所以头部最多是部分用到、或者间接用到（比如用兄弟面板"校准"），不能下结论。
4. **合法但对练习集过拟合的加宽（B）。** M0 自己在练习集上是 2.64，说明练习卡被**刻意选成大波动**。
   按家族把 F4/F2 加宽很多，能在 Dev 上大幅占优（我们 F4 2.0 也是这个逻辑）；Final 如果平静，就会倒贴。

结论：**Dev 名次和 Final 名次的相关性大概率很弱**。我们的诚实 v4 在 Dev 上落后头部 0.6，不代表 Final 落后。

### T4：0.77 到 0.99 的来源

1. **用练习题事后结果做样本内调参，甚至逐题对答案（B）。** analysis 0.99 意味着 10 道题（EPS、拍卖、CPI 分项、COT 持仓、信用事件、FOMC 曲线……）
   预测和区间几乎完美，而且 0 条虚假 claim。主办方明确允许用这些结果，但称之为"样本内"（A，[T4#24](https://github.com/Agenthon-2026/track4-analysis-public/issues/24)）。
   提问的 bram-wq（Team 229）自己承认这样做过，可见是常见做法（A）。
2. **Dev 不跑 NLI 矛盾检查、不评推理（A）。** 所以 Dev 分偏高，也测不出 Final 里会拉开差距的两项。
3. 第 4 名（0.77）和第 5 名（0.59）之间有断崖，第 5 名以下都挤在 0.45–0.59（B）。这和"头 4 名用了结果信息、其余是诚实系统"的分层一致。
   我们 0.539 在诚实那一层的前列。

---

## 4. 对我们 Final 的具体建议

### T2

1. **盯 [T2#26](https://github.com/Agenthon-2026/track2-forecasting-public/issues/26) 第 2 问，准备两个 Final 档位。** 如果主办方说 F2/F4 窗口是**事后**按事件选的
   （和练习卡一样），用现在的 v4（F2 1.25 / F4 2.0）；如果说是**事前**选的或是前瞻窗口，换保守档（F4 ≈ 1.1–1.25，F2 ≈ 1.0）。
   依据：`V4_NOTES.md` 的平静世界测试里 F4 2.0 多亏 0.42；k = 0.5 时 F4 才打平。两个描述文件和镜像在 10-13 前都备好、封好签。
   10-12 前还没有答复的话，倾向折中（F4 ≈ 1.5），在 rationale 里写清理由。
2. **合规自查已过（本次核对）。** 引擎只从 `[metadata].category` → `forecast_spec.card_family` → `task.id` 判断家族（`engine/cardinfo.py:_family`），
   **不读** difficulty、design_note、"Text corpus role"、标题和 description；语料只读 `corpus_index.json` 和正文。保持这样。
   注意：Final 卡的 `task.id` 不一定带 F 编码，但 `category` 每张卡都有（A），所以主路径没问题。
3. **可以合法利用 M0**（[T2#26](https://github.com/Agenthon-2026/track2-forecasting-public/issues/26)）：没有信号的卡收缩到 M0 的形状和相关结构，等于在平静世界里锁定 ≈1.0。
   在 rationale 里写明"以 M0 为锚"。
4. **rationale 要经得起人工复核。** 前列一定会被审：写正向推导，不要用门槛等于结论的"验证"，引用语料时不要只挑支持自己的句子。
   我们 rationale 由真实推导生成（STATUS 记录），保持。不要交出异常窄的分布（g4 的说法）。
5. **种子稳健性。** 复跑用新种子（A），我们 5 个种子的分差很小（0.934–0.944，旧规则），没问题；确认 n_draws 足够（≥ 2,000）。
6. **Dev 名次不要追。** 剩下的 Dev 次数只用于确认管道（新语料、新 id 下 104/104 通过、看每卡分有没有失败的 8.0）。
   不要为了缩小和 −1.34 的差距去用回忆或方向提示。
7. **House 用法（如果 v5 用到）：** 25 次、没有限速、单次最多 4,000 token；可以把自己的分位数放进 prompt（[T2#24](https://github.com/Agenthon-2026/track2-forecasting-public/issues/24)）；
   绝不能让模型"回忆结果"。客户端超时要 ≥ 120 秒（T4#21 的教训）。
8. **Final 资源** 10-13 前公布（A）。看到后核对 1,800 秒时限和镜像拉取时间。

### T4

1. **Final 得分重心在 Dev 看不到的两项：推理加分（每道被评题最多 +0.25）和 NLI 矛盾检查。** 候选 A/B 都已经写 3 条 reasons。
   上传前用 `faithfulness/judge.py`（Final 用的同一对 DeBERTa）在 11 个 unit 上跑一遍**带 NLI 的**检查，确认 0 条矛盾；
   用 `check_submitted_reasons` 做 schema 检查（reasons 不合 schema 会让**整题答案无效**）。
   premise 要从一个语料文档逐字引用、至少 3 个词，才能豁免禁用词表。
2. **泛化优先于 Dev 分。** 隐藏集题型更多、大多没有公开对应（A）：确保没见过的 target/prompt 也能出合法答案
   （总给区间、按 prompt 单位、排序方向从 prompt 读、不拒答）。用"改写练习题"的方式做健壮性测试（换实体、换单位、换方向措辞）。
3. **不必追头 4 名的 Dev 分。** 他们的 0.77–0.99 大概率是样本内（B）。我们用练习题结果调常数是允许的（A），
   provenance 已经按 #24 写好样本内例外；但 `approx_truth.py` 是"凭记忆写的"，#24 要求**首次公布值 + 来源 + 取数日期**。
   如果 Final 版本真用它定过任何常数，建议补齐来源；只用于相对诊断的话，现有写法够用。
4. **House 调用：** 时延 18–52 秒（A），我们单次 200 秒超时、House 阶段 330 秒内结束，在 600 秒限内合适；
   等 Final 资源公告（10-13 前）再核对一次。temperature 0 + 固定 seed，配合复跑核验。
5. **镜像细节：** LABEL、amd64、不带 macOS xattr、尽量小（拉取时间算进每个 unit 的时限）。我们 CI 已覆盖。
6. **等两个未决裁定**（[T4#25](https://github.com/Agenthon-2026/track4-analysis-public/issues/25)：naive 规则怎么构造、恰好等于参考值算哪类）。
   出来后看是否影响我们 up/down 题的边界处理。

### 两个赛道通用

- **Final 尽早上传**（同分先传者胜，A）。10-13 12:00 UTC 开放后，版本确定就传，不要拖到 10-25。
- **仓库可见性**：`dapeipeipeipei/agenthon-2026` 是公开的，网页搜索排第一。对手能看到我们的 v4 参数、F4 的赌注和 T4 的 House 合并规则。
  建议老板决定是否在 Final 结束前转为私有（不影响核验，provenance 随源码保存即可）。

---

## 5. 待跟进的主办方答复

| issue | 待答 | 影响 |
|---|---|---|
| [T2#26](https://github.com/Agenthon-2026/track2-forecasting-public/issues/26) Q2 | Final F2/F4 窗口是事前还是事后选的 | 决定 F4 档位（最大单项风险） |
| [T4#25](https://github.com/Agenthon-2026/track4-analysis-public/issues/25) Q1/Q3 | naive 规则怎么构造；恰好等于参考值怎么标 | T4 边界与锚点 |
| 各赛道 Final 资源公告（10-13 前） | 单 unit 时限、阶段时钟、House 上限 | 两个镜像的超时参数 |
| [T2#25](https://github.com/Agenthon-2026/track2-forecasting-public/issues/25) Q3 / T2#4 | 计分器构建标识、输入版本 | 低 |

原始 issue 文本在本次会话的 scratchpad 里没有入库；需要时用 `gh issue view N -R Agenthon-2026/<repo> --comments` 重取。
