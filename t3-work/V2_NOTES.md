# T3 第二轮笔记（V2）— 评分口径、时间去向、各条线的天花板

更新：2026-10-08 深夜（第二轮调研结论；供 `t3/startup`、`t3/kernel-cpp`、`t3/v2` 三条线共用）。
数据来源：主办方 issue #2 / #14 / #17 / #23 / #24 / #27 / #28 / #29 / #34、README「How throughput is measured」、
我们 CI 的 71 单元计时（`C:\Users\wensh\.cache\agenthon-t3\cilast\t3-ci-out\out\timing.json`）和本机 round-5 计时。

## 1. 榜分到底怎么算（已核实）

| 项 | Dev 榜（10-07 13:47 UTC 起） | Final |
|---|---|---|
| 单元分子 | 主办方自己数的 `trace.parquet` 行数（batch 单元 = 各子场景行数之和）；**必须与参考行数完全相等，Tier B 也一样**（#28，否则该单元拒绝） | 同 |
| 单元分母 | **容器从 create 到 remove 的整段时间**，跑 1 次（#2 的 10-07 公告；含启动+善后） | Docker daemon 的 `State.StartedAt → State.FinishedAt`，5 次跑取**后 4 次中位数**，第 1 次是热身但也必须成功且输出字节一致（#24） |
| 容器内什么在窗口里 | create/start/wait/读输出/remove 全在（#34 提问中，主办方未答） | runc 自身启动 + GPU 设备挂载在窗口内（每个单元 `gpu=true`，CPU 镜像也挂）（#23） |
| 汇总 | **全部单元的算术平均**，每单元同权；失败单元记 0 仍进平均（#27） | 同；单元限时 300 s，超时/崩溃/OOM 记 0 |
| 上限 | 单元 1e7 ev/s 截断；自报 >1e9/市场 拒绝 | 同 |
| 自报 `events.json` | 只查自洽（±5%、行数），**不进榜** | 不进榜 |
| CPU | `--cpus=4`（CFS 配额，不 pin 核）、16 GiB；x86-64-v3 可用（AVX2/FMA/BMI2），别更高（#29） | 同；跑分主机独占，单元按 roster 顺序串行 |
| 参考输出 | 自己各次重复必须 `trace.parquet`/`message_trace.parquet` 字节一致；**与主办方参考文件不要求字节一致**，只要求行数相等 + Tier A/B 语义门 | 同 |

结论：**分母里有一段与我们代码无关的固定容器开销 c**。#34 里一支队伍（10-07 后两次 Dev 跑）实测：
「每个单元几乎全部时间是约 0.5 s 的固定每容器开销，我们的模拟器本身在大多数单元上 < 0.1 s；两次跑总分差 0.1%」。
也就是说榜首那批队伍的引擎是编译型、模拟时间趋近 0，Dev 榜分 ≈ mean(N_i / c)。

## 2. 用 71 个公开单元反推：什么能到 20 万

模型：单元分 = N_i / (c + s + t_i / f)，c = 平台固定容器开销，s = 我们进程启动（解释器+import+写文件的固定部分），
t_i = 我们现在在 CI 容器里的模拟时间（CI 窗口减去 s001 的 0.53 s 固定部分），f = 引擎提速倍数。
k 是「平台机器相对 GitHub runner 的慢速系数」，用我们的真实平台分 34 774 反解。

- 71 单元：N 均值 108 853，中位数 71 932，总和 7.73 M；最大 gb-mega 1 168 360，最小 s001 604。
- **纯固定开销天花板 mean(N_i/c)**：c=0.35 → 311 k；c=0.5 → 218 k；c=0.65 → 167 k。榜首 231 k ⇒ 平台 c ≈ 0.45 s 且模拟≈0。
- 我们的 CI 均值 32.0 k（这次镜像）；本机 50.7 k；平台 34.8 k。

| c | 反解 k | s=0.25（现在的 Python 启动） f=1 / 4 / 10 / 30 / 100 | s=0.10 f=1 / 4 / 10 / 30 / 100 | s=0.03 f=1 / 4 / 10 / 30 / 100 |
|---|---|---|---|---|
| 0.35 | 1.03 | 30k / 66k / 95k / 129k / 157k | 33k / 77k / 114k / 160k / 202k | 34k / 84k / 127k / 182k / 233k |
| 0.50 | 0.91 | 30k / 62k / 85k / 111k / 130k | 33k / 70k / 99k / 132k / 159k | 34k / 75k / 108k / 146k / 178k |
| 0.65 | 0.80 | 31k / 58k / 77k / 97k / 112k | 33k / 65k / 88k / 113k / 132k | 34k / 69k / 95k / 123k / 144k |

