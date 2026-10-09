# T3 第三轮：原生二进制的"每单元固定开销"（分支 `t3/native-lean`）

更新：2026-10-09。接 KERNEL_NOTES（C++ 内核 + `jpsim-native`）之后的一轮：目标是把我们自己在主办方机器上
每个单元的固定时间压到地板，同时把平台的固定开销 c_p 和我们的那一份分开估出来。**不合 main**。

最终镜像：见 §7（digest、描述文件 `submission/submission.lean.json` / `.lean.final.json`，打包 `pack_all.py t3-lean` / `t3-lean.final`）。

## 1. 先把平台的开销模型拟出来（三个真实平台分 + 同一套 CI 计时）

模型：平台上一个单元的窗口 `T_plat,i = c_p + k · T_ci,i`，`T_ci,i` 是我们 CI（GitHub runner，平台容器参数，
daemon `StartedAt→FinishedAt`）量到的同一镜像的窗口；榜分 = `mean_i(N_i / T_plat,i)`。两个未知数 c_p、k，
三个方程（三份上过 Dev 榜的镜像各自的 71 单元 CI 窗口 + 平台实测分），最小二乘（对数误差）：

| 镜像 | CI 均值 ev/s | CI s001 窗口 | CI 中位窗口 | 平台实测 | 模型 | 偏差 |
|---|---|---|---|---|---|---|
| v1 Python `f905419e` | 31 956 | 0.532 s | 1.862 s | **34 774** | 33 675 | −3.2% |
| v2 Cython `87c3d796` | 147 703 | 0.540 s | 0.497 s | **87 209** | 94 182 | +8.0% |
| native (Arrow) `e05df48c` | 468 828 | 0.085 s | 0.139 s | **134 487** | 129 014 | −4.1% |

**拟合结果：c_p ≈ 0.63 s，k ≈ 0.63**（对数均方误差 5%；任两点拟合 c_p 在 0.52–0.80、k 在 0.53–0.90 之间）。
读法：
- 主办方 Dev 口径（create→rm）每个容器约 **0.63 s 是平台自己的**：比我们 CI 上的 Docker 侧（§4 实测 0.093 s）大得多，
  和 issue #34 另一支队伍说的"每单元约 0.5 s 固定"一致。
- k≈0.63：平台 host 跑我们的进程比 GitHub runner 快约 1.6×。
- **三张镜像落在同一条线上，说明 native 没有平台专属的额外固定成本**：它在 CI 上 s001 的窗口 0.085 s，减去 runc 的
  ≈0.065 s，我们自己的固定部分只有 **≈0.02 s**（平台上 ≈0.013 s）。模型里把它全删掉：129k → 131k（+1.5%）。
- 纯固定开销天花板 `mean(N_i / c_p)` = **173k**。榜首 231k 要 c ≤ 0.47 s 且模拟≈0：他们的 host 批次/时段的 c 比我们那次小，
  不是我们代码能动的（平台 host 之间像 CI runner 之间一样有 ±15% 的差）。
- 按单元大小分组看平台分的来源（native 镜像，模型）：13 个 ≥100k 事件的单元贡献 73k / 129k，48 个中等单元 53k，10 个小单元 2.6k。
  **真正能动的是大单元的变量时间（模拟 + 写表），不是小单元的固定时间。**

结论：这一轮"固定开销"能挤的只有 ≈0.02 s/单元（+1–2%）；更大的收益来自 Arrow 写表器本身的变量开销（§3）。

## 2. 我们的固定时间去哪了（CI 容器外裸跑，`tools/bench_native.py`，s001 = 604 事件）

