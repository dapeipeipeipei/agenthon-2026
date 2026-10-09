# T3 固定开销线（`t3/startup`）— 计时窗口、逐相分解、每项改动的收益

更新：2026-10-09 凌晨。分支 `t3/startup`（4 个提交：878344e → 53e6615 → 04f088e → 6a2cbea），**不合 main**。最终镜像 `ghcr.io/dapeipeipeipei/jinpei-t3@sha256:75184bfdb4cfaec86ce49acabd54cffe071a36ea29232373121085fa8b027009`（CI run 37863318582，tag `6a2cbea61280-startup` / `ci-startup`）。

配套工具：`tools/profile_units.py`（容器内逐相分解）、`tools/check_content.py`（表级一致性）、`tools/ci_run_units.py --env`、`tools/run_units.py` 的 `phases.json`，工作流 `t3-image.yml` 新增 `baseline_image`（同一 runner 上跑两份镜像做 A/B）和 `profile` 输入。

## 1. 计时窗口到底是什么（代码 + 主办方原话）

| 场景 | 窗口 | 来源 |
|---|---|---|
| **Dev 榜**（10-07 13:47 UTC 起） | `docker create` → `docker rm`：含创建、启动、运行、退出、日志收集、删除；**一次跑**；单元分 = 参考行数 ÷ 窗口；榜分 = 71 单元算术平均 | kit `README.md` §"Provisional Development"、issue #2 的 10-07 公告 |
| **Final** | Docker daemon 的 `State.StartedAt` → `State.FinishedAt`：**创建/inspect/删除不计**，但 runc 自身启动（含 GPU 设备挂载，CPU 镜像也挂）计入；每单元 5 跑，第 1 跑热身不计，取后 4 跑**中位数**；每跑 ≤300 s；`--cpus=4` 是 CFS 配额不是 cpuset，不 pin core | `README.md` §"Planned official Final timing" 第 4 条；issue #24 主办方回复（10-08） |
| 镜像拉取 | "The unit clock includes container creation and an image pull when needed" 是 1800 s 超时钟的口径；Dev 的 create→rm 窗口里只有某台 host 上**第一次**跑会在 `docker create` 里触发拉取，之后缓存。镜像越小越保险，但只影响每台 host 的第一个单元 | `SUBMISSION_CLI.md` 第 41 行 |
| Python 启动 / import / parquet flush / 退出 | 全部在窗口内（StartedAt 在 runc exec 入口进程之前，FinishedAt 在进程退出之后） | `tools/ci_run_units.py` 用同一口径计时 |
| 别队的观察 | issue #34（10-08）：另一队说 Dev 上"几乎所有单元的时间都是约 0.5 s 的固定容器开销，模拟本身 <0.1 s"，两次跑总分差 0.1% | — |

结论：**Dev 口径比 Final 多出 create/rm（主办方那边的固定时间，所有队一样）**；我们能动的只有 StartedAt 之后、exit 之前：解释器启动、import、模拟、抽表、写 parquet、退出。

## 2. 逐相分解（CI runner，`--cpus=4 --read-only --user 65534`，3 次中位数，单位秒）

相位定义（`JPSIM_PHASES=1` 打点，`tools/profile_units.py` 汇总）：`pre_exec` = StartedAt→verb 脚本 exec（第 3 轮起 verb 直接是 Python 脚本，没有 sh 打点，合并进 `py_start`）；`py_start` = exec→cli 模块顶部；`imports` = 引擎+numpy(+写表器) import；`config` = 解析场景+建代理+gc.freeze；`sim` = ABIDES 事件循环；`tables` = 抽轨迹/账本成 numpy 列；`parquet` = 写两份 parquet；`events` = sha256+events.json；`exit` = 最后打点→FinishedAt。

### 2a. 第 1 轮（878344e：os._exit、-S、stdlib .pyc、fork 前 import、去 pyarrow.compute/fs）—— run 37859276493