读法：
- **只靠引擎提速、启动仍是 Python（s≈0.25）：f=100 也只到 130–157 k。**
- **启动降到 0.03 s（无 Python，静态二进制）+ 引擎 ×30–100：178–233 k。** 这就是榜首那批。
- **Python 内的编译扩展（f≈2–4）：45–66 k**，排名约从 15 升到 12–13。

每单元权重相同，所以 12 个大单元（>120 k 事件）只贡献 CI 均值的 21%；真正拉均值的是中等单元（5–10 万事件）在固定开销上的表现。

## 3. 我们的时间去哪了（第 5 轮引擎）

CI 容器（GitHub runner，StartedAt→FinishedAt）：s001（604 事件）0.53 s ⇒ 这就是「容器 + Python 启动 + import + 写两个 parquet」的固定部分；
eq-deterministic（10.7 k）0.86 s；as01（24.7 k）1.10 s；gb-mega（1.17 M）32.5 s（≈36 k ev/s 稳态）。
本机（Windows，仅看相对值）：裸解释器 74 ms、`import numpy` 85 ms、`import pyarrow(.parquet)` 25 ms、引擎模块 ~40 ms；Linux 上裸解释器约 20 ms。
估计 Linux 容器里 Python 侧固定开销 ≈ 0.15–0.25 s，其余 ≈ 0.3 s 是容器本身（与榜首相同，无法优化）。

稳态模拟（Python）：参考引擎 ~14 k ev/s，我们 36–50 k ev/s；cProfile 分布见 PLAN.md 第二阶段（事件循环+代理+交易所 ≈ 60%，轨迹抽取+parquet ≈ 20%，启动 ≈ 20%）。

## 4. 对三条线的含义

- `t3/startup`：Python 进程固定开销每省 0.1 s，均值约 +10%（表 2 中 s 列）。可做：不 import numpy（RNG 必须保留 numpy，除非内核线接手）、
  pyarrow 换自写 parquet 写入器、`python -S -E -I`、zipapp/.pyc、更小基础镜像（镜像大小基本不影响 runc 启动，但 layer 数/ENV 可能影响）。
  天花板：s→0.03 但 f=1 只到 34 k（不值）；必须和内核线叠加才有意义。batch 单元现在 fork 4 进程已是 47–64 k。
- `t3/kernel-cpp`：唯一能到 15 万以上的路。**必须同时拿走启动**（静态二进制、自写 parquet），否则封顶 130–157 k。精确语义清单见 §5。
- `t3/v2`（本线，Cython/编译扩展）：f 期望 2–4 ⇒ 45–66 k；是保底，不是答案。

## 5. 内核重写必须复刻的精确语义（给 t3/kernel-cpp；全部来自读代码）

### 5.1 numpy 1.26 legacy `RandomState` 的算法（bit-exact 可做）
- 种子：`RandomState(seed=int)` 和 `np.random.seed(int)` 都走 `mt19937_seed` = 经典 `init_genrand`（`key[0]=seed; key[i]=1812433253*(key[i-1]^(key[i-1]>>30))+i`），pos=624，`has_gauss=0`。
- `random_sample`/next_double = res53：`a=next32()>>5, b=next32()>>6; (a*67108864+b)/9007199254740992`。
- `gauss`（legacy 极坐标法，带缓存）：`do{x1=2u-1;x2=2u-1;r2=x1²+x2²}while(r2>=1||r2==0); f=sqrt(-2*log(r2)/r2); 缓存 f*x1, 返回 f*x2`。**缓存是每个 RandomState 一份**。
- `normal(loc,scale)=loc+scale*gauss`；`lognormal(mean,sigma)=exp(normal)`；`exponential(scale)=scale*(-log(1-u))`；`pareto(a)=exp((-log(1-u))/a)-1`；`uniform(lo,hi)=lo+(hi-lo)*u`（range 先在 Python 里算成 float）。
- `randint(low,high)`（legacy，use_masked）：`rng=high-1-low`；rng==0 直接返回；rng==0xFFFFFFFF → `low+next32()`（配置里 `randint(0,2**32,dtype=uint64)` 就是这条：**一次 32 位输出**）；否则 `mask=下一个2^k-1`，`do v=next32()&mask while v>rng`。`randint(0,2)`、`randint(2)` = 一次 32 位输出取最低位。
- `np.log(mean_ns)`（latency 的 mu）只算一次，用 libm log。

