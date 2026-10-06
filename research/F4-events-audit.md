# F4 事件表核对记录（2026-10-06）

> **本文件引用了旧表原文，旧表原文里有窗口内的真实结果。只供人工审计，引擎、生成脚本和任何提示词都不得读取本文件。**

核对对象：`research/F4-events.md`（旧版由 Claude 预填，标记"待核对"）。
核对方法：
1. 逐题读 `track2-forecasting-public/units/t2-F4-*/card.toml`（as-of、horizon、value_unit、notes）和 `text/corpus_index.json`，用关键词上下文检索核对每条说法在语料里有没有出处。
2. 用公开史料核对日期、事件名称，以及某件事在 as-of 那天**是否已经能知道**。
3. target 日期用 `np.busday_offset(asof, h)` 复算（与 `t2-work/realized.py` 同口径）。

## 总结

- **31/31 行全部改写。** 每一行旧"窗口内事件"都写了 as-of 之后才发生的事（破产、开战、公投结果、加息幅度、具体数据值、单日暴跌日期等），每一行旧"对该资产的方向"都是事后结果。两列整列删除，换成"as-of 时已知（带语料出处）""窗口内已排期的催化剂""事前分支"三列。
- **旧"类型"列有 4 行把结果写进了类型**：ust-downgrade-watch-2011 写成"反直觉"，svb-whiplash-2023、powell-december-2018、momentum-reversal-2009 写成"反转"。已改成事前类型（双向竞争 / 条件冲击）。
- **target 日期：31 行全部复算正确**，没有改。补充了两点：NFP、CPI 两道题的真实目标是"某一期数据的首发值"，不是表上那个日期的数值；10-13-2008 是哥伦布日，H.10 汇率和美债都没有报价，realized 取的是最近一天。
- **资产映射错误 2 处**：cpi-vintage-2022 旧表写的是"5 月 CPI 同比 8.6%"，但目标是 **6 月** CPI 指数水平的首发值（2022-7-13 发布），5 月那期只是中间期；covid-nfp-2020 旧表写的是"4 月非农 -2000 万"（变化量），但目标是 4 月 PAYEMS **水平**的首发值。另外在表头写明了 CHF、JPY、NOK 是"每美元多少本币"，本币升值时数值下降，旧表写的"CHF 暴涨""NOK 暴跌"容易被误读成数值方向。
- **题卡 notes 说语料里有、但语料里实际没有的 9 处**（引擎读不到这些，检测器也不可能靠它们触发）：funding-stress-2y-2008（没有两房接管和雷曼）、chf-floor-strain-2015（没有瑞士央行 12-18 的负利率决定）、eur-warnings-2022（没有明确的入侵预警，只有"地缘紧张"）、short-vol-2018（没有做空波动率产品拥挤的文本）、ust10y-supply-2023（没有发债供给和停摆风险文本）、momentum-reversal-2009（没有美国压力测试文本，国有化只出现在英国语境）、china-panic-2016（没有熔断机制）、vaccine-timeline-2020（8-K 里没写明"11 月"）、gbp-conference-2016（没有党代会和第 50 条）。新表在相关行标了"公开（不在语料）"或加粗注明。

## 逐题改动

记号：**[泄漏]** = 写了 as-of 之后的事；**[映射]** = 资产或目标定义错误；**[缺口]** = 题卡或旧表说的内容语料里没有；**[事实]** = 说法本身不准确。