| 单元 | 事件 | 窗口 | ev/s | pre_exec | py_start | imports | config | sim | tables | parquet | events | exit |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| t3-s001-price-time-priority | 604 | 0.262 | 2 309 | 0.072 | 0.011 | 0.119 | 0.002 | 0.018 | 0.028 | 0.003 | 0.001 | 0.005 |
| t3-eq-deterministic-baseline | 10 669 | 0.575 | 18 562 | 0.075 | 0.011 | 0.119 | 0.003 | 0.280 | 0.069 | 0.010 | 0.001 | 0.008 |
| t3-eq001-pareto-latency-tail | 16 377 | 0.643 | 25 462 | 0.070 | 0.011 | 0.119 | 0.003 | 0.338 | 0.080 | 0.015 | 0.002 | 0.007 |
| t3-mr-cancel-replace-churn | 322 007 | 6.171 | 52 183 | 0.070 | 0.011 | 0.118 | 0.003 | 4.777 | 1.020 | 0.158 | 0.008 | 0.029 |
| t3-gb-mega-throughput | 1 168 360 | 30.699 | 38 059 | 0.073 | 0.011 | 0.119 | 0.024 | 23.702 | 6.094 | 0.562 | 0.032 | 0.083 |

（这一轮 `tables` 里还含懒加载的 pyarrow import ≈ 0.02–0.03 s。）

### 2b. 第 4 轮 = 最终镜像（6a2cbea：+ parquet_lite、无日志垫片、numpy 裁剪、shebang 直启）—— run 37863318582

| 单元 | 事件 | 窗口 | ev/s | pre_exec+py_start | imports | config | sim | tables | parquet | events | exit |
|---|---|---|---|---|---|---|---|---|---|---|---|
| t3-s001-price-time-priority | 604 | 0.202 | 2 988 | 0.080 | 0.095 | 0.002 | 0.017 | 0.002 | 0.003 | 0.001 | 0.003 |
| t3-eq-deterministic-baseline | 10 669 | 0.499 | 21 363 | 0.075 | 0.094 | 0.003 | 0.275 | 0.039 | 0.008 | 0.001 | 0.005 |
| t3-s019-latency-jitter-kendall | 12 328 | 0.470 | 26 217 | 0.077 | 0.093 | 0.003 | 0.252 | 0.032 | 0.009 | 0.001 | 0.005 |
| t3-eq-lognormal-lowsigma | 14 379 | 0.522 | 27 552 | 0.082 | 0.095 | 0.003 | 0.285 | 0.042 | 0.010 | 0.001 | 0.005 |
| t3-eq-uniform-tight | 14 704 | 0.558 | 26 340 | 0.074 | 0.095 | 0.003 | 0.325 | 0.043 | 0.010 | 0.001 | 0.006 |
| t3-eq-pareto-heavytail | 14 790 | 0.524 | 28 220 | 0.074 | 0.094 | 0.003 | 0.293 | 0.043 | 0.010 | 0.001 | 0.005 |
| t3-eq-lognormal-highmag | 14 804 | 0.537 | 27 585 | 0.081 | 0.094 | 0.003 | 0.299 | 0.043 | 0.010 | 0.001 | 0.005 |
| t3-eq-lognormal-highsigma | 14 820 | 0.539 | 27 478 | 0.078 | 0.095 | 0.003 | 0.304 | 0.043 | 0.010 | 0.001 | 0.005 |
| t3-eq-uniform-wide | 14 947 | 0.560 | 26 700 | 0.081 | 0.094 | 0.003 | 0.325 | 0.043 | 0.010 | 0.001 | 0.005 |
| t3-eq001-pareto-latency-tail | 16 377 | 0.565 | 28 975 | 0.076 | 0.093 | 0.003 | 0.334 | 0.045 | 0.010 | 0.001 | 0.006 |
| t3-mr-cancel-replace-churn | 322 007 | 5.885 | 54 721 | 0.073 | 0.095 | 0.003 | 4.640 | 0.931 | 0.109 | 0.008 | 0.027 |
| t3-gb-highfreq-40hz-60s | 500 530 | 12.806 | 39 086 | 0.079 | 0.095 | 0.009 | 9.997 | 2.303 | 0.224 | 0.017 | 0.038 |
| t3-gb-horizon-240s | 555 032 | 13.894 | 39 947 | 0.075 | 0.094 | 0.006 | 10.987 | 2.358 | 0.250 | 0.019 | 0.043 |
| t3-gb-pop-horizon-scale | 725 591 | 18.581 | 39 050 | 0.078 | 0.095 | 0.016 | 14.454 | 3.545 | 0.312 | 0.024 | 0.054 |
| t3-gb-mega-throughput | 1 168 360 | 29.880 | 39 102 | 0.075 | 0.094 | 0.023 | 22.868 | 6.064 | 0.545 | 0.038 | 0.083 |