| 二进制 | 进程墙钟（15 次中位） | 系统调用 | RSS 峰值 | 缺页 | 动态加载器 |
|---|---|---|---|---|---|
| native (Arrow，动态链接 libarrow/libparquet 60 MB .so) | **8.8 ms** | 400（60 mmap、33 openat、18 futex） | 31.5 MB | 1 011 | 1.45 M cycles（`LD_DEBUG=statistics`） |
| lean 动态链接（只有 libc/libstdc++/libm） | 2.1 ms | 119 | 3.7 MB | 243 | 0.20 M cycles（重定位 53%） |
| **lean 静态链接**（最终） | **1.4 ms** | **72**（2 mmap、4 openat、0 lseek） | **1.4 MB** | **156** | 无 |

Arrow 版的 7.4 ms 固定时间 = 加载/重定位两个大 .so + libarrow 的全局构造函数 + 它自己起的线程池（futex）+ 映射 31 MB 的缺页。
进程级其它项（fork/exec 辅助进程、目录扫描、fsync）本来就没有：`simulate` verb 原先是 `/bin/sh` 脚本再 exec 二进制，
现在二进制本身就是 `/usr/local/bin/simulate`（按程序名取 verb），少一次 exec + dash 启动。

本机（i7-13700K，`JPSIM_PHASES=1`）lean 二进制逐相，秒：

| 单元 | 事件 | 解析 | 内核 run | 建表 tables | 编码 encode | 写+哈希 | 合计 |
|---|---|---|---|---|---|---|---|
| s001 | 604 | 0.0002 | 0.0002 | 0.0011 | 0.0004 | 0.0004 | 0.0023 |
| eq-deterministic | 10 669 | 0.0003 | 0.0034 | 0.0018 | 0.0014 | 0.0007 | 0.0076 |
| mp01 | 72 044 | 0.0003 | 0.0108 | 0.0039 | 0.0046 | 0.0018 | 0.021 |
| mp05 | 240 829 | 0.0002 | 0.0346 | 0.0147 | 0.0141 | 0.0051 | 0.069 |
| gb-mega | 1 168 360 | 0.0002 | 0.2466 | 0.118 → 0.093（基数排序后） | 0.079（4 线程） | 0.037（SHA-NI） | 0.48 → 0.46 |

CI runner 大约慢 1.5×。小单元 ~1 ms 的 `tables` 是起线程；中等单元里内核占一半、写表器三块各占 1/6。

## 3. 做了什么（每一项都过 71/71 表级一致 + 官方 verifier + 合成场景）

| # | 改动 | 省（CI 裸跑，每单元） |
|---|---|---|
| 1 | **自写 Parquet 写表器 `engine/jpkernel/jpparquet.h`**：thrift compact 手写；int 列 PLAIN 页，字符串列字典页 + RLE_DICTIONARY 位打包索引（和参考一样的编码族）；定义级 RLE/位打包；自写 snappy 块编码器（`JPSIM_CODEC=none` 可关）；1 Mi 行一个行组；`pandas` + `ARROW:schema` 元数据原样照抄参考文件，pyarrow/pandas 读回 schema（含可空 Int64）、元数据、值完全相同。每个 (行组, 列) 是一个任务，≤4 线程并行；整个文件先在内存拼好，一次 write()，哈希直接算这块内存（原先是写完再读文件算） | s001 9 → 1.4 ms；中位单元 76 → 36 ms；gb-mega 1.09 → 0.75 s |
| 2 | **静态链接**（`-static -ffunction-sections -Wl,--gc-sections`，glibc 2.36 的 libm.a，IFUNC 的 FMA 变体在静态二进制里同样按 CPU 选，和动态 libm 一样）；无 iostream，stdio 读写 | 动态加载器 0.7 ms → 0 |
| 3 | verb 就是二进制（`/usr/local/bin/simulate`、`simulate-batch` 两份拷贝，最后一层）；不再经 `/bin/sh` | ~1 ms |
| 4 | 退出不析构：`_exit`，内核对象不 delete（gb-mega 的堆逐对象释放是纯成本） | 大单元几十 ms |
| 5 | `simulate-batch` 的 worker 数从 cgroup v2 `cpu.max` 读（`--cpus=4` 是配额，亲和掩码看到的是 host 全部核）；仍是每子场景一个 fork（fork 时父进程几乎没碰内存） | 批量单元不再 32 进程挤 4 核 |
| 6 | SHA-256 走 x86 SHA 扩展（cpuid 运行时判断，标量回退，`JPSIM_NO_SHANI=1` 强制标量；两条路径与 hashlib 核对过） | gb-mega 哈希 45 MB：~0.1 s → ~0.03 s |
| 7 | trace 的 (t, order_id) 稳定排序改成 64 位键 LSD 基数排序（t 跨度 + order_id 能装进 63 位时；否则回退 std::stable_sort） | gb-mega tables 118 → 93 ms |
| 8 | `JPSIM_PHASES=1`：stderr 一行各相位时间戳（工具用） | — |