| 题 | 旧（原文摘录） | 问题 | 新（摘要） | 证据 |
|---|---|---|---|---|
| aud-gfc-2008 | 雷曼 9-15 破产…；方向"AUD 大跌" | [泄漏] 9-15 破产发生在 as-of（9-12，周五）之后 | 已知：澳储行 9-2 首次降息；美方语料积累系统性风险；公开：两房 9-7 被接管，某券商周末正在找买家、结局未知。分支：系统性 / 勉强过关 | bis_stevens_2008-09-08, bis_bernanke_2008-07-08/07-15, bis_hoenig_2008-09-01, fomc_minutes_20080805；题卡 notes |
| chf-floor-strain-2015 | 瑞士央行 1-15 突然放弃下限；方向"CHF 暴涨" | [泄漏] 1-15 在 as-of（12-19）之后；[映射] 单位是 CHF/USD，CHF 升值时数值下跌；[缺口] 12-18 负利率决定不在语料里 | 已知：1.20 下限 +"无限量"承诺；欧央行铺垫宽松；公开：12-18 负利率、黄金公投被否、希腊总统选举首轮未果。分支：守住 / 松动或放弃 | bis_jordan_2014-12-01（"maintain the minimum exchange rate of CHF 1.20… unlimited quantities"）, ecb_draghi_2014-11-* |
| china-panic-2016 | 1 月初 A 股熔断、人民币贬值、全球大跌 | [泄漏] 结果；[缺口] 熔断机制本身不在语料里（属于公开可知：12-4 宣布、1-1 生效） | 已知：12-16 首次加息；中国与汇率担忧。日程：1-8 非农、FOMC 1-26/27 | fomc_statement_20151216, bis_fischer_2015-11-19, bis_shirai_2015-11-20 |
| china-stress-mkt-2015 | 8-11 人民币汇改贬值，8-24 全球股灾 | [泄漏] | 已知：中国股市暴涨后剧跌、当局救市；各央行讲话提中国风险；美股面板平静 | bis_stevens_2015-07-22（"spectacular developments in the equity market"）, bis_kuroda_2015-07-21, bis_nakaso_2015-07-27 |
| covid-mkt-2020 | 2-24 起崩盘，3 月多次熔断 | [泄漏] | 已知：苹果 2-17 营收达不到指引；欧日央行点名新冠风险；as-of 当天标普处于历史高位 | apple_8k_2020-02-18（"we do not expect to meet the revenue guidance"）, bis_guindos_2020-02-03, bis_lagarde_2020-02-05 |
| covid-nfp-2020 | 4 月非农 -2000 万，史上最大；方向"NFP 极端负" | [泄漏] 具体数值；[映射] 目标是 4 月**水平**的首发值，不是变化量；target 日期只是名义日期 | 已知：3-3、3-15 紧急降息，3-23 无限量 QE（都在语料里）；面板截至 2 月。分支：停摆主导 / 部分停摆，区间极宽 | fomc_statement_20200303/0315/0323；题卡 notes（"Forecast the April 2020 payrolls LEVEL as first published"） |
| covid-rates-2020 | 避险 + 美联储 3-3、3-15 两次紧急降息 | [泄漏] 两次降息都在 as-of（2-14）之后；补充：例会 3-17/18 在窗口外 | 已知：面板平静；央行讲话点名新冠；1-29 按兵不动 | bis_elderson/guindos/lagarde/wakatabe, fomc_statement_20200129 |
| cpi-friday-2022 | 6-10 CPI 超预期，6-15 加 75bp | [泄漏] | 已知：5-4 加息 50bp、更大步伐"排除在外"；4 月 CPI 8.3%、3 月 8.5%。日程：6-3 非农、6-10 CPI、6-14/15 FOMC | fomc_statement_20220504, fomc_minutes_20220504, cpi_2022-05-11 |
| cpi-vintage-2022 | 5 月 CPI 同比 8.6% 创新高；"单次发布 / 高于预期" | [泄漏] 数值与方向；[映射] 写成了中间期（5 月），目标是 6 月指数水平的首发值 | 已知：3 月 8.5%、4 月 8.3%，面板截至 4 月。分支：能源继续上冲 / 回落 | cpi_2022-04-12, cpi_2022-05-11；题卡 notes（"Forecast the June 2022 CPI index level AS FIRST PUBLISHED"） |
| eur-warnings-2022 | 2-24 俄乌开战；方向"EUR 下跌" | [泄漏]；[缺口] 题卡说"警告明确公开"，但语料只有"地缘政治紧张加剧" | 已知：欧央行、西班牙央行提地缘紧张与能源；公开：俄军集结、美方 2-11 起警告。日程：欧央行 3-10、FOMC 3-15/16 | ecb_lagarde_2022-02-07/02-14_2（"geopolitical tensions have increased"）, bis_cos_2022-02-17（"geopolitical risks which could potentially be highly disruptive"） |
| factor-stress-2008 | 雷曼破产，10 月初崩盘；方向"QMJ 上涨" | [泄漏] 包括 QMJ 的实际方向 | 已知：同 2008 语料。分支：系统性（联合尾部）/ 勉强过关 | 同 aud-gfc-2008，另有 empsit_2008-09-05 |
| funding-stress-10y-2008 | 雷曼，避险与流动性挤兑交织 | [泄漏] 雷曼 | 双向竞争：避险压低收益率 vs 救助融资供给推高 | 题卡 notes；cpi_2008-08-14 |
| funding-stress-2y-2008 | 雷曼 + 美联储 10-8、10-29 连续降息 | [泄漏] 日期本身无误，但都在 as-of 之后；[缺口] 题卡说语料里有两房接管和"失败的券商"，实际没有 | 已知：同 2008 语料。日程：FOMC 9-16、10-28/29（12-16 在窗口外） | 关键词检索：t2-F4-funding-stress-2y-2008/text 中 "conservatorship" 0 次、"lehman" 0 次；最晚的美方文档是 empsit_2008-09-05 |
| gbp-brexit-2016 | 6-23 脱欧公投，结果脱欧 | [泄漏] 公投结果；公投日期本身是事前已知，保留 | 已知：金融政策委员会称公投是最显著的近期风险；欧央行、美联储提及。分支：留欧 / 脱欧，不预设 | bis_cunliffe_2016-04-26, bis_cœuré_2016-03-30, fomc_minutes_20160427（"upcoming British referendum on membership"） |
| gbp-conference-2016 | 10-2 梅宣布明年 3 月触发脱欧程序，10-7 闪崩 | [泄漏] 两件都在 as-of（9-30）之后；[缺口] 语料里没有党代会和第 50 条 | 已知：公投后面板企稳；公开：第 50 条时间表未定。日程：保守党年会 10-2 至 10-5、10-7 非农 | fomc_minutes_20160727, bis_iwata_2016-08-04 |
| hike-cycle-2021Q4b | 2022 上半年从 0 加到 1.75%，节奏不断上修 | [泄漏] | 已知：12-15 缩减购债步伐翻倍、"通胀已超过 2% 一段时间"；公开：点阵图显示 2022 年加息 3 次。日程：4 次 FOMC | fomc_statement_20211215, fomc_statement_20211103 |
| hml-covid-2020 | 价值股（能源金融）跌得更惨；方向"HML 大幅为负" | [泄漏] | 条件冲击：停摆分支下 HML 是暴露最大的因子（题卡的事前推理），不写结果 | 题卡 notes；同 covid-rates-2020 语料 |
| jpy-carry-2007 | 8 月次贷恐慌，8-16 日元单日暴涨 | [泄漏] | 已知：COT 日元投机空头接近纪录；次贷与杠杆压力；公开：贝尔斯登基金清零、7-10 起大批降级。日程：8-3 非农、FOMC 8-7 | cftc_cot_japanese_2007（"last report 2007-07-17 (public release 2007-07-20)"）, bis_warsh_2007-06-05（"compelled to sell assets to meet margin calls"）, fomc_minutes_20070628 |
| jpy-crowding-2024 | 7-31 日本央行加息，8-5 全球套利平仓 | [泄漏] 8-5 部分；7-31 加息就在 as-of 当天、语料里有，**保留** | 已知：日央行加息到约 0.25% 并公布缩减购债计划；COT 空头仍接近极端；FOMC 按兵不动 | boj_ycc_20240731（"remain at around 0.25 percent"）, cftc_cot_japanese_2024, fomc_statement_20240731 |
| mkt-selloff-2011 | 8-2 最后一刻通过，8-5 标普下调评级，8-8 大跌 | [泄漏] 三件都在 as-of（7-22）之后；8-2 作为**期限**是事前已知，保留 | 已知：伯南克警告降级风险；技术性违约之争；公开：标普 7-14 负面观察。分支：协议解脱 / 信心冲击 | bis_bernanke_2011-06-14（"induce ratings downgrades of U.S. government debt"）, bis_smaghi_2011-07-08, fomc_minutes_20110622 |
| momentum-reversal-2009 | 3-9 见底后垃圾股暴力反弹，动量因子史上最大崩溃；类型"反转" | [泄漏] 见底日期与结果；类型带了结果；[缺口] 语料里没有美国压力测试文本 | 条件冲击（DM16 反弹期权）：反弹分支 / 持续承压分支。日程：FOMC 3-17/18、4-28/29，G20 4-2 | bis_papademos_2009-02-11, bis_gieve_2009-02-19（"nationalised utilities"，英国语境） |
| nok-covid-2020 | COVID + 3-8 油价战 | [泄漏] 3-6 OPEC+ 谈崩和 3-8 价格战都在 as-of（2-28）之后；OPEC+ **会议日期**是事前已知，保留 | 已知：2-24 那周全球大跌（面板可见）；疫情全球化；COT 原油多头偏建设性。日程：OPEC+ 3-5/6 | bis_guindos_2020-02-03, bis_masai_2020-02-06, cftc_cot_crude_2020（"last report 2020-02-25"） |
| powell-december-2018 | 11-28 鲍威尔"接近中性"被解读为鸽派，12-19 加息后市场失望，12 月大跌；类型"反转" | [泄漏] 12-19 及之后；[事实] 原话是"略低于中性利率估计区间的下沿"，不是"接近中性"；类型带了结果 | 已知：11-28 讲话、11 月纪要"进一步渐进加息"、贸易摩擦。日程：G20 12-1、FOMC 12-18/19、1-1 关税升级 | bis_powell_2018-11-28（"just below the broad range of estimates of the level that would be neutral"）, fomc_statement_20181108（"further gradual increases"） |
| qe1-expansion-2009 | 3-18 宣布购买 3000 亿国债，当天收益率暴跌，之后回升 | [泄漏] | 已知：12 月"正在评估"、1-28"准备购买"。日程：FOMC 3-17/18、4-28/29 | fomc_statement_20081216（"evaluating the potential benefits of purchasing longer-term Treasury securities"）, fomc_statement_20090128（"prepared to purchase longer-term Treasury securities"） |
| quant-crowding-2007 | 8-6 到 8-9 量化基金去杠杆，因子集体异常 | [泄漏] | 已知：次贷与杠杆借款人融资收紧；公开：贝尔斯登基金清零、杠杆收购融资受阻。日程：8-3 非农、FOMC 8-7 | bis_bernanke_2007-07-18, fomc_minutes_20070628（"availability of credit to some highly leveraged… borrowers appeared to be tightening"） |
| short-vol-2018 | 2-5 波动率产品爆仓，VIX 单日翻倍；方向"大跌后部分回升" | [泄漏]；[缺口] 题卡说语料记录了做空波动率产品拥挤，实际没有 | 已知：VIX 接近历史低位、估值偏高。日程：FOMC 1-30/31、2-2 非农 | fomc_minutes_20171213（"the VIX… at levels close to historical lows"）, bis_dudley_2018-01-11（"asset valuations… elevated"） |
| svb-whiplash-2023 | 3-10 硅谷银行倒闭…收益率暴跌（史上最大单周跌幅）；类型"反转" | [泄漏] 倒闭和收益率走势；[事实]"史上最大单周跌幅"不准确（通常的说法是 1987 年以来最大的三日跌幅），反正属于泄漏，删除；3-7 鹰派证词和 3-8 的 8-K 都在 as-of 当天或之前，保留 | 双向竞争：鹰派路径 / 银行压力扩散。日程：3-10 非农、3-14 CPI、FOMC 3-21/22 | bis_powell_2023-03-07（"prepared to increase the pace of rate hikes"）, svb_8k_2023-03-08_d430920dex991（"after tax loss of approximately $1.8 billion"）, fomc_minutes_20230201（"large, unrealized losses on some banks' securities portfolios"） |
| taper-warning-2013 | 5-22 伯南克提缩减购债，6-19 确认，收益率飙升 | [泄漏] | 已知：3 月纪要中段的购债速度讨论。日程：FOMC 4-30/5-1、6-18/19 | fomc_minutes_20130320（"most dealers anticipated that the pace of purchases would be adjusted down before ending"；"bring the program to a close"） |
| ust-downgrade-watch-2011 | 8-5 标普下调评级，但避险资金涌入国债；类型"反直觉"；方向"收益率大跌" | [泄漏] 结果、类型、方向三处 | 双向竞争：信用风险（上行）vs 避险（下行），按两支分配质量 | 题卡 notes（"Allocate tail mass across BOTH branches"）；cpi_2011-07-15 |
| ust10y-supply-2023 | 10 月供给压力 + 点阵图偏鹰，10-19 触及 5% | [泄漏] 10-19；[缺口] 题卡说的供给与停摆文本语料里没有。"点阵图偏鹰"（9-20）是事前可知，保留但改标"公开" | 已知：9-20 按兵不动、鲍威尔 8-25"必要时还会加息"；公开：惠誉降级、发债规模上调、停摆风险未解决。日程：10-6 非农、10-12 CPI、10-30 季度融资估算 | fomc_statement_20230920, bis_powell_2023-08-25；关键词检索：该语料 "shutdown" 0 次、"issuance" 只出现在国库券和市政债语境 |
| vaccine-timeline-2020 | 11-9 辉瑞疫苗有效，动量因子单日历史最大回撤 | [泄漏]；[缺口] 8-K 里没有写明"11 月" | 已知：Moderna 首次中期分析有三种结果；褐皮书提大选不确定性。日程：11-3 大选、FOMC 11-4/5 | moderna_8k_2020-10-29（"two planned interim analyses… three potential outcomes"）, beigebook_202009（"the upcoming presidential election"） |