读法：
- **固定部分现在 ≈ 0.18 s**：runc→exec+解释器启动 0.075（其中 runc 的 ≈0.065 是平台的，动不了）+ `imports` 0.094（numpy 49 ms、abides+jpsim ≈ 30 ms、json/其它 stdlib ≈ 15 ms）+ `exit` 0.003。v1 时同一段是 ≈0.52 s（含每次启动从源码编译 stdlib、pandas 风格的 pyarrow 全家桶 import、解释器终结）。
- numpy 的 import 几乎全是它自己的 Python 模块执行（`numpy.core` 子模块 ~29 ms、`numpy.lib` ~18 ms、`numpy.random` ~8 ms），`_multiarray_umath.so`+OpenBLAS 的加载只有 ~6 ms——所以"自编译无 BLAS 的 numpy"不值得做。
- 大单元里 **`tables`（Python 遍历代理日志抽列）占窗口 16–20%**（gb-mega 6.1 s / 29.9 s），`sim` 占 77%；这两块不是固定开销，但 `tables` 是纯 Python 列表循环，值得另一条线专门做（见 §7）。

## 3. 每项改动是什么、为什么安全、省多少

| # | 改动 | 省（CI 容器，每单元） | 安全性 |
|---|---|---|---|
| 1 | `os._exit(0)`：输出全部关闭刷盘后直接退出，跳过解释器终结（冻结堆的逐对象释放） | 小单元 ~10 ms；**大单元 0.3–1 s**（gb-mega 堆 ~1 GB） | 文件已 close；stdout/stderr 先 flush；异常路径不变 |
| 2 | stdlib 预编译 `.pyc`：`python:3.11-slim` 把 stdlib 的 pyc 全删了，rootfs 只读 + uid 65534 ⇒ v1 **每次启动都从源码编译** json/re/enum/typing/…；构建时 `python -v` 断言零个模块从 .py 编译 | ~0.15–0.2 s | 纯构建期 |
| 3 | `-S`（不跑 site.py/.pth），PYTHONPATH 显式带 site-packages；`PYTHONDONTWRITEBYTECODE=1` | ~10 ms | — |
| 4 | 批量单元：父进程先 `warm_imports()` 再 fork，4 个子进程继承已加载模块 | gbatch 单元 0.4–0.85 s | import 不消耗随机数；子进程照样重置计数器 |
| 5 | 不再 `Array.take`（避免 `pyarrow.compute`）、直接调 `pyarrow._parquet.ParquetWriter`（避免 `pyarrow.fs`） | ~20 ms | 71/71 字节一致（JPSIM_PARQUET=pyarrow 模式） |
| 6 | **`jpsim/parquet_lite.py`**：手写 thrift compact + Parquet v2（PLAIN 页、RLE 定义级、1 Mi 行 row group、cramjam snappy），带参考文件一模一样的 `pandas` + `ARROW:schema` 元数据；**默认路径不 import pyarrow** | 同 runner A/B **+6.3%**（47 716 → 50 737）；s001 窗口 −32 ms | 字节不同（参考是字典编码+统计信息），但 pyarrow/pandas 读回 **schema、元数据、dtype（含 Int64 可空）、值完全相同**：`check_content.py` 71/71（190 个文件），官方 developer verifier 71/71 admissible。评分读的是表不是字节；Final 的"稳定输出"是和自己的重复跑比，写表器确定性 |
| 7 | `jpsim/_nolog.py` 替换引擎 11 个模块的 `import logging`（引擎从未配置 handler，只建 logger + debug 调用） | ~8 ms | 行为零变化 |
| 8 | `_trim_numpy()`：`sys.modules` 预置空壳，跳过 numpy.ma/polynomial/fft/ctypeslib/linalg 和 numpy.random 的 Generator/PCG64/Philox/SFC64/_pickle、以及 `secrets`（只给 randbits，同样 os.urandom） | 容器内 numpy import 63 → 49 ms；A/B +1.0% 和 +1.5%（两轮合计） | RandomState/MT19937 不动；71/71 字节一致；`JPSIM_FULL_NUMPY=1` 可关 |
| 9 | verb 改成 `#!/usr/local/bin/python3 -OS` 脚本，不经 /bin/sh | ~2 ms | — |
| 10 | 手写 argv 解析（不 import argparse） | ~5 ms | `--help` 契约保留 |

