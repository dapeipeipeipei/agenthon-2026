# T3 静态线（分支 `t3/static`）— 固定开销由什么决定、静态镜像、数字

更新：2026-10-09。CI run 37883050672（commit f01eca8，`.github/workflows/t3-static.yml`）。

**候选镜像**：`ghcr.io/dapeipeipeipei/jinpei-t3@sha256:c3b318b87418b4ed229fc03017583fb9c63d1046adc4338b2d9c21d0efb1545e`
（tag `f01eca86545e-static` / `ci-static`，匿名可拉已验：匿名 token 取 manifest 返回 200）。
描述文件 `submission/submission.static.json` / `.static.final.json`（已 seal + `SubmissionDescriptor` 校验）；
打包 `pack_all.py t3-static`（→ `t3-dev-static.zip`）/ `t3-static.final`。

## 1. 做了什么

| 件 | 内容 |
|---|---|
| `engine/jpkernel/pqlite.h` | 自写 Parquet 写入器（t3/startup 的 `parquet_lite.py` 移植到 C++ 并加强）：thrift compact 手写 footer；int 列 PLAIN、字符串列 RLE_DICTIONARY（PLAIN 字典页 + 位打包索引）；全部 OPTIONAL + RLE/位打包定义级；`pandas` + `ARROW:schema` 两个 kv 元数据逐字抄自参考文件；1 Mi 行一个 row group。**整个文件在内存里拼好，一次 `write(2)`，同一块内存算 SHA-256**（不回读） |
| 同上，snappy | 树内 snappy 压缩器（经典 64 KiB 块 + 4 字节哈希 + 跳步启发式）。只有单个文件原始大小 ≥ 24 MiB 时才压（batch 按子场景数等比例降阈值），保证任何单元都远离 256 MiB 上限；阈值只看行数，重复跑字节一致。`JPSIM_SNAPPY=0/1` 可强制 |
| `engine/jpkernel/sha256ni.h` | SHA-NI 指令版 SHA-256（运行时 cpuid 选择，`JPSIM_NO_SHANI=1` 回到便携版），events.json 的 `trace_sha256`/`message_trace_sha256` 用它 |
| `jpsim_native.cpp` | 默认走 pqlite（不再 include Arrow）；旧的 libparquet 路径保留在 `-DJPSIM_ARROW_WRITER` 后面（`build_native.py` 传了这个宏，所以旧的动态镜像/Dockerfile 行为不变）。`argv[0]` 分派：`simulate`/`simulate-batch` 就是二进制本身（硬链接），不经 sh 包装；输出写完 `_exit`，内核/结果堆不释放 |
| `Dockerfile.static` | 构建段：bookworm（glibc 2.36）里 `g++ -O2 -static -ffp-contract=off -fno-fast-math`，**零共享库**（构建时断言 `ldd` = not a dynamic executable），strip 后 ~3 MB。rootfs 段：python:3.11-slim + numpy/pyarrow/scipy + 引擎 + `simulate-py`（回退）+ 静态二进制。最终 `FROM scratch` + `COPY --from=rootfs / /`：**一层**、label 2.0、USER 65534、无 ENTRYPOINT/VOLUME |
| `Dockerfile.static-min` | 仅测量用：scratch + 二进制（3.2 MB，无回退） |
| 工具 | `fixed_cost_bench.py`（镜像形态 × 单元 × 重复的矩阵，暖/冷页缓存，分相计时）；`check_content.py`（从 t3/startup 拿来）；`check_hashes.py --rows-only`；`synthetic_check.py --content`（表级比较） |

回退路径不变：二进制预检不过 → stderr 说明原因 → `exec /usr/local/bin/simulate-py`（JPSIM_KERNEL=py，pyarrow 写，字节同参考）。批量按子场景各自决定。

## 2. 正确性（全部在 CI 镜像内，平台容器参数）

