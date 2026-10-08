# jpkernel — C++ 内核线（t3/kernel-cpp）

更新：2026-10-08 晚。

## 一句话

把 ABIDES 的事件循环、交易所撮合、四种赛道代理、稀疏均值回复 oracle、延迟模型和两份输出的
行构造全部用 C++ 重写（`engine/jpkernel/jpkernel.cpp`，单文件，C ABI，`ctypes` 绑定在
`engine/jpsim/kernel.py`），**71/71 单元 trace.parquet + message_trace.parquet 与主办方参考逐字节
相同**；Python 只剩解析 scenario.json、调内核、用 pyarrow 15.0.2 写 parquet。

## 覆盖范围（71 个单元 95 个场景全部清点过）

| 维度 | 单元里出现的 | 内核实现 |
|---|---|---|
| 代理 | NoiseTrader / MarketMaker / ValueTrader / MomentumTrader（kit adapter 的四种）+ ExchangeAgent | 全部 |
| 消息 | Wakeup、MarketClosePriceRequest/Msg、MarketHoursRequest/Msg、QuerySpread/Response、LimitOrder、CancelOrder、OrderAccepted/Executed/Cancelled、MarketClosed | 全部（无 MarketOrder/Replace/Modify/PartialCancel/MessageBatch，单元不用） |
| 延迟 | log_normal 88 / pareto 3 / uniform 3 / deterministic 1 | 全部（`mu = float(np.log(mean_ns))` 仍由 Python/numpy 算，和参考同一调用） |
| oracle | SparseMeanRevertingOracle；jump_intensity>0 的 14 个场景有 Poisson megashock；6 个场景有 scheduled_jump | 全部，含 megashock 后 oracle 时间戳变成 float 的 Python 语义（int/float 精确比较、int+float 提升） |
| 交易所 | protocol_enforcement 的 7 个单元（cancel_newest 4 / cancel_oldest 3），ack/compute delay 都是 0 | 全部 |
| RNG | 全局 MT19937（seed、每个 RandomState 的种子抽取、megashock 指数分布）+ 各 RandomState 的 normal / lognormal / uniform / exponential / pareto / randint | numpy legacy 算法逐位复现：init_genrand 播种、polar Box-Muller 带缓存第二值、`-log(1-u)` 指数、掩码拒绝 randint；`tools/rng_check.py` 对 5 个种子 × 14 种生成器各 30 万次逐位核对 0 失败 |

Python 语义细节：`round()` 半偶、`int(x)` 截断、`max(0, v)`、`(bid+ask)//2`、堆序 `(time, sender, recipient, message_id)`、
消息 id/订单 id 的全局计数器、`stop_time` 之后仍处理一条事件（参考的循环条件先检查再弹出）。

libm：log/exp/pow 走参考 numpy/CPython 用的同一个 C 运行库（Linux=镜像的 glibc 2.36，内核在同一镜像的
build stage 里编译；Windows 本机=ucrtbase.dll 动态解析，避免 MinGW 自带 libm 末位差异）。编译 `-O2 -ffp-contract=off -fno-fast-math`，不用 `-march=native`（禁 FMA 融合）。

## 数字（本机 i7-13700K，一进程一单元，整进程墙钟）

| | 参考引擎 | jpsim v1（纯 Python 瘦身） | jpkernel |
|---|---|---|---|
| 71 单元 ev/s 算术平均（榜分口径） | 13 988 | 50 659 | **271 574** |
| 中位数 | 14 004 | 55 749 | 266 048 |
| 最小（s001，604 事件） | 968 | 1 573 | 3 059 |
| 最大 | 18 454 | 81 183 | 913 378 |
| t3-gb-mega-throughput（1.17M 事件）整进程 | 87.0 s | 19.4 s | 1.28 s（内核 0.47 s，pyarrow 建表+写 0.8 s） |

固定开销：每进程 ≈0.2 s（python 启动 0.05 + numpy 0.08 + pyarrow 0.02 + 杂项）。本机实测若固定开销再降
0.1/0.15/0.2 s，均值分别 → 405k / 562k / 1.1M，所以下一步杠杆是去掉 Python（C++ 直写 parquet）。

本机的坑：开发机装了 pandas（参考栈），pyarrow 第一次 `pa.array()` 会懒加载 pandas（0.28 s）；`cli.py` 现在
`sys.modules["pandas"] = None`，镜像里本来没有 pandas。

## 怎么用

- 构建：`python engine/jpkernel/build.py`（Linux→libjpkernel.so，Windows MinGW→jpkernel.dll，二进制不入库）。
  Dockerfile 的 `kernel-build` stage 在 `python:3.11-slim-bookworm` 里装 g++ 编译，最终镜像 `python -c "from jpsim import kernel; assert kernel.available()"` 不过就构建失败。
- 引擎选择：`JPSIM_KERNEL=auto`（默认，库能加载就用 C++）/`cpp`（加载失败即报错）/`py`（纯 Python ABIDES 路径，作为内核的对照 oracle）。
- 回归：`tools/run_units.py ... --env JPSIM_KERNEL=cpp` + `tools/check_hashes.py`；RNG：`tools/rng_check.py`。

## 风险

- 未实现的分支（MarketOrder、Replace、无 latency_config 的线距延迟模型、`sigma_n==0`）在 71 个公开单元里不出现；
  私有/Final 单元若用到会直接报错而不是出错误结果（`kernel.py` 对未知 agent_type / stp_policy / 缺 latency_config 抛异常；
  `cli.py` 在 `JPSIM_KERNEL=auto` 下不会静默回退到 Python，因为 run_kernel 的异常会直接传出）。
- 平台 CPU 若有 AVX512，numpy 的 `np.log` 可能走 SVML：我们在 Python 里用同一个 `np.log` 算 mu，所以和参考一致。
- glibc 版本：镜像固定 bookworm；主办方参考在他们的镜像（同一 python:3.11-slim 基线）上生成。