试过/评估后不做：
- **snappy 字节级复刻**：cramjam/python-snappy 都是 Rust `snap` 移植，和 Arrow 捆绑的 Google snappy 在尾块上会出现同长不同字节（1.78 MB 输入 32 块里尾块 1 块不同），所以 parquet_lite 走"表级一致"而不是"字节一致"；字节级复刻还要复刻 pyarrow 的字典编码回退/分页/统计信息，不值。
- 无 BLAS 的 numpy：OpenBLAS 加载只占 ~6 ms。
- 更深的 numpy 裁剪（matrixlib/index_tricks）：`numpy.lib.shape_base` 直接 `from numpy.matrixlib.defmatrix import matrix`，断不开。
- `--network=none`、`MALLOC_ARENA_MAX`、CPU 绑核：窗口内没有网络；malloc arena 只影响多线程；主办方明确不 pin core（#24）。
- 镜像瘦身：pyarrow 仍装着（回归模式要用），镜像拉取只影响每台 host 第一个单元，没动。

## 4. 同一 runner 上的 A/B（榜分口径：71 单元算术平均）

| run | 基线镜像 | 新镜像 | 基线均值 | 新均值 | 变化 |
|---|---|---|---|---|---|
| 37859276493 | v1 `sha256:f905419e…`（主 checkout 已交 Dev 的那份） | 878344e | 30 405 | **40 698** | **+33.9%** |
| 37860692264 | 878344e | 53e6615（+parquet_lite） | 47 716 | **50 737** | **+6.3%** |
| 37861866131 | 53e6615 | 04f088e（+nolog/numpy 裁剪/shebang） | 43 011 | **43 458** | **+1.0%** |
| 37863318582 | 04f088e | 6a2cbea（+linalg/secrets 裁剪） | 43 504 | **44 140** | **+1.5%** |

runner 之间的绝对值差 ±15%（run 1 和 run 2 的同一镜像差 17%），所以只看同一 run 内的相对值。链式相乘：**v1 → 最终 ×1.46**。

按单元大小分组（run 1 v1→878344e；run 4 04f088e→6a2cbea）：

| 组 | 单元数 | v1 | 878344e | 04f088e | 6a2cbea |
|---|---|---|---|---|---|
| <20k 事件 | 10 | 13 940 | 21 525 | 23 642 | 24 550 |
| 20k–100k | 48 | 31 705 | 43 540 | 46 811 | 47 590 |
| ≥100k | 13 | 38 269 | 44 952 | 46 572 | 46 470 |

逐单元（v1 → 878344e，run 1）：小单元窗口 −0.30 s（s001 0.557 → 0.257 s）；72k 单元 −0.42 s；批量单元 −0.4 到 −0.85 s（gbatch-homog-8 39.9k → 83.3k ev/s）；最大单元 −3.1 s。

## 5. 平台分估计