试过/量过不做：
- 不压缩（`JPSIM_CODEC=none`）：文件大 2–3×，写出去更慢（15 单元合计 2.01 → 2.34 s）。
- `GLIBC_TUNABLES=glibc.malloc.hugetlb=1`：CI runner 的 THP 本来就是 `[always]`，零差异。
- 单线程写表：中等单元无差，gb-mega 0.68 → 0.83 s；保留 ≤4 线程（小于 2 万行时不起线程）。
- 空镜像（scratch，只有二进制）：Final 窗口和带 Python 回退的镜像完全一样（0.079 vs 0.080 s），Dev 口径的 Docker 侧也一样（§4），
  所以 Python 回退留在镜像里不花任何运行时成本。

## 4. CI 镜像内（平台容器参数，同一 runner 上和基线 `e05df48c` 对跑）

run 37882764539（第一版 lean，无 SHA-NI/基数排序）：

| | 基线 native (Arrow) | lean | 变化 |
|---|---|---|---|
| 榜分口径 Final 窗口（StartedAt→FinishedAt）71 单元均值 | 465 285 | **598 186** | **+28.6%** |
| Dev 窗口（host 上 create→rm）71 单元均值 | 303 515 | **355 703** | **+17.2%** |
| 71 单元窗口合计 Final / Dev | 12.25 / 18.96 s | 9.60 / 16.41 s | |
| 10 个最小单元：Final 均值 / Dev 均值 / Docker 侧 = Dev−Final | 0.097 / 0.191 / 0.094 s | 0.080 / 0.173 / 0.093 s | scratch 镜像 0.079 / 0.172 / 0.093 |

10 个最小 + 5 个最大单元（3 次中位，秒；Final = daemon 窗口，Dev = create→rm）：

| 单元 | 事件 | 基线 Final | lean Final | 基线 Dev | lean Dev | ev/s（Final）基线 → lean |
|---|---|---|---|---|---|---|
| s001-price-time-priority | 604 | 0.080 | 0.073 | 0.178 | 0.164 | 7 636 → 9 093 |
| eq-deterministic-baseline | 10 669 | 0.096 | 0.079 | 0.190 | 0.174 | 113 636 → 142 383 |
| s019-latency-jitter-kendall | 12 328 | 0.096 | 0.079 | 0.190 | 0.171 | 125 600 → 157 386 |
| eq-lognormal-lowsigma | 14 379 | 0.100 | 0.080 | 0.196 | 0.172 | 145 908 → 170 673 |
| eq-uniform-tight | 14 704 | 0.097 | 0.082 | 0.193 | 0.174 | 151 735 → 179 099 |
| eq-pareto-heavytail | 14 790 | 0.097 | 0.082 | 0.191 | 0.178 | 148 925 → 185 262 |
| eq-lognormal-highmag | 14 804 | 0.099 | 0.081 | 0.191 | 0.170 | 146 369 → 182 361 |
| eq-lognormal-highsigma | 14 820 | 0.108 | 0.080 | 0.199 | 0.174 | 150 902 → 179 751 |
| eq-uniform-wide | 14 947 | 0.098 | 0.081 | 0.190 | 0.172 | 154 004 → 190 111 |
| eq001-pareto-latency-tail | 16 377 | 0.101 | 0.080 | 0.196 | 0.175 | 168 370 → 205 002 |
| mr-cancel-replace-churn | 322 007 | 0.313 | 0.235 | 0.410 | 0.329 | 1 031 768 → 1 419 536 |
| gb-highfreq-40hz-60s | 500 530 | 0.500 | 0.394 | 0.595 | 0.492 | 1 018 800 → 1 281 994 |
| gb-horizon-240s | 555 032 | 0.529 | 0.422 | 0.625 | 0.519 | 1 052 370 → 1 336 633 |
| gb-pop-horizon-scale | 725 591 | 0.644 | 0.518 | 0.744 | 0.616 | 1 128 440 → 1 399 067 |
| gb-mega-throughput | 1 168 360 | 0.983 | 0.779 | 1.091 | 0.886 | 1 194 899 → 1 500 885 |

