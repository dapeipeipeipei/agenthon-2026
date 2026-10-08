# jpkernel / jpsim-native — C++ 内核线（分支 t3/kernel-cpp）

更新：2026-10-09 凌晨。

## 一句话

把 ABIDES 参考引擎里赛道用到的全部东西（事件循环、交易所撮合、四种代理、稀疏均值回复 oracle、延迟模型、
两份输出的行构造）用 C++ 重写成 `engine/jpkernel/jpkernel.cpp`，再加一个**运行时完全不用 Python** 的
`jpsim-native`（`engine/jpkernel/jpsim_native.cpp`：自带 JSON 解析 + 内核 + 直接调 pyarrow 15.0.2 wheel
里的 libarrow/libparquet 写 parquet + SHA-256 + events.json + fork 批量）。**71/71 公开单元 trace.parquet 和
message_trace.parquet 与主办方参考逐字节相同**（本机、CI 裸跑、CI 镜像内三处都验过），镜像内榜分口径均值
从 jpsim v1 的 36.7k 提到 **__CI_MEAN__ ev/s**（同一 CI 跑法下 13×）。

## 三层交付（都在镜像里）

| 层 | 入口 | 用途 |
|---|---|---|
| `jpsim-native` | `simulate` / `simulate-batch`（提交用的两个 verb） | 零 Python：进程 9 ms 起步；parquet 由 wheel 自带的 libparquet 写，字节和 pyarrow 一样 |
| `libjpkernel.so` + `jpsim/kernel.py` | `simulate-py`（`JPSIM_KERNEL=auto/cpp`） | 同一内核的 ctypes 绑定，Python 解析 + pyarrow 写；本机 Windows 用 MinGW 编 dll 跑回归 |
| 纯 Python ABIDES | `simulate-py`（`JPSIM_KERNEL=py`） | 上一轮的 jpsim v1，作为内核的对照 oracle |

## 覆盖范围（71 个单元 95 个场景全部清点过）

| 维度 | 单元里出现的 | 内核实现 |
|---|---|---|
| 代理 | NoiseTrader / MarketMaker / ValueTrader / MomentumTrader（kit adapter 的四种）+ ExchangeAgent | 全部 |
| 消息 | Wakeup、MarketClosePriceRequest/Msg、MarketHoursRequest/Msg、QuerySpread/Response、LimitOrder、CancelOrder、OrderAccepted/Executed/Cancelled、MarketClosed | 全部（MarketOrder/Replace/Modify/PartialCancel/MessageBatch 单元不用，未实现） |
| 延迟 | log_normal 88 / pareto 3 / uniform 3 / deterministic 1 | 全部 |
| oracle | SparseMeanRevertingOracle；jump_intensity>0 的 14 个场景有 Poisson megashock；6 个有 scheduled_jump | 全部，含 megashock 后 oracle 时间戳变成 Python float 的语义（int/float 精确比较、int+float 提升） |
| 交易所 | protocol_enforcement 的 7 个单元（cancel_newest 4 / cancel_oldest 3），ack/compute delay 都是 0 | 全部 |
| RNG | 全局 MT19937（seed、每个 RandomState 的种子抽取、megashock 指数分布）+ 各 RandomState 的 normal / lognormal / uniform / exponential / pareto / randint | numpy legacy 算法逐位复现：init_genrand 播种、polar Box-Muller 带缓存第二值、`-log(1-u)` 指数、掩码拒绝 randint；`tools/rng_check.py` 5 个种子 × 14 种生成器各 30 万次逐位核对 0 失败 |

Python 语义细节：`round()` 半偶、`int(x)` 截断、`max(0, v)`、`(bid+ask)//2`、堆序 `(time, sender, recipient, message_id)`、
消息 id/订单 id 的全局计数器（WakeupMsg 也占 id）、`stop_time` 之后仍处理一条事件（参考循环先检查再弹出）、
QuerySpreadResponse 的 `mkt_closed`、交易所第一条消息前 computation delay 仍是 50。

libm：log/exp/pow 走参考 numpy/CPython 用的同一个 C 运行库（Linux=镜像的 glibc 2.36，内核在同一镜像的
build stage 里编译；Windows 本机=ucrtbase.dll 动态解析，避免 MinGW 自带 libm 末位差异）。
编译 `-O2 -ffp-contract=off -fno-fast-math`，不用 `-march=native`（禁 FMA 融合）。

