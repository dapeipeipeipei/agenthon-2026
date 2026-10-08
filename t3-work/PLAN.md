# T3（加速市场模拟）— 作战计划与第一阶段结论

更新：2026-10-08 午后（本地 Windows 跑通参考引擎 + 官方评分器后）。

## 第一阶段结论（≤2 h 检查点）

**可以做，而且路线很清楚：把主办方的参考引擎原样打包就已经是一个「可录取」的提交；接下来所有工作都是在不改变输出的前提下让它更快。**

### 1. 「原样打包参考引擎」是否被允许

- 许可证：`track3-simulation-public` 的 `LICENSE` 是 MIT；`DATA-LICENSE.md` 说明仓库内一切（含 adapter `baselines/abides_fork/`、四个补丁）默认 MIT；上游 ABIDES 为 BSD-3（`THIRD-PARTY-NOTICES.md`）。两者都允许复制、修改、再分发（保留版权声明即可）。
- 比赛规则 §6（agenthon.net/rules）：官方提交必须是容器镜像并实现赛道 CLI；§6.1：「已有软件、公开数据、预训练模型、研究代码…只要合法、许可证正当且赛道允许即可使用」。许可政策页（agenthon.net/licensing）§3：示例、基线、评分器等主办方公开软件可按其公开许可证复用。没有任何「必须原创」「不得提交基线」的条款。
- 主办方在 issue #1 的正式回答：「**慢于基线但正确的提交仍然进榜**，只会贴一个信息性标签 `t3.throughput_nonimproving`，不影响录取和名次」。评分器 `baselines/README.md §3` 也明确该标签 "changes neither admission nor the ranked score"。
- 结论：打包参考引擎是**合规且可录取**的；领奖时需要「资源/来源声明」（§7），如实写明基于主办方 ABIDES 基线即可。

### 2. 参考引擎本地跑通并通过官方评分器

本机没有 Docker 守护进程、WSL 起不来，所以用 winget 装了 Python 3.11.9 并复现基线栈（numpy 1.26.4 / pandas 1.5.3 / scipy 1.17.1 / pyarrow 15.0.2 / coloredlogs 15.0.1；ABIDES `f9cbe51` + 4 个补丁）。两个 Windows 坑位：补丁文件在本机是 CRLF 要先转 LF；`abides_core.utils.str_to_ns` 在 Windows 上 `astype(int)` 是 int32 会溢出，本地改成 `int64`（Linux 上本来就是 int64，镜像里不需要）。`resource` 模块用了一个只读峰值内存的垫片。

官方评分器 = `qfbench2_track_simulation.scoring.build_developer_verifier`（g0–g3 四道门：sidecar 完整性/自洽、与场景绑定、语义回归 Tier A/B、风格化事实、消息账本）。`t3-work/tools/score_local.py` 直接调它，不经过 Docker。

抽样 6 个单元（覆盖 agent-mix/latency/exchange-protocol+STP/reactive+jump/matching/batch）：**6/6 PASS，trace.parquet 和 message_trace.parquet 的 sha256 与主办方参考文件逐字节相同**。全部 71 单元的本地跑在后台进行中（结果见 STATUS.md）。

### 3. 参考引擎的速度（本机 i7-13700K，单进程整进程墙钟 = 解释器启动+import+模拟+写 parquet）

| 单元 | 事件数 | 整进程墙钟 | 事件/秒 |
|---|---|---|---|
| t3-s001-price-time-priority | 604 | 0.61 s | 985 |
| t3-eq-deterministic-baseline | 10 669 | 1.55 s | 6 890 |
| t3-as01-base-mix | 24 695 | 2.44 s | 10 115 |
| t3-ra01-fundamental-shock-mid | 36 431 | 2.63 s | 13 855 |
| t3-mp01-stp-newest-baseline | 72 044 | 4.30 s | 16 775 |
| t3-gbatch-dense-3（3 个子场景串行） | 24 751 | 1.88 s | 13 155 |

主办方自己记录的 65 个参考 `events.json`（只计模拟循环）几何均值 13 793 ev/s；榜单量的是容器从启动到退出的整段时间，所以小单元被固定开销（Python 启动 + pandas/abides import ≈ 0.9 s）压得很低。

### 4. 评分方式要点（决定优化方向）

- 榜分 = **全部单元事件/秒的算术平均**（每单元权重相同），单元事件/秒 = 主办方数的 parquet 行数 ÷ 容器墙钟（Dev：create→remove，跑一次；Final：StartedAt→FinishedAt，5 次取后 4 次中位数，每次 ≤300 s）。
- 每个单元必须和参考**行数完全一致**（Tier B 也要），Tier A 还要成交序列/事件多重集/Kendall-τ 完全一致；65/71 单元要求 message_trace.parquet。
- Dev 每单元分数上限 1e7，且 Dev 榜只算 10-07 13:47 UTC 之后的跑；Final 一队一份。

