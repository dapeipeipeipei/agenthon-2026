# T3 固定开销线（`t3/startup`）— 计时窗口、逐相分解、每项改动的收益

更新：2026-10-09 凌晨。分支 `t3/startup`，不合 main。配套工具：`tools/profile_units.py`（容器内逐相分解）、`tools/check_content.py`（表级一致性）、`tools/ci_run_units.py --env`、工作流 `t3-image.yml` 的 `baseline_image` / `profile` 输入（同一 runner 上 A/B）。

## 1. 计时窗口到底是什么（代码 + 主办方原话）

| 场景 | 窗口 | 来源 |
|---|---|---|
| **Dev 榜**（10-07 13:47 UTC 起） | `docker create` → `docker rm`：含创建、启动、运行、退出、日志收集、删除；**一次跑**；单元分 = 参考行数 ÷ 窗口；榜分 = 71 单元算术平均 | kit `README.md` §"Provisional Development"、issue #2 的 10-07 公告 |
| **Final** | Docker daemon 的 `State.StartedAt` → `State.FinishedAt`：**创建/inspect/删除不计**，但 runc 自身启动（含 GPU 设备挂载，CPU 镜像也挂）计入；每单元 5 跑，第 1 跑热身不计，取后 4 跑**中位数**；每跑 ≤300 s；`--cpus=4` 是 CFS 配额不是 cpuset | `README.md` §"Planned official Final timing" 第 4 条；issue #24 主办方回复（10-08） |
| 镜像拉取 | "The unit clock includes container creation and an image pull when needed"——这是 1800 s 超时钟的口径；Dev 的 create→rm 窗口里 **第一次** 在某台 host 上跑时 `docker create` 会触发拉取（之后缓存）。镜像越小越保险，但只影响每台 host 的第一个单元 | `SUBMISSION_CLI.md` 第 41 行 |
| Python 启动 / import / parquet flush | 全部在窗口内（StartedAt 在 runc exec 入口进程之前打点，FinishedAt 在进程退出之后） | `tools/ci_run_units.py` 用同一口径计时 |
| 别队的观察 | issue #34（10-08）：另一队说 Dev 上"几乎所有单元的时间都是约 0.5 s 的固定容器开销，模拟本身 <0.1 s"，两次跑总分差 0.1% | — |

结论：**Dev 口径比 Final 多出 create/rm（主办方那边约 0.3 s 量级，所有队一样）**；我们能动的只有 StartedAt 之后、exit 之前的部分：解释器启动、import、模拟、抽表、写 parquet、退出。

## 2. 逐相分解（CI runner，`--cpus=4 --read-only --user 65534`，3 次中位数，单位秒）

相位定义（`JPSIM_PHASES=1` 打点，`tools/profile_units.py` 汇总）：`pre_exec` = StartedAt→verb 脚本 exec；`py_start` = exec→cli 模块顶部；`imports` = 引擎+numpy(+写表器) import；`config` = 解析场景+建代理+gc.freeze；`sim` = ABIDES 事件循环；`tables` = 抽轨迹/账本成 numpy 列；`parquet` = 写两份 parquet；`events` = sha256+events.json；`exit` = 最后打点→FinishedAt。

### 2a. 第 1 轮（commit 878344e：os._exit、-S、stdlib .pyc、fork 前 import、去 pyarrow.compute/fs）—— run 37859276493

| 单元 | 事件 | 窗口 | ev/s | pre_exec | py_start | imports | config | sim | tables | parquet | events | exit |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| t3-s001-price-time-priority | 604 | 0.262 | 2 309 | 0.072 | 0.011 | 0.119 | 0.002 | 0.018 | 0.028 | 0.003 | 0.001 | 0.005 |
| t3-eq-deterministic-baseline | 10 669 | 0.575 | 18 562 | 0.075 | 0.011 | 0.119 | 0.003 | 0.280 | 0.069 | 0.010 | 0.001 | 0.008 |
| t3-eq001-pareto-latency-tail | 16 377 | 0.643 | 25 462 | 0.070 | 0.011 | 0.119 | 0.003 | 0.338 | 0.080 | 0.015 | 0.002 | 0.007 |
| t3-mr-cancel-replace-churn | 322 007 | 6.171 | 52 183 | 0.070 | 0.011 | 0.118 | 0.003 | 4.777 | 1.020 | 0.158 | 0.008 | 0.029 |
| t3-gb-mega-throughput | 1 168 360 | 30.699 | 38 059 | 0.073 | 0.011 | 0.119 | 0.024 | 23.702 | 6.094 | 0.562 | 0.032 | 0.083 |