（ev/s 列取自同 run 的 71 单元单次跑；窗口列是 3 次中位。）

读法：CI 上一个容器的地板是 ≈0.066 s（runc + 挂载 + exec），s001 现在 0.066–0.073 s，**我们自己的固定部分已经量不出来**；
中等单元（48 个，2 万–10 万事件）的 Final 窗口 0.132 → 0.103 s；大单元 −20%。Docker 侧（Dev−Final）0.093 s 与镜像无关。

最终镜像（SHA-NI + 基数排序）的同 runner A/B 见 §7。

## 5. 平台分估计

把 §1 的模型套在同一 run 的基线/lean 窗口上：基线模型 128.5k（实测 134.5k），lean 模型 135.5k，比值 1.054；
用 Dev 窗口直接拟合（k 取 0.5–1.0）得 140.6k–145.6k。**估计 141k–146k（+5–8%）**。
差距的来源很清楚：平台 c_p≈0.63 s 吃掉了一切——lean 镜像在 CI 上 71 单元窗口合计只有 9.6 s，
平台上同样这 71 个单元要花 71 × 0.63 ≈ 45 s 的容器开销。模拟+写表再快一倍也只能到 ~155k；要到 230k 必须 c_p 掉到 0.47 以下，这是平台的事。

## 6. 验证方法（每次改动都跑；CI 已固化）

- `t3-native-dev.yml`（无 Docker，~8 分钟）：同一份源码编三个变体——lean 静态、lean 动态、Arrow（`build_native.py --arrow`）；
  Arrow 版 71 单元 `check_hashes.py` **逐字节**对主办方声明哈希（内核回归门）；lean 版 71 单元 `check_content.py --ref-root` 对 Arrow 版文件
  **表级一致**（schema+元数据、`Table.equals`、pandas dtypes+`DataFrame.equals`）+ `check_hashes.py --sidecar-only`（行数对参考 + sidecar 算术 + 允许的文件清单）；
  `JPSIM_CODEC=none` 再跑一遍；4 单元重复稳定（字节）；`synthetic_check.py --compare content`（9 个合成场景 + 混合批量，回退该发生时发生）；
  `bench_native.py`（启动开销 strace/time、LD_DEBUG、10 小 + 5 大单元 × 变体）。
- `t3-image.yml`：镜像内 71 单元两种窗口；基线镜像同 runner 对跑 + 以它的（逐字节=参考的）文件做表级比对；重复稳定；镜像内合成场景；官方 g0/g1；
  10 小 + 5 大 × 3 次 profile；推 GHCR。
- 本机（Windows，MinGW 静态编译，libm 走 ucrtbase）：71/71 表级一致（pyarrow 15.0.2 和 25.0.1 两个读取器）、官方 developer verifier 71/71 admissible、合成场景全绿
  （Windows 下回退用 `_spawnvp`，批量按子场景 spawn 自身）。

## 7. 最终镜像与数字