### 5.2 全局 RNG（`np.random.seed(scenario.seed)` 后）的消耗顺序
1. oracle 的 symbol `random_state`：1×uint32（`_draw_random_state`）
2. `SparseMeanRevertingOracle.__init__`：全局 `exponential(1/lambda)` 1×double；再用 symbol RS：`normal(megashock_mean, sqrt(50000))`，`randint(2)`
3. 交易所 RS：1×uint32
4. 每个代理（按 `agent_configs` 顺序 × count）：1×uint32
5. latency RS：1×uint32（有 `latency_config` 时）
6. kernel RS：1×uint32（**抽了但没用**：`abides.run` 没把它传给 Kernel，Kernel 自己建 `RandomState(0)` 且在有 latency model 时从不调用）
运行期间全局 RNG 只被 oracle 的下一次 megashock 间隔 `np.random.exponential` 用。

### 5.3 oracle（`SparseMeanRevertingOracle`）的坑
- 第一个 megashock 时间 `mst = mkt_open + exponential(...)` 是 **float**（≈1.6e18，ulp=256 ns）；后续 `mst = pt + int(exponential)` 仍是 float。
  `while mst < current_time`、`if current_time <= pt` 是 **Python 的 int–float 精确比较**（不是先把 int 转 double）。C++ 里必须用精确比较（long double 或手写），否则在 128 ns 内会判错。
- `d = ts - pt` 混合类型时 Python 把 int 转 double 再减（和 C++ 显式 cast 一样）。
- OU：`v = RS.normal(mu + (pv-mu)*exp(-gamma*d), sqrt((theta**2/(2*gamma))*(1-exp(-2*gamma*d))))`，`theta**2` 是 C `pow(theta,2.0)`；`v += v_adj; v = max(0, v); v = int(round(v))`（round 半偶）；然后 scheduled_jump 一次性 `max(0, v+magnitude)`。
- `observe_price`：`current_time >= mkt_close` 时用 `mkt_close-1` 推进；`int(round(RS.normal(r_t, sqrt(1000.0))))`。
- kappa = 场景 kappa/1e9（无/0 → 1.67e-16）；megashock_lambda_a = jump_intensity/1e9（无/0 → 2.77778e-18）；megashock_mean = jump_sigma 或 1000.0；var 50000。

### 5.4 内核与消息
- 事件堆元素 `(time, (sender, recipient, message))`，平局按 sender、recipient、**message_id**；message_id 在 **Message 对象构造时**递增（从 1），`WakeupMsg()` 在 `set_wakeup` 时构造。所以构造顺序必须一模一样（含从未投递的）。`Order.order_id` 从 0 递增，在 `LimitOrder(...)` 构造时分配，`copy_order` 不分配。
- 代理「还在未来」（`agent_current_times[r] > now`）时消息/唤醒**原样重排**到 `agent_current_times[r]`。
- `send_message`：`sent = now + computation_delay[sender] + current_agent_additional_delay + delay`；`deliver = sent + int(latency)`；账本行 `(message_id, src, dst, sent, deliver, deliver-sent, 类名, order.order_id 或 None, causal_parent)`。
- 交易所：每次收消息先 `set_computation_delay(compute_delay_ns)`（protocol_enforcement 才非 0），`MarketHoursRequest` 时置 0；`OrderAccepted/Cancelled/Executed` 加 `pipeline_delay=ack_delay_ns`；`current_time > mkt_close` 收到 OrderMsg 回 `MarketClosedMsg`（代理随后 `logEvent("MKT_CLOSED")`，不进 trace）。
- 生命周期：`kernel_initializing`（交易所 `set_wakeup(mkt_close)` → message_id 1）→ `kernel_starting`（每个代理 `set_wakeup(start_time=当天 0 点)`）→ 第一次唤醒：代理发 `MarketClosePriceRequestMsg`、`MarketHoursRequestMsg`；收到 `MarketHoursMsg` 后 `set_wakeup(mkt_open+0)`。stop_time = mkt_close + 1 s；弹出 time > stop_time 的事件即停（该事件不处理）。
- 代理计算延迟默认 50 ns。