另外，表下"给程序用的抽象"一节删掉了"方向一列是事后知道的，只能用来检验"这句话（那一列已经没有了），换成两条：`skew_dir` 只能来自事前机制；事后真实值只放在 `t2-work/realized/`。

## events.py 漏检核对（只读，未改动）

用 `engine.events.detect` 对 104 道题全部跑了一遍（PYTHONUTF8=1，与引擎同参数）。非 F4 题的 `stress_score` 中位数是 1.98，75 分位 2.92。下面这些 F4 危机题的分数**低于非 F4 题的中位数**，也就是说检测器认为它们比一般的平静题还要平静：

| 题 | stress | 为什么漏 |
|---|---|---|
| jpy-crowding-2024 | 1.08 | 信号在 COT 数字表和 7-31 日央行加息里，文本没有危机词 |
| short-vol-2018 | 1.17 | 风险的形式是"低波动、估值偏高"（自满），词表只认危机词 |
| taper-warning-2013 | 1.21 | 购债速度的讨论不用 "taper" 这个词（2013 年 5 月才流行） |
| china-stress-mkt-2015 | 1.48 | 离岸股市剧跌用的是 "spectacular developments in the equity market""run-up in share prices" |
| quant-crowding-2007 | 1.60 | 杠杆和融资收紧的措辞（"highly leveraged""tightening"）不在 crisis 词表里 |
| covid-nfp-2020 | 1.88 | 紧急降息、无限量购债的措辞（"lower the target range""in the amounts needed"）不算危机词；`inflation_dominated` 也不相关 |
| chf-floor-strain-2015 | 1.92 | "minimum exchange rate" 不在任何词表里；"floor" 只在 policy 族、权重 0.7 |
| china-panic-2016 | 1.92 | 只有汇率与中国的泛泛讨论 |