平台 v1 实测 34 774（CI 同镜像 30–37k，平台 host ≈ CI runner 的中位水平）。按同 runner 的相对收益 ×1.46：**≈ 50.7k**。Dev 的 create→rm 比 Final 的 StartedAt→FinishedAt 多一段所有队都一样的固定时间，会稀释小单元的提升，所以保守 **45–50k**。榜首 200–231k 的差距在 `sim`（事件循环，77%）和 `tables`（16–20%），固定开销这条线已接近地板（平台 0.065 s + numpy 0.05 s + 引擎 import 0.03 s）。

## 6. 回归方法（每次改动都要过；CI 已固化）

1. `JPSIM_PARQUET=pyarrow` 模式 71 单元 → `tools/check_hashes.py`：**字节级**对参考（模拟本身的回归；numpy 裁剪也在这一模式下验证）。
2. 默认模式 71 单元 → `tools/check_content.py`（本机对 kit 参考文件；CI 对基线镜像输出）+ 本机 `tools/score_local.py`（官方 verifier）。
3. CI 同 runner A/B（`baseline_image` 输入）+ `profile_units.py` 分解 + 3 单元重复稳定性 + 官方 g0/g1。

本机（Windows, venv311）最后一次（6a2cbea）：pyarrow 模式 71/71 字节一致、均值 55 044；默认模式 71/71 表一致、71/71 admissible。

要换到这份镜像：`python t3-work/tools/make_descriptors.py sha256:75184bfdb4cfaec86ce49acabd54cffe071a36ea29232373121085fa8b027009`（本分支没动 `submission/`，由老板决定何时切）。

## 7. 给内核线的提示（不是固定开销，但分解表里很显眼）

- `tables`（`trace_fast.trace_arrays` / `message_arrays`）：gb-mega 6.1 s，mr-cancel-replace 0.93 s，72k 单元 ~0.08 s。纯 Python 逐条遍历 `agent.log` + 每条 `ev.get(...)`；如果内核改成在 logEvent 时直接追加到类型化列（或在 C++ 内核里直接产列），这块能基本消失。
- `exit` 在 gb-mega 是 0.083 s：内核回收 ~1 GB 地址空间，堆小了自然小。
- `parquet` 0.55 s（gb-mega）：一半是 cramjam snappy、一半是 numpy 编码；字符串列改字典编码可再省 ~0.1 s，没做。

## 附：71 单元 CI 窗口（秒），v1/878344e 来自 run 1，04f088e/6a2cbea 来自 run 4（两次 run 不同 runner）