### 5.5 订单簿/撮合（`order_book.py`）
- STP 只在 `protocol_enforcement` 时启用（`cancel_newest`/`cancel_oldest`），检查最优价位 `peek()` 的 agent_id。
- `execute_order`：`order.quantity >= 最优订单量` 则 `pop()`（整单），否则拷贝部分成交；成交价 = 被动单 limit_price；两边各发一条 `OrderExecutedMsg`（被动方先）。
- `enter_order`：空边 append；比最差还差 append；否则线性找第一个「更优」插入或「相等」追加。`cancel_order` 线性扫价位。
- `handle_limit_order` 结束时 logEvent `BEST_BID`/`BEST_ASK`（字符串 `"ABM,price,qty"`）→ trace 的 QUOTE_UPDATE；`last_trade = int(round(Σp·q/Σq))`。
- `cancel_all_orders` 按代理 `self.orders` **dict 插入顺序**发 Cancel。

### 5.6 trace 行的生成规则（`jpsim/trace_fast.py`，与参考 `extract_trace` 等价）
- 按代理顺序、再按各自 log 顺序收集：`ORDER_SUBMITTED/ACCEPTED/CANCELLED/REPLACED/EXECUTED`（取 order dict 的 agent_id/side/limit_price|fill_price/quantity/order_id），`t = 代理记日志时的 current_time`。
- `ORDER_EXECUTED` 按 t 稳定排序后每个 order_id **最后一条**记 `ORDER_FILLED`，其余 `PARTIAL_FILL`；price 用 fill_price。
- 交易所 `BEST_BID/BEST_ASK`：按 `(t, side)` 去重取**最后值**、按首次出现排序，agent_id=0，order_id=-1，type `QUOTE_UPDATE`。
- 全部行 = [订单行..., 报价行...] 后做 **稳定 lexsort(t_ns, order_id)**。
- message_trace：账本里**被投递**的消息（seq 由投递顺序给；唤醒也计，type `AGENT_WAKEUP`，t_send/order_id/causal_parent 为 null），按 seq 排序。

### 5.7 parquet 字节级布局（pyarrow 15.0.2 `write_table(..., compression="snappy")` 默认）
- `created_by = "parquet-cpp-arrow version 15.0.2"`，格式 2.6，数据页 V1，行组 1 048 576 行（gb-mega 分 2 组，第 2 组 119 784 行），
  每列：字典页（PLAIN）+ 数据页（RLE_DICTIONARY），列块 encodings 列表 `('PLAIN','RLE','RLE_DICTIONARY')`，列块统计 min/max/null_count（无 distinct），
  key-value 元数据 `pandas`（见 `trace_fast._pandas_metadata`，顺序 pandas 在前）和 `ARROW:schema`（Arrow IPC schema 的 base64）。
- schema：trace `t_ns i64, agent_id i32, msg_type str, side str, price i64, size i64, order_id i64`；message_trace 10 列，`t_send_ns/order_id/causal_parent` 可空（pandas 元数据 numpy_type `Int64`）。
- 要字节一致必须复刻：字典页/数据页的切分（默认 data page 1 MiB、字典上限 1 MiB 后回退 PLAIN）、RLE/bit-pack 的位宽选择、snappy 1.1.10 的压缩输出、thrift 紧凑编码的字段顺序。
  **不追求字节一致也合规**：规则只要求行数相等 + 语义门 + 自己重复一致；那样用最简单的 PLAIN/无压缩写入器即可，然后用「读回列比对」替代 sha256 做自检。

### 5.8 libm
- 参考轨迹由 Linux（glibc 2.36，bookworm）上的 numpy 生成；numpy 的 legacy 分布直接调 libm `exp/log/sqrt`。glibc ≥2.28 的 exp/log/pow 与 musl ≥1.2 同源（ARM optimized-routines），
  C++ 里直接用 glibc 或内嵌 musl 版本都能 bit-exact；mingw 本地验证不保证（mingw 有自己的 x87 实现），本地只做 smoke，以 CI/Linux 为准。
- 编译必须 `-ffp-contract=off`（x86-64-v3 开了 FMA，GCC 默认会把 a*b+c 合成 FMA 改变舍入）；不要 `-ffast-math`。
- 经验：Windows（UCRT libm）跑的 numpy 71/71 与 Linux 参考字节一致，说明 1-ulp 级 libm 差异在整数化输出里基本不可见——但不要依赖这一点。