另外两类问题：
- **binary 族方向反了**：gbp-brexit-2016（公投在窗口**内**）的 `binary_score` 只有 0.32，而 gbp-conference-2016（公投早已过去，语料都在回顾）是 1.32。原因是 binary 词表不区分"即将投票"和"投票之后"。
- **hike-cycle-2021Q4b 的 `inflation_dominated` 是 False**：12 月声明里的鹰派信号是"reduce the monthly pace of its net asset purchases""inflation having exceeded 2 percent"，hawkish 词表都没有覆盖，所以 UST 压力方向会被判反。taper-warning-2013 同理。

### 建议新增的通用模式

不含任何专有名词，在没见过的危机上也能用。下面是我在 104 道题上验证过的结果，看的是每千词密度的排名。

| 建议的词族 / 加入哪族 | 正则 | 104 题上的表现 |
|---|---|---|
| peg_regime → crisis 或 policy（权重 1.0） | `minimum exchange rate\|exchange rate (?:floor\|ceiling\|cap\|peg)\|\bpegs?\b\|unlimited quantities\|defend (?:the\|its) (?:currency\|floor\|peg)` | chf-floor-strain-2015 排**第 1**（0.25），第 2 名是 chf-highly-valued-2021；对其他题几乎是 0，区分度很好 |
| bank_fragility → crisis | `unrealized losses\|after[- ]tax loss\|capital raise\|offering of (?:its )?common stock\|\buninsured deposit\|deposit outflows?\|wind[- ]down\|liquidation\|susceptib\w+ .{0,40}runs` | 前 5 名是 svb-whiplash-2023 和 4 道 2008 题；F4 均值是非 F4 的 2.6 倍 |
| fiscal_deadline → crisis 或 binary | `debt (?:ceiling\|limit)\|technical default\|government shutdown\|statutory (?:federal )?debt\|\bx-date\b` | 两道 2011 题排第 2 和第 3；第 1 名 taper-begins-2013 也确实临近 2013 年 10 月的期限，合理 |
| purchase_pace_hawk → hawkish | `(?:reduc\w*\|slow\w*\|adjust\w* down\|moderat\w*\|taper\w*) (?:in )?the (?:monthly )?pace of (?:its )?(?:net )?(?:asset )?purchases\|pace of purchases would be adjusted down\|bring the program to a close\|exceeded 2 percent` | hike-cycle-2021Q4b 排第 1，taper-warning-2013 排第 7；能修正上面说的 `inflation_dominated` 误判 |
| emergency_easing → crisis（权重 0.7） | `lower(?:ed)? the target range\|to 0 to 1/4 percent\|in the amounts needed\|unscheduled\|inter-?meeting (?:cut\|move)\|effective lower bound` | covid-nfp-2020 排第 7；但正常的降息周期也会命中，建议只在"距上一次声明不到 3 周又出新声明"时加权（不定期会议本身就是危机信号，比关键词更通用） |
| forward_binary → binary（权重 1.0），同时把旧 binary 词在"following / after / outcome of the vote"语境下降权 | `(?:upcoming\|forthcoming\|ahead of the\|scheduled) (?:\w+ ){0,3}(?:referendum\|vote\|election\|ballot)\|referendum on membership` | 区分"事前"和"事后"；gbp-brexit-2016 排第 9。语料里前瞻句很少，单靠它不够，降权回顾语境是主要修正 |
| offshore_equity → crisis（0.5） | `(?:equity\|stock\|share) (?:market\|prices?) (?:turmoil\|correction\|declines?\|fell\|plunge\|volatility)\|run-up in share prices\|capital outflows\|depreciation of the (?:exchange rate\|renminbi\|currency)` | china-stress-mkt-2015 排第 5、china-panic-2016 排第 10；信号弱，权重别给高 |
| leverage_unwind → crisis（0.7） | `margin calls\|forced (?:selling\|sales\|deleverag)\|highly[- ]leveraged\|leveraged (?:loans\|funds\|investors\|positions)\|redemptions\|crowded` | quant-crowding-2007 只排第 32，区分度差；不建议单独依赖 |
| valuation_complacency → uncertainty（0.5） | `(?:elevated\|stretched\|high\|rich) (?:asset )?valuations\|historical lows\|historically low\|low (?:financial market )?volatility\|complacen\|reach(?:ing)? for yield` | short-vol-2018 只排第 21，区分度差；2024 年的 AI 题分数更高。**不建议**加 |
| geopolitical → crisis | `geopolitic\|military\|troops\|armed conflict` | eur-warnings-2022 只排第 13，considerable-period-2003（伊拉克战争）遥遥领先；作为低权重（0.3）补充可以，不能指望它识别 2022 年这道题 |

### 非关键词的建议（同样通用）

1. **COT 持仓极端度**：`cftc_cot_*.txt` 是纯数字表，关键词永远读不到。jpy-carry-2007、jpy-crowding-2024、nok-covid-2020 的核心信号都在这里。建议解析 `noncomm_net`（注意表里混了迷你合约行，要按 `open_interest` 取主合约），算最新一期相对全表的 z 分数或分位数，极端时加宽尾部，偏向放在"拥挤方向被迫平仓"的一侧。
2. **as-of 当天的政策决定文档**：如果某份 decision 或 statement 类文档的日期离 as-of 不超过 1 天（jpy-crowding-2024 的 boj_ycc_20240731、svb-whiplash-2023 的 8-K），说明冲击是新鲜的、市场还没消化完，可以作为独立的加宽因子。
3. **数据发布在窗口内**：现在的 `meeting_in_window` 只看 FOMC 声明。可以照同样的逻辑处理 `cpi_*`、`empsit_*` 这类文档：最近一期发布已经过去 3 周以上，且 h ≥ 15，就判定下一期发布落在窗口内（cpi-friday-2022、short-vol-2018 的催化剂都是这类）。

以上都没有改 `t2-work/engine/events.py`。