**一个真实的 1-ulp 坑**：延迟模型的 `mu = float(np.log(mean_ns))`。在 AVX-512 的 GitHub runner 上
numpy 走 SVML，`np.log(5000.0)` = 8.517193191416236，glibc `log(5000.0)` = …238；在本机（无 AVX-512）两者相同。
两种 mu 都复现了全部参考（涉及 15 个 mean_ns=5000 的单元）：lognormal 延迟最后 `int(round(·))`，1 ulp 的 mu
翻不动任何整数。jpsim-native 用 glibc；t3-native-dev CI 里有一步打印这个差异（只报告不失败）。

## 数字

本机 i7-13700K，一进程一单元（Python 包装内核，整进程墙钟含 python+numpy+pyarrow 启动 ≈0.2 s）：

| | 参考引擎 | jpsim v1（纯 Python 瘦身） | jpkernel（Python 包装） |
|---|---|---|---|
| 71 单元 ev/s 算术平均 | 13 988 | 50 659 | **302 429** |
| 中位数 | 14 004 | 55 749 | 306 802 |
| 最小（s001，604 事件） | 968 | 1 573 | 3 835 |
| 最大 | 18 454 | 81 183 | 932 422 |

CI（GitHub ubuntu-latest，runner 之间 ±15%）：

| 跑法 | 71 单元均值 | s001 / mp05 / gb-mega 墙钟 |
|---|---|---|
| jpsim v1 镜像（容器 StartedAt→FinishedAt） | 36 739 | — |
| jpkernel Python 包装镜像 `sha256:db41a196…`（容器窗口） | 142 461 | 0.44 / 0.71 / 1.91 s |
| jpsim-native 裸跑（无容器，一进程一单元） | 901 620 – 1 001 480 | 0.011 / 0.19 / 0.91 s |
| jpsim-native 镜像 `sha256:3d8a8f16…`（容器窗口） | 481 345 | 0.07 / 0.29 / 1.34 s |
| jpsim-native 镜像 `__FINAL_DIGEST_SHORT__`（容器窗口，双线程写 + 消息槽池） | __CI_MEAN__ | __FINAL_TRIPLE__ |

内核本身（本机）：gb-mega 1.17M 事件 / 1.42M 消息 0.32 s；mp05 0.05 s。gb-mega 整进程里剩下的是
parquet 写（33 MB message_trace + 12 MB trace，libparquet 单线程约 0.5 s，现在两个文件两线程并行）。

## 怎么用

- 构建：`python engine/jpkernel/build.py`（内核 .so/.dll）；`python engine/jpkernel/build_native.py`（Linux，
  需要 pip 里有 pyarrow==15.0.2，用 `pa.get_include()`/`get_library_dirs()`，rpath 指向 wheel 目录）。二进制不入库。
- Dockerfile 的 `kernel-build` stage 在 `python:3.11-slim-bookworm` 里装 g++ + numpy + pyarrow 编两样；最终镜像检查
  `ldd jpsim-native` 链到 site-packages/pyarrow 的 libparquet 并能 `--help`，否则构建失败。
- CI：`.github/workflows/t3-native-dev.yml`（裸跑 71 单元 + 哈希，3 分钟）；`t3-image.yml --ref t3/kernel-cpp -f tag_suffix=-native`
  （镜像内 71 单元 + 哈希 + 重复稳定 + g0/g1 + 推 GHCR）。
- 本机回归：`tools/run_units.py --python <venv311> --module jpsim --pythonpath engine --env JPSIM_KERNEL=cpp` + `tools/check_hashes.py`；
  RNG：`tools/rng_check.py`。native 二进制在 Windows 不能链接（MSVC 构建的 arrow.dll），只能在 CI 验。
- 提交描述：`tools/make_descriptors.py sha256:<digest> --suffix cpp` → `submission/submission.cpp.json` / `.cpp.final.json`；
  打包 `pack_all.py t3-cpp`（dev）/ `t3-cpp.final`。

## 风险

- 未实现的分支（MarketOrder、Replace、无 latency_config 的线距延迟模型、`sigma_n==0`）在 71 个公开单元里不出现；
  私有单元若用到：jpsim-native 对未知 agent_type / stp_policy / 缺 latency_config 直接报错退出（不会出错误结果）。
  保险：镜像里保留 `simulate-py`（JPSIM_KERNEL=py 走纯 Python ABIDES），但提交 verb 不会自动回退。
- parquet 字节依赖 wheel 里的 libparquet 15.0.2（和参考同版本同构建）；镜像固定 bookworm + 该 wheel。
- `np.log` vs glibc 的 1 ulp（见上）：对公开单元无影响；理论上某个私有单元的某条延迟恰好卡在 .5 边界才会出现差异，概率可忽略，
  而且主办方自己换 CPU 重跑也会遇到同样的事。
- 容器启动开销在主办方机器上未知（CI 上约 0.06 s），小单元分数被它压住，对所有队一样。