- 71/71 单元：行数 = 参考行数，sidecar 算术（n_events=行数、eps=n/wall ±5%、scenario_id/seed、`trace_sha256` 重算）全对（`check_hashes.py --rows-only`）。
- 71/71 单元 190 个 parquet：**与当前提交的字节一致镜像（e05df48c，同 runner 跑，71/71 与参考逐字节相同）读回的表完全相同**——schema 含元数据、值、pandas dtype（含可空 Int64）（`check_content.py --ref-root base`）。
- 官方 g0/g1：71/71 通过；本机（Windows MinGW 编同一份源码）对 kit 参考文件：71/71 表一致（不压缩 和 强制 snappy 两种），**官方 developer verifier（含 g3 语义门）71/71 admissible**。
- 重复稳定：s001 / mp05 / gbatch-varsize / gb-mega 第二次跑逐字节相同（gb-mega 走 snappy 分支）。
- 合成套件（`--content`）：9 个场景 + 混合批量全 SAME；预期回退的 3 个（多余键 / oracle gbm / 延迟 gamma）都宣告回退且输出与 Python 引擎相同；未知代理两边都跑不了；批量里恰好 1 个子场景回退。
- 字节层面：与参考不再逐字节相同（参考是 libparquet 的字典+统计+snappy），评分读的是表；主办方明确不要求与参考字节一致（V2_NOTES §1），只要求行数 + 语义门 + 自身重复一致。

## 3. 固定开销由什么决定（CI 证据，run 37883050672）

矩阵：15 个单元（604 → 1.17M 事件）× 7 个变体 × 5 次，变体顺序每轮轮换；每个单元两种跑法：
`run` = 一次 `docker run --rm`（kit `throughput/run_unit.py` 的参数，最接近 Dev 的 create→rm），
`phases` = `docker create` / `docker start -a` / daemon `StartedAt→FinishedAt`（Final 窗口）/ `docker rm` 分别计时。

### 3a. 镜像形态不影响容器开销

15 单元均值（秒）：

| 变体 | 镜像 | run | create | start -a | window | rm | create→rm |
|---|---|---|---|---|---|---|---|
| base（当前提交 e05df48c） | 535 MB / 14 层，动态 libarrow | 0.309 | 0.020 | 0.299 | 0.254 | 0.014 | 0.333 |
| **static（候选）** | 508 MB / 2 层（scratch 压平） | **0.215** | 0.019 | 0.204 | **0.158** | 0.014 | **0.238** |
| static-layered | 538 MB / 10 层（同二进制，不压平） | 0.216 | 0.019 | 0.204 | 0.158 | 0.014 | 0.238 |
| static-min | 3.2 MB / scratch / 无回退 | 0.215 | 0.019 | 0.204 | 0.158 | 0.014 | 0.237 |
| static + `GLIBC_TUNABLES=glibc.malloc.hugetlb=1` | 同 static | 0.214 | 0.019 | 0.204 | 0.158 | 0.014 | 0.237 |
| no-op（static-min 跑 `--help`） | — | 0.112 | 0.019 | 0.103 | 0.060 | 0.014 | 0.136 |
| no-op（base 跑 `--help`） | — | 0.121 | 0.019 | 0.111 | 0.068 | 0.014 | 0.145 |

结论：
1. **create（19 ms）、rm（14 ms）与镜像大小/层数/USER/ENV 完全无关**（3 MB 单层 vs 535 MB 14 层差 <1 ms）。runc 起停 + 挂载 + 退出收尾的平台地板在 CI 上是 window 0.060 s、`docker run --rm` 0.112 s。
2. 镜像能影响的只有**进程本身**：动态 libarrow/libparquet 的装载在 no-op 上就多 8 ms（0.068 vs 0.060），真正的大头是写 parquet：libparquet 的字典编码+统计+snappy 比自写的 PLAIN 一次写慢得多（72k 事件单元 window 0.134 → 0.082 s；gb-mega 1.07 → 0.68 s）。
3. THP（malloc hugetlb）对大单元无可测收益。
4. 冷页缓存（每次跑前 `drop_caches`，模拟一台被别的镜像挤掉缓存的 host）：5 单元均值 base 0.367 s → static 0.257 s（s001 0.303 → 0.231）。冷启动下静态镜像少读 ~60 MB 共享库，差距比暖启动更大（s001：暖 −11 ms，冷 −72 ms）。