## 第二阶段：在输出完全不变的前提下提速（当前进行中）

cProfile（t3-mp01，7.9 s 带探针 / 4.3 s 实跑）分解：
- 内核事件循环 + 代理 + 交易所 ≈ 59%；其中 `logger.debug("...".format(order))` 虽然日志关着但字符串**照样先格式化**（`Order.__str__`→`fmt_ts`→pandas Timestamp）≈ 1.2 s；`deepcopy` ≈ 0.6 s；`np.clip` 标量 + lognormal 每条消息 ≈ 0.5 s；`queue.PriorityQueue` 带锁。
- `ExchangeAgent.kernel_terminating → analyse_order_book → get_time_dropout`（pandas iterrows 遍历 28k 行账本快照）≈ 1.85 s，**纯善后分析，不进任何输出**；`append_book_log2` 每笔订单拍一次 L2 快照 ≈ 0.56 s，同样不进输出。
- `parse_logs_df` + `extract_trace` + `extract_message_trace`（pandas 构造/排序）≈ 1.7 s。
- 启动：pandas 0.36 s + abides 包 0.54 s。

计划的动作（每一步都用 71 单元 + 官方评分器回归，输出 sha256 对照参考）：
1. 把打过补丁的 ABIDES 两个包 vendor 进 `t3-work/engine/`（BSD-3 声明保留），adapter 复制为 `jpsim/`；
2. 关掉不进输出的工作：`book_logging=False`、跳过 `analyse_order_book`、交易所不再 logEvent 非订单消息、去掉所有 `logger.debug` 的即时格式化；
3. 事件队列 `PriorityQueue → heapq`（同一比较语义）；标量 `np.clip → min/max`；`gc` 冻结；
4. 轨迹提取不走 pandas：直接从代理日志拼列、按与 `extract_trace` 完全相同的排序规则输出，`pyarrow` 直写 Snappy parquet；消息账本同理；
5. 启动瘦身：进程内不再 import pandas/coloredlogs，只留 numpy + pyarrow；
6. batch 单元：子场景用 `multiprocessing`（fork）并行 4 核，各子进程自带计数器复位，输出与串行完全一致；
7. 之后视时间：`deepcopy` 换定制浅拷贝、PriceLevel 总量缓存、PyPy 可行性评估。

### 第二阶段进展（10-08 晚）

- 第 1–5 轮全部落地（上面 1–6 条 + `-O`、去掉 LAST_TRADE/HOLDINGS 等不进输出的日志、Order 去掉 ABC 元类、账本行改元组）：本机 71 单元均值 **50 659 ev/s**（参考 13 988，**3.62×**），71/71 与参考逐字节相同。
- **Cython（纯 Python 模式编译 14 个热模块）：CI 实测比纯 Python 慢约 10%**（对象操作为主的代码 Cython 没有收益，还多了调用开销），已删除。
- **PyPy 3.9 + numpy 1.26.4 跑模拟、CPython 3.11 + pyarrow 写 parquet**（`Dockerfile.pypy`，`jpsim/writer.py` 两段式交接，本地 CPython↔CPython 交接验证逐字节相同）：CI 构建中，结果见 STATUS.md。
- CI（GitHub runner）同一镜像两次跑均值 36.7k / 33.1k ev/s：runner 之间波动 ±15%，CI 时间只能看相对值。

## 交付物

- `t3-work/Dockerfile`（linux/amd64，`qfbench2.interface_version="2.0"`，非 root，`simulate`/`simulate-batch` 在 PATH，无 ENTRYPOINT，无 VOLUME）
- `.github/workflows/t3-image.yml`：构建 → 在镜像内用平台参数跑全部 71 单元 → 官方评分器 → 时间表 → 推 `ghcr.io/dapeipeipeipei/jinpei-t3` → digest
- `t3-work/submission/submission.json` / `submission.final.json`（`category: simulator`，`models: []`，sealed）
- `pack_all.py` 增加 `t3-<x>` 任务
- `t3-work/STATUS.md`

## 风险

- 主办方计时含容器启动，本机数字只能看相对值；Final 时限 300 s/次，最大单元参考引擎本机 110 s，安全。
- 任何改动都可能动到 RNG 消耗顺序（全局 `np.random` 还被 oracle 的 megashock 抽样使用），所以**不改任何抽样调用的顺序和参数**。
- Dev 上传次数：T3 还有 23 次（每天 5 次）；10-12 20:00 UTC 之后的上传不再跑。