**镜像：`ghcr.io/dapeipeipeipei/jinpei-t3@sha256:7b118bff490c4c5c4c9d9627d4a34f0356f7441def144a35d91eba17e479b5d8`**
（tag `8433b97…-lean2` / `ci-lean2`，CI run 37883796088，commit 8433b97；14 层 174 MB；匿名 manifest GET 200 已验）。
描述文件 `submission/submission.lean.json`（dev）/ `submission.lean.final.json`（final）已 seal + 校验 → `pack_all.py t3-lean` / `t3-lean.final`。
第一版 lean（无 SHA-NI / 基数排序）`sha256:691363f6bba4c66395453cb37b2233d6accacfee0d7080442525fb4848c94b49`（tag `-lean`，run 37882764539）同样全绿。

同 runner A/B（run 37883796088，基线 = 现提交的 native `e05df48c`）：

| | 基线 | **lean 最终** | 变化 |
|---|---|---|---|
| Final 窗口 71 单元均值 | 480 801 | **647 950** | **+34.8%** |
| Dev 窗口（create→rm）71 单元均值 | 306 058 | **371 466** | **+21.4%** |
| 71 单元窗口合计 Final / Dev | 11.86 / 18.88 s | 8.84 / 15.94 s | |
| 48 个中等单元 Final 窗口均值 | 0.128 s | 0.099 s | |
| s001 Final 窗口（3 次中位） | 0.080 s | 0.066 s | 容器地板 |
| gb-mega Final 窗口 | 0.944 s | 0.639 s | −32% |
| 10 小 + 5 大：Final / Dev（3 次中位） | 见下 | | |

| 单元 | 事件 | 基线 Final | lean Final | 基线 Dev | lean Dev |
|---|---|---|---|---|---|
| s001-price-time-priority | 604 | 0.080 | 0.066 | 0.182 | 0.158 |
| eq-deterministic-baseline | 10 669 | 0.095 | 0.075 | 0.192 | 0.172 |
| s019-latency-jitter-kendall | 12 328 | 0.095 | 0.071 | 0.193 | 0.170 |
| eq-lognormal-lowsigma | 14 379 | 0.094 | 0.080 | 0.192 | 0.181 |
| eq-uniform-tight | 14 704 | 0.097 | 0.074 | 0.196 | 0.171 |
| eq-pareto-heavytail | 14 790 | 0.099 | 0.076 | 0.199 | 0.171 |
| eq-lognormal-highmag | 14 804 | 0.094 | 0.081 | 0.188 | 0.176 |
| eq-lognormal-highsigma | 14 820 | 0.097 | 0.077 | 0.198 | 0.174 |
| eq-uniform-wide | 14 947 | 0.097 | 0.081 | 0.197 | 0.178 |
| eq001-pareto-latency-tail | 16 377 | 0.102 | 0.078 | 0.200 | 0.176 |
| mr-cancel-replace-churn | 322 007 | 0.307 | 0.197 | 0.409 | 0.294 |
| gb-highfreq-40hz-60s | 500 530 | 0.480 | 0.327 | 0.584 | 0.428 |
| gb-horizon-240s | 555 032 | 0.510 | 0.342 | 0.613 | 0.446 |
| gb-pop-horizon-scale | 725 591 | 0.618 | 0.427 | 0.725 | 0.529 |
| gb-mega-throughput | 1 168 360 | 0.944 | 0.639 | 1.055 | 0.748 |

CI 裸跑（run 37883768667，同一 runner）：15 单元（10 小 + 5 大）lean 均值 1 524 566 ev/s vs Arrow 792 369；gb-mega 进程 0.81 → 0.56 s；
s001 进程 1.6 ms。71 单元：Arrow 变体逐字节 71/71、lean 表级 71/71、sidecar 71/71、合成全绿。

**平台分估计（§1 模型套 run 37883796088 的窗口）：Final 窗口模型 129.7k → 139.0k（×1.072），按实测 134.5k 换算 ≈144k；
Dev 窗口直接拟合 142.5k–148.9k。报 141k–149k，中心 ~145k（+8%）。** 剩下的 85% 是平台 c_p。
