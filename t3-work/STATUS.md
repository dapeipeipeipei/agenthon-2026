# T3（加速市场模拟）— 进度存档

**最后更新：2026-10-09 凌晨**（kernel-cpp 线）。来龙去脉和第一阶段结论看 [PLAN.md](PLAN.md)，来源声明看 [PROVENANCE.md](PROVENANCE.md)，C++ 内核线细节看 [KERNEL_NOTES.md](KERNEL_NOTES.md)。

## kernel-cpp 线（分支 t3/kernel-cpp，10-09 凌晨）：C++ 内核 + 零 Python 运行时，71/71 逐字节相同

- `engine/jpkernel/jpkernel.cpp`：ABIDES 事件循环/撮合/四代理/oracle/延迟/两份输出的 C++ 重写，numpy legacy RNG 逐位复现；
  `jpsim_native.cpp`：`simulate`/`simulate-batch` 原生二进制，parquet 由 pyarrow 15.0.2 wheel 自带的 libparquet 写（字节同源）。
- 验证：本机 71/71、CI 裸跑 71/71、CI 镜像内（平台容器参数）71/71 + 重复稳定 + g0/g1 全过；`tools/rng_check.py` RNG 逐位 0 失败。
- 速度（榜分口径 71 单元算术平均）：本机 Python 包装 306k（v1 50.7k，参考 14.0k）；CI 镜像内 **575 460**（v1 镜像同口径 36.7k）；
  CI 裸跑 0.9–1.3M。gb-mega 1.17M 事件整进程 0.9 s（v1 19.4 s，参考 87 s）。
- **安全网（第二轮）**：native 跑前预检范围（未知键/选项/类型 → stderr 说明原因并 exec `simulate-py`，JPSIM_KERNEL=py，输出相同只是慢；批量按子场景）；
  镜像里 Python 回退带 scipy；内核补了无 latency_config 的线距延迟模型；`tools/synthetic_check.py` 9 场景+混合批量在本机/CI 裸跑/CI 镜像内三处全绿。
- 镜像：`ghcr.io/dapeipeipeipei/jinpei-t3@sha256:e05df48c07b7f2c93ecdd7c3116b6b7986518e3cd5a6efa5e80fe244a2299dc1`（tag `-native3`，匿名可拉已验；上一版 `sha256:97678c5c…` 无安全网）；描述文件
  `submission/submission.cpp.json` / `.cpp.final.json` 已封印校验 → `pack_all.py t3-cpp` / `t3-cpp.final`。
  备选（Python 包装内核镜像，同样 71/71）：`sha256:db41a1960ab14ed5546581b5fc7dd6d0e91d15ed00373ce0c743018a1f8fd6ce`（142k）。
- 风险：私有单元若用到公开单元没有的分支（MarketOrder/Replace/无 latency_config），native 直接报错不出错误结果；
  `np.log` 在 AVX-512 上与 glibc 差 1 ulp（对公开单元无影响，细节见 KERNEL_NOTES）。

## 一句话现状（v1，10-08 晚）

**可以上传 Dev 了。** 参考引擎（主办方 ABIDES 基线）已在本机复现并通过官方评分器 71/71；我们的 `jpsim` = 同一份引擎、输出逐字节相同、整进程快 3.6 倍（本机）；镜像 `ghcr.io/dapeipeipeipei/jinpei-t3@sha256:f905419e…`（CI run 37823919708，commit 04f1da4c，第 5 轮引擎）已在 CI 里用平台参数跑完 71/71 字节级一致、重复稳定、匿名可拉；描述文件 `submission/submission.json`（dev）/`submission.final.json`（final）已封印校验。打包：`.venv\Scripts\python pack_all.py t3-dev`（老板输入 Team Key）。

## 数字（本机 i7-13700K，一进程一单元，整进程墙钟 = 启动 + import + 模拟 + 写 parquet）

| | 参考引擎（abides_fork 原样） | jpsim | 倍数 |
|---|---|---|---|
| 官方评分器（developer verifier，g0–g3） | 71/71 PASS | 71/71 PASS | |
| 事件/秒 算术平均（榜分口径） | **13 988** | **50 659**（第 5 轮） | **3.62×** |
| 中位数 | 14 004 | 55 749 | |
| 最小（t3-s001，604 事件，固定开销主导） | 968 | 1 573 | |
| 最大（t3-mp05） | 18 454 | 81 183 | |
| t3-gb-mega-throughput（1.17M 事件） | 87.0 s | 19.4 s | |
| **CI 容器内**（GitHub runner，`--cpus=4 --read-only --user 65534`，daemon StartedAt→FinishedAt 窗口）均值 | 未测 | **36 739**（最小 s001 2 162，最大 gbatch-hetero 63 633；6 个 batch 单元 fork 并行 47–64k） | |