### 3b. 逐单元（暖，`run` 中位数，秒；Dev 的 create→rm 最接近这一列）

| 单元 | 事件 | base | static | no-op 地板 |
|---|---|---|---|---|
| s001 | 604 | 0.121 | 0.110 | 0.112 |
| eq-deterministic | 10 669 | 0.139 | 0.119 | 0.107 |
| as01 | 24 695 | 0.151 | 0.123 | 0.111 |
| gbatch-homog-8 | 65 163 | 0.171 | 0.129 | 0.115 |
| sf-01 | 71 932 | 0.188 | 0.142 | 0.108 |
| mp03 | 120 920 | 0.230 | 0.159 | 0.116 |
| mp05 | 240 829 | 0.321 | 0.204 | 0.114 |
| gb-pop-128 | 303 603 | 0.436 | 0.270 | 0.114 |
| gb-highfreq | 500 530 | 0.575 | 0.392 | 0.112 |
| gb-mega | 1 168 360 | 1.130 | 0.748 | 0.114 |

小单元（<25k）已经贴着地板（多 0–12 ms）；72k 级单元比地板多 ~30 ms（这就是内核模拟本身）。

### 3c. 71 单元（Final 窗口，`ci_run_units.py`，同一 runner）

| 镜像 | 71 单元均值 | 中位数 | s001 / sf-01 / mp05 / gb-mega 窗口 |
|---|---|---|---|
| base e05df48c | 490 588 | 525 562 | 0.069 / 0.134 / 0.271 / 1.075 s |
| **static c3b318b8** | **748 752（+53%）** | 786 594 | 0.061 / 0.082 / 0.142 / 0.687 s |

## 4. Dev 分估计

Dev 单元时间 = 平台 create→rm 地板 c + k × 我们的 CI 窗口。用三份已上平台的镜像（v1 34 774、v2 87 209、native 134 487）
和它们的 CI 窗口拟合，最优 c≈0.60–0.62 s、k≈0.6；用本 run 的 base 计时以 native=134 487 重新标定 k，再套到 static 的 71 单元窗口：

| 假设的平台地板 c | static 估计 | 进程时间归零的天花板 |
|---|---|---|
| 0.62 | 146 k | 176 k |
| 0.55 | 150 k | 198 k |
| 0.50 | 154 k | 218 k |
| 0.45 | 157 k | 242 k |

**静态镜像在 Dev 上预计 ≈146–157 k（native 134 k，+9–17%）**，排名约从 15 升到 ~10–12。榜首 231 k 要求平台对他们的 c ≲ 0.45 s；
本 run 证明镜像形态（大小、层数、ENV、USER、scratch）在 runc 上不改变 create/start/rm，所以剩下的差距只能来自（a）平台 host 的地板本身（同一 host 上对所有队一样，但不同 host 之间可能不同，#34 未答）、（b）GPU 钩子（见下）、（c）进程时间——我们剩下的进程时间在 72k 单元上 ~30 ms（CI），主要是内核模拟本身，下一步要压的是内核。

## 5. 风险与未测项

- **GPU 钩子**：平台每个单元 `--gpus`（`NVIDIA_DRIVER_CAPABILITIES=compute`），nvidia 钩子会往 rootfs 挂驱动库并跑 ldconfig。GitHub runner 无 GPU，测不到。
  因此候选镜像保留完整 Debian bookworm rootfs（和现在的提交一样的用户态目录结构，钩子行为相同）；纯 scratch 的 static-min 只作测量，**不提交**。
- 字节不再等于参考：评分读表（已证 71/71 表一致 + verifier 71/71）；Final 的"稳定"是自身重复，已验。若主办方日后要求与参考逐字节，回退到 `submission.cpp`（e05df48c）即可。
- 静态 glibc 2.36 的 libm 与动态 libm 同源（同版本、同 ifunc 选择），71/71 表一致佐证；1-ulp np.log 问题同 KERNEL_NOTES。
- 输出体积：不压缩时 72k 单元两份文件 6.7 MB（参考 2.7 MB）；单文件 ≥24 MiB 才 snappy，gb-mega 两文件 46 MB（参考 45 MB），离 256 MiB 很远。