（这一轮 `tables` 里还含懒加载的 pyarrow import ≈ 0.02–0.03 s。）

### 2b. 第 2 轮（commit 53e6615：+ parquet_lite，不再 import pyarrow）—— run 37860692264（runner 更快，只看相位结构）

| 单元 | 事件 | 窗口 | ev/s | pre_exec | py_start | imports | config | sim | tables | parquet | events | exit |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| t3-s001-price-time-priority | 604 | 0.206 | 2 933 | 0.065 | 0.011 | 0.104 | 0.002 | 0.016 | 0.002 | 0.003 | 0.001 | 0.003 |
| t3-eq-deterministic-baseline | 10 669 | 0.462 | 23 082 | 0.064 | 0.010 | 0.102 | 0.003 | 0.234 | 0.035 | 0.008 | 0.001 | 0.003 |
| t3-s019-latency-jitter-kendall | 12 328 | 0.430 | 28 645 | 0.063 | 0.010 | 0.102 | 0.002 | 0.209 | 0.031 | 0.008 | 0.001 | 0.003 |
| t3-eq-lognormal-lowsigma | 14 379 | 0.466 | 30 841 | 0.063 | 0.011 | 0.102 | 0.003 | 0.236 | 0.036 | 0.009 | 0.001 | 0.004 |
| t3-eq-uniform-tight | 14 704 | 0.498 | 29 552 | 0.066 | 0.010 | 0.102 | 0.003 | 0.263 | 0.037 | 0.010 | 0.001 | 0.003 |
| t3-eq-pareto-heavytail | 14 790 | 0.474 | 31 224 | 0.065 | 0.010 | 0.102 | 0.003 | 0.243 | 0.037 | 0.009 | 0.001 | 0.003 |
| t3-eq-lognormal-highmag | 14 804 | 0.477 | 31 056 | 0.065 | 0.010 | 0.103 | 0.003 | 0.244 | 0.037 | 0.009 | 0.001 | 0.004 |
| t3-eq-lognormal-highsigma | 14 820 | 0.483 | 30 682 | 0.064 | 0.010 | 0.102 | 0.003 | 0.252 | 0.037 | 0.009 | 0.001 | 0.004 |
| t3-eq-uniform-wide | 14 947 | 0.490 | 30 521 | 0.063 | 0.011 | 0.101 | 0.003 | 0.261 | 0.037 | 0.009 | 0.001 | 0.004 |
| t3-eq001-pareto-latency-tail | 16 377 | 0.505 | 32 426 | 0.061 | 0.010 | 0.101 | 0.003 | 0.274 | 0.042 | 0.010 | 0.001 | 0.004 |
| t3-mr-cancel-replace-churn | 322 007 | 5.097 | 63 178 | 0.066 | 0.010 | 0.101 | 0.002 | 4.031 | 0.762 | 0.103 | 0.009 | 0.014 |
| t3-gb-highfreq-40hz-60s | 500 530 | 10.551 | 47 440 | 0.065 | 0.010 | 0.102 | 0.007 | 8.341 | 1.767 | 0.215 | 0.019 | 0.022 |
| t3-gb-horizon-240s | 555 032 | 11.558 | 48 021 | 0.063 | 0.010 | 0.102 | 0.005 | 9.208 | 1.878 | 0.236 | 0.021 | 0.024 |
| t3-gb-pop-horizon-scale | 725 591 | 15.088 | 48 090 | 0.063 | 0.010 | 0.101 | 0.014 | 11.968 | 2.575 | 0.299 | 0.027 | 0.032 |
| t3-gb-mega-throughput | 1 168 360 | 24.212 | 48 255 | 0.061 | 0.010 | 0.100 | 0.019 | 19.284 | 4.183 | 0.472 | 0.042 | 0.047 |