两份 parquet（trace / message_trace）的 sha256 与主办方参考文件**完全相同**（71/71，`tools/check_hashes.py`）。

## 做了什么（全部不改任何输出字节）

见 PLAN.md 第二阶段清单；落地在 `engine/`（vendored ABIDES + 补丁，BSD-3）和 `engine/jpsim/`（adapter）。所有改动用 `# [jpsim]` 标注，`tools/strip_debug_logs.py` 是去掉 107 条 `logger.debug` 的工具。

## 交付物状态

| 件 | 状态 |
|---|---|
| `t3-work/Dockerfile` | 完成：python:3.11-slim + numpy 1.26.4 + pyarrow 15.0.2，label 2.0，uid 65534，两个 verb 在 PATH，无 ENTRYPOINT/VOLUME |
| `.github/workflows/t3-image.yml` | 完成：build → 镜像内跑 71 单元（`--network=none --cpus=4 --memory=16g --pids-limit 256 --read-only --user 65534:65534`，64 MiB /tmp）→ 字节级哈希核对 → 3 单元重复稳定性 → 官方 g0/g1 → 推 `ghcr.io/dapeipeipeipei/jinpei-t3` |
| `t3-work/submission/submission.json` / `.final.json` | 完成：指向 `sha256:f905419e…`，`category: simulator`，`models: []`，已 seal + 校验 |
| `pack_all.py` | 已加 `t3-dev` / `t3-final` |

## 使用说明（怎么跑、怎么交）

1. **交 Dev**：主 checkout 里 `.venv\Scripts\python pack_all.py t3-dev` → 输入 Team Key → `../agenthon-submissions/t3-dev.zip` 上传 CodaBench（T3 赛道页）。Final 用 `t3-final`。
2. **换镜像**：push `t3/build`（或 `workflow_dispatch`，可选 `dockerfile=`/`tag_suffix=` 输入试别的 Dockerfile）→ CI 全绿后从 job summary 取 digest → `python t3-work/tools/make_descriptors.py sha256:<digest>` → commit。
3. **本地回归**（不需要 Docker）：`python t3-work/tools/run_units.py --python C:\Users\wensh\.cache\agenthon-t3\venv311\Scripts\python.exe --module jpsim --units track3-simulation-public/units --out-root <out> --pythonpath t3-work/engine`，然后 `PYTHONUTF8=1 .venv\Scripts\python t3-work/tools/check_hashes.py --units ... --out-root <out> --timing <out>/timing.json`（字节级）和 `score_local.py`（官方评分器）。
4. **改引擎的铁律**：不改任何 `random_state`/`np.random` 抽样的次数、顺序、参数；改完必须 71/71 哈希一致。

## 本地工具

- `tools/run_units.py`：一进程一单元跑任意引擎并记墙钟（参考引擎要 Python 3.11 环境，在 `C:\Users\wensh\.cache\agenthon-t3\venv311`，ABIDES 克隆+补丁在同目录 `abides/`）。
- `tools/score_local.py`：不经 Docker 直接调官方 `build_developer_verifier`，需 `PYTHONUTF8=1` 和主 checkout 的 `.venv`（已 `pip install -e track3-simulation-public`）。
- `tools/check_hashes.py`：和主办方声明的参考 sha256 逐字节核对 + sidecar 算术，CI 里就用它（不需要拉 LFS）。
- `tools/ci_run_units.py`：按 Final 口径（daemon 的 StartedAt→FinishedAt）在容器里计时。

## 风险 / 待办

- Final 一队一份；Dev 上传剩 23 次、每天 5 次；10-12 20:00 UTC 后上传不再跑。
- 容器启动开销在主办方机器上未知（他们会在 #24 发校准数据），小单元事件/秒会被压低，对所有队一样。
- 试过并放弃：Cython 纯 Python 模式（CI 慢 10%）、PyPy 3.9 两段式（71/71 一致但慢 1.1–2.6×，启动和 cpyext 开销吃掉 JIT）。
- 还可做：交易所/代理消息分发链、PriceLevel 总量缓存、更快的日志结构；每一步都要过 71 单元哈希核对。预计还有 10–20%，再往上要换语言重写内核（bit-exact 风险大）。