| 单元 | 事件 | v1 | 878344e | 04f088e | 6a2cbea | 6a2cbea ev/s |
|---|---|---|---|---|---|---|
| t3-s001-price-time-priority | 604 | 0.557 | 0.257 | 0.209 | 0.195 | 3 092 |
| t3-eq-deterministic-baseline | 10 669 | 0.880 | 0.571 | 0.514 | 0.498 | 21 427 |
| t3-s019-latency-jitter-kendall | 12 328 | 0.853 | 0.541 | 0.489 | 0.466 | 26 477 |
| t3-eq-lognormal-lowsigma | 14 379 | 0.926 | 0.594 | 0.541 | 0.519 | 27 720 |
| t3-eq-uniform-tight | 14 704 | 0.945 | 0.630 | 0.571 | 0.551 | 26 680 |
| t3-eq-pareto-heavytail | 14 790 | 0.915 | 0.592 | 0.535 | 0.520 | 28 436 |
| t3-eq-lognormal-highmag | 14 804 | 0.933 | 0.598 | 0.536 | 0.530 | 27 944 |
| t3-eq-lognormal-highsigma | 14 820 | 0.941 | 0.609 | 0.557 | 0.543 | 27 269 |
| t3-eq-uniform-wide | 14 947 | 0.946 | 0.621 | 0.579 | 0.550 | 27 173 |
| t3-eq001-pareto-latency-tail | 16 377 | 0.960 | 0.636 | 0.595 | 0.559 | 29 285 |
| t3-as01-base-mix | 24 695 | 1.150 | 0.825 | 0.746 | 0.758 | 32 561 |
| t3-gbatch-dense-3 | 24 751 | 0.860 | 0.481 | 0.431 | 0.409 | 60 542 |
| t3-as05-oracle-variant | 25 443 | 1.146 | 0.825 | 0.752 | 0.736 | 34 585 |
| t3-ca-thin-book-depth | 25 746 | 1.156 | 0.819 | 0.763 | 0.749 | 34 361 |
| t3-as04-value-heavy | 26 990 | 1.187 | 0.846 | 0.789 | 0.789 | 34 190 |
| t3-gbatch-homog-4 | 32 580 | 0.946 | 0.517 | 0.458 | 0.441 | 73 872 |
| t3-gbatch-varsize | 34 639 | 1.030 | 0.557 | 0.513 | 0.467 | 74 106 |
| t3-ra04-shock-late | 34 953 | 1.301 | 0.974 | 0.887 | 0.878 | 39 830 |
| t3-ra06-shock-value-heavy | 35 298 | 1.328 | 0.997 | 0.897 | 0.895 | 39 425 |
| t3-ra02-shock-early | 35 381 | 1.329 | 0.985 | 0.918 | 0.863 | 41 020 |
| t3-ra05-shock-momentum-heavy | 35 697 | 1.323 | 1.009 | 0.907 | 0.888 | 40 212 |
| t3-st04-thin-book-mm-strain | 36 251 | 1.496 | 1.148 | 1.063 | 1.054 | 34 389 |
| t3-ra03-large-shock | 36 330 | 1.333 | 0.978 | 0.903 | 0.882 | 41 212 |
| t3-ra01-fundamental-shock-mid | 36 431 | 1.319 | 0.993 | 0.919 | 0.878 | 41 498 |
| t3-ca-high-volatility | 40 237 | 1.488 | 1.112 | 1.007 | 1.016 | 39 591 |
| t3-ca-baseline-calm | 40 257 | 1.467 | 1.119 | 1.027 | 1.030 | 39 076 |
| t3-ca-fast-mean-reversion | 40 280 | 1.440 | 1.111 | 1.021 | 1.017 | 39 624 |
| t3-ca-fat-tail-jumps | 40 283 | 1.445 | 1.130 | 1.028 | 1.033 | 39 012 |
| t3-as02-scale-up-noise | 42 231 | 1.625 | 1.285 | 1.157 | 1.164 | 36 293 |
| t3-gbatch-many-6 | 48 881 | 1.402 | 0.673 | 0.617 | 0.595 | 82 138 |
| t3-as03-mm-liquidity-heavy | 50 828 | 1.740 | 1.380 | 1.303 | 1.296 | 39 228 |
| t3-gbatch-hetero-mix | 56 712 | 1.429 | 0.805 | 0.752 | 0.744 | 76 275 |
| t3-gbatch-homog-8 | 65 163 | 1.633 | 0.782 | 0.709 | 0.686 | 95 020 |
| t3-gb-base-30agent-30s | 68 351 | 2.424 | 1.974 | 1.874 | 1.813 | 37 694 |
| t3-sf-03-vol-clustering-momentum | 71 827 | 1.965 | 1.585 | 1.460 | 1.453 | 49 435 |
| t3-sf-01-baseline-regime | 71 932 | 1.995 | 1.572 | 1.469 | 1.437 | 50 071 |
| t3-sf-07-coarse-agent-batch | 71 935 | 1.982 | 1.548 | 1.456 | 1.428 | 50 370 |
| t3-sf-06-high-fundamental-vol | 71 960 | 1.950 | 1.547 | 1.464 | 1.451 | 49 608 |
| t3-sf-02-heavy-jump-tails | 71 966 | 1.973 | 1.566 | 1.480 | 1.455 | 49 448 |
| t3-momentum-mix-priority | 71 980 | 2.170 | 1.751 | 1.679 | 1.643 | 43 817 |
| t3-st05-latency-spike-pareto | 71 980 | 1.960 | 1.566 | 1.472 | 1.462 | 49 227 |
| t3-fastlob-tight-book | 72 003 | 2.166 | 1.681 | 1.591 | 1.554 | 46 323 |
| t3-coarse-tick-ties | 72 004 | 2.247 | 1.751 | 1.661 | 1.645 | 43 775 |
| t3-st03-volatility-burst | 72 020 | 2.039 | 1.561 | 1.488 | 1.475 | 48 827 |
| t3-st01-liquidity-churn-baseline | 72 034 | 2.035 | 1.571 | 1.484 | 1.462 | 49 283 |
| t3-mp07-heavy-flow-oldest | 72 036 | 1.965 | 1.550 | 1.481 | 1.536 | 46 893 |
| t3-mp01-stp-newest-baseline | 72 044 | 1.993 | 1.556 | 1.444 | 1.462 | 49 262 |
| t3-stp-cancel-newest | 72 048 | 2.182 | 1.743 | 1.657 | 1.629 | 44 219 |
| t3-mp02-stp-oldest-baseline | 72 049 | 1.985 | 1.551 | 1.466 | 1.448 | 49 771 |
| t3-partialfill-atomicity | 72 052 | 2.151 | 1.729 | 1.656 | 1.644 | 43 829 |
| t3-fastlob-core | 72 061 | 2.198 | 1.754 | 1.667 | 1.645 | 43 801 |
| t3-st02-informed-entry-burst | 72 075 | 2.038 | 1.609 | 1.497 | 1.510 | 47 735 |
| t3-sf-04-thin-book-depth | 72 093 | 2.212 | 1.694 | 1.616 | 1.597 | 45 144 |
| t3-multilevel-crossing | 72 250 | 2.096 | 1.647 | 1.552 | 1.510 | 47 843 |
| t3-as06-throughput-fast | 74 502 | 2.397 | 1.963 | 1.808 | 1.826 | 40 794 |
| t3-mp06-tight-spread-selfcross | 80 929 | 2.160 | 1.714 | 1.644 | 1.653 | 48 953 |
| t3-mr-long-horizon-replay | 81 221 | 2.208 | 1.749 | 1.660 | 1.631 | 49 790 |
| t3-sf-05-deep-book-liquidity | 82 591 | 2.195 | 1.748 | 1.652 | 1.640 | 50 367 |
| t3-mp04-mm-heavy-oldest | 120 915 | 3.031 | 2.474 | 2.383 | 2.359 | 51 260 |
| t3-mp03-mm-heavy-newest | 120 920 | 3.038 | 2.484 | 2.362 | 2.324 | 52 026 |
| t3-mr-checkpoint-cadence-fine | 161 055 | 3.902 | 3.211 | 3.126 | 3.116 | 51 681 |
| t3-cancelmodify-lifecycle | 161 529 | 4.101 | 3.380 | 3.251 | 3.254 | 49 637 |
| t3-s012-partial-fill-cancel-race | 202 291 | 4.788 | 3.943 | 3.859 | 3.794 | 53 319 |
| t3-mp05-cancel-churn-newest | 240 829 | 5.349 | 4.545 | 4.312 | 4.327 | 55 658 |
| t3-gb-pop-128-agents | 303 603 | 9.503 | 8.314 | 8.045 | 8.556 | 35 483 |
| t3-mr-deep-book-state-size | 319 488 | 8.463 | 7.229 | 7.012 | 6.975 | 45 803 |
| t3-mr-cancel-replace-churn | 322 007 | 7.275 | 6.116 | 5.963 | 6.037 | 53 343 |
| t3-gb-highfreq-40hz-60s | 500 530 | 14.923 | 13.465 | 12.871 | 12.867 | 38 901 |
| t3-gb-horizon-240s | 555 032 | 16.147 | 14.383 | 13.871 | 13.768 | 40 314 |
| t3-gb-pop-horizon-scale | 725 591 | 21.383 | 19.110 | 18.562 | 19.086 | 38 017 |
| t3-gb-mega-throughput | 1 168 360 | 34.291 | 31.195 | 30.168 | 30.215 | 38 668 |

均值：run 1 v1 30 405 / 878344e 40 698；run 4 04f088e 43 504 / 6a2cbea 44 140。