读法：
- **固定部分 ≈ 0.19 s**：`pre_exec` 0.065（runc 到 exec，平台的，动不了）+ `py_start` 0.011 + `imports` 0.10（numpy ≈ 0.07，abides/jpsim ≈ 0.03）+ `exit` 0.003。
- `imports` 的 numpy 部分几乎全是 numpy 自己的 Python 模块执行（`numpy.core` 子模块 32 ms、`numpy.lib` 19 ms、`numpy.random` 8 ms），`_multiarray_umath.so`+OpenBLAS 的加载只有 ~6 ms——所以"自编译无 BLAS 的 numpy"不值得做。
- 大单元里 **`tables`（Python 遍历代理日志抽列）占窗口 15–17%**（gb-mega 4.2 s / 24.2 s），`sim` 占 80%；这两块是内核线的事，但 `tables` 是纯 Python 列表循环，值得另一条线专门做。

### 2c. 第 3 轮（commit 04f088e：+ 无日志垫片、numpy 裁剪、shebang 直启）—— run 37861866131

（见 §4 的 A/B 表；分解表在 run 的 `profile.txt`。）

## 3. 每项改动是什么、为什么安全、省多少

| # | 改动 | 省（CI 容器，每单元） | 安全性 |
|---|---|---|---|
| 1 | `os._exit(0)`：输出全部关闭刷盘后直接退出，跳过解释器终结（冻结堆的逐对象释放） | 小单元 ~10 ms；**大单元 0.3–1 s**（gb-mega 堆 ~1 GB） | 文件已 close；stdout/stderr 先 flush |
| 2 | stdlib 预编译 `.pyc`（`python:3.11-slim` 把 stdlib 的 pyc 全删了，rootfs 只读 + uid 65534 导致**每次启动都从源码编译** json/re/enum/typing/…）；构建时 `python -v` 断言零个模块从 .py 编译 | ~0.15–0.2 s | 纯构建期 |
| 3 | `-S`（不跑 site.py/.pth），PYTHONPATH 显式带 site-packages；`PYTHONDONTWRITEBYTECODE=1` | ~10 ms | — |
| 4 | 批量单元：父进程先 `warm_imports()` 再 fork，4 个子进程继承已加载模块 | gbatch 单元 0.4–0.85 s | import 不消耗随机数；子进程照样重置计数器 |
| 5 | 不再 `Array.take`（避免 `pyarrow.compute`）、直接调 `pyarrow._parquet.ParquetWriter`（避免 `pyarrow.fs`） | ~20 ms | 71/71 字节一致（JPSIM_PARQUET=pyarrow 模式） |
| 6 | **`jpsim/parquet_lite.py`**：手写 thrift compact + Parquet v2（PLAIN 页、RLE 定义级、1 Mi 行 row group、cramjam snappy），带参考文件一模一样的 `pandas` + `ARROW:schema` 元数据；**不 import pyarrow** | 同 runner A/B **+6.3%**（47 716 → 50 737）；s001 窗口 −32 ms | 字节不同（参考是字典编码+统计信息），但 pyarrow/pandas 读回 **schema、元数据、dtype（含 Int64 可空）、值完全相同**：`check_content.py` 71/71（190 个文件），官方 developer verifier 71/71 admissible。评分读的是表不是字节；Final 的"稳定输出"是和自己重复跑比，写表器确定性 |
| 7 | `jpsim/_nolog.py` 替换引擎 11 个模块的 `import logging`（引擎从未配置 handler，只建 logger + debug 调用） | ~8 ms | 行为零变化 |
| 8 | `_trim_numpy()`：`sys.modules` 预置空壳，跳过 numpy.ma/polynomial/fft/ctypeslib/linalg 和 numpy.random 的 Generator/PCG64/Philox/SFC64/_pickle、以及 `secrets`（只给 randbits，同样 os.urandom） | 本机 numpy import 76 → 50 ms（CI 约 −20 ms） | RandomState/MT19937 不动；71/71 字节一致；`JPSIM_FULL_NUMPY=1` 可关 |
| 9 | verb 改成 `#!/usr/local/bin/python3 -OS` 脚本，不经 /bin/sh | ~2 ms | — |
| 10 | 手写 argv 解析（不 import argparse） | ~5 ms | `--help` 契约保留 |

试过/评估后不做：
- **snappy 字节级复刻**：cramjam/python-snappy 都是 Rust `snap` 移植，和 Arrow 捆绑的 Google snappy 在尾块上会出现同长不同字节（32 块里 1 块），所以 parquet_lite 走"表级一致"而不是"字节一致"。
- 无 BLAS 的 numpy：见 §2b，OpenBLAS 加载只占 ~6 ms。
- 更深的 numpy 裁剪（matrixlib/index_tricks）：`numpy.lib.shape_base` 直接 `from numpy.matrixlib.defmatrix import matrix`，断不开。
- `--network=none`、`MALLOC_ARENA_MAX`、CPU 绑核：窗口内没有网络；malloc arena 只影响多线程；主办方明确不 pin core。

## 4. 同一 runner 上的 A/B（榜分口径：71 单元算术平均）

| run | 基线镜像 | 新镜像 | 基线均值 | 新均值 | 变化 |
|---|---|---|---|---|---|
| 37859276493 | v1 `sha256:f905419e…`（主 checkout 已交 Dev 的那份） | commit 878344e | 30 405 | **40 698** | **+33.9%** |
| 37860692264 | commit 878344e | commit 53e6615（+parquet_lite） | 47 716 | **50 737** | **+6.3%** |
| 37861866131 | commit 53e6615 | commit 04f088e（+nolog/numpy 裁剪/shebang） | （待填） | （待填） | |

runner 之间的绝对值差 ±15%（run 1 和 run 2 的同一镜像差 17%），所以只看同一 run 内的相对值。链式相乘：v1 → 现在约 **×1.42**（+42%）。

逐单元（run 1，v1 → commit 1）：小单元窗口 −0.30 s（s001 0.557 → 0.257 s，1 084 → 2 350 ev/s）；72k 单元 −0.42 s（36.2k → 46.3k ev/s）；批量单元 −0.4 到 −0.85 s（gbatch-homog-8 39.9k → 83.3k）；最大单元 −3.1 s（34.1k → 37.5k）。

## 5. 平台分估计

平台 v1 实测 34 774（CI 同镜像 30–37k，所以平台 host ≈ CI runner 的中位水平）。按同 runner 的相对收益 ×1.42 估：**约 49–50k**。再考虑 Dev 的 create→rm 比 Final 的 StartedAt→FinishedAt 多一段所有队都一样的固定时间（按 #34 的描述约 0.3 s），小单元的提升在平台上会被稀释一些，保守 **45–50k**；榜首 200–231k 的差距主要在 `sim`（事件循环）和 `tables`，不在固定开销里了。

## 6. 回归方法（每次改动都要过）

1. `JPSIM_PARQUET=pyarrow` 模式 71 单元 → `tools/check_hashes.py`：**字节级**对参考（模拟本身的回归）。
2. 默认模式 71 单元 → `tools/check_content.py`（对 kit 参考文件或基线镜像输出）+ `tools/score_local.py`（官方 verifier）。
3. CI 同 runner A/B（`baseline_image` 输入）+ `profile_units.py` 分解。

本机（Windows, venv311）最后一次：pyarrow 模式 71/71 字节一致、均值 55 804；默认模式 71/71 表一致、71/71 admissible、均值 70 439（Windows 上 pyarrow import 特别贵，所以本机数字夸大了 lite 的收益）。
