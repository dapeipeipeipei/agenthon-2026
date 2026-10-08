# T3（加速市场模拟）— 进度存档

**最后更新：2026-10-08 下午**（第一天）。来龙去脉和第一阶段结论看 [PLAN.md](PLAN.md)。

## 一句话现状

参考引擎（主办方 ABIDES 基线）已在本机复现并通过官方评分器 71/71；我们的 `jpsim` = 同一份引擎、输出逐字节相同、整进程快约 3 倍；镜像由 GitHub Actions 构建并在镜像内用平台参数跑全部 71 单元做字节级回归；描述文件待 CI 出 digest 后封印。

## 数字（本机 i7-13700K，一进程一单元，整进程墙钟 = 启动 + import + 模拟 + 写 parquet）

| | 参考引擎（abides_fork 原样） | jpsim | 倍数 |
|---|---|---|---|
| 官方评分器（developer verifier，g0–g3） | 71/71 PASS | 71/71 PASS | |
| 事件/秒 算术平均（榜分口径） | **13 988** | **42 791**（第一轮）→ 第二轮见下 | ≈3.1× |
| 中位数 | 14 004 | 45 334 | |
| 最小（t3-s001，604 事件，固定开销主导） | 968 | 1 528 | |
| 最大 | 18 454 | 94 640 | |
| t3-gb-mega-throughput（1.17M 事件） | 87.0 s | ≈ 28 s | |

两份 parquet（trace / message_trace）的 sha256 与主办方参考文件**完全相同**（71/71，`tools/check_hashes.py`）。

## 做了什么（全部不改任何输出字节）

见 PLAN.md 第二阶段清单；落地在 `engine/`（vendored ABIDES + 补丁，BSD-3）和 `engine/jpsim/`（adapter）。所有改动用 `# [jpsim]` 标注，`tools/strip_debug_logs.py` 是去掉 107 条 `logger.debug` 的工具。

## 交付物状态

| 件 | 状态 |
|---|---|
| `t3-work/Dockerfile` | 完成：python:3.11-slim + numpy 1.26.4 + pyarrow 15.0.2，label 2.0，uid 65534，两个 verb 在 PATH，无 ENTRYPOINT/VOLUME |
| `.github/workflows/t3-image.yml` | 完成：build → 镜像内跑 71 单元（`--network=none --cpus=4 --memory=16g --pids-limit 256 --read-only --user 65534:65534`，64 MiB /tmp）→ 字节级哈希核对 → 3 单元重复稳定性 → 官方 g0/g1 → 推 `ghcr.io/dapeipeipeipei/jinpei-t3` |
| `t3-work/submission/submission.json` / `.final.json` | 待 CI digest |
| `pack_all.py` | 已加 `t3-dev` / `t3-final` |

## 本地工具

- `tools/run_units.py`：一进程一单元跑任意引擎并记墙钟（参考引擎要 Python 3.11 环境，在 `C:\Users\wensh\.cache\agenthon-t3\venv311`，ABIDES 克隆+补丁在同目录 `abides/`）。
- `tools/score_local.py`：不经 Docker 直接调官方 `build_developer_verifier`，需 `PYTHONUTF8=1` 和主 checkout 的 `.venv`（已 `pip install -e track3-simulation-public`）。
- `tools/check_hashes.py`：和主办方声明的参考 sha256 逐字节核对 + sidecar 算术，CI 里就用它（不需要拉 LFS）。
- `tools/ci_run_units.py`：按 Final 口径（daemon 的 StartedAt→FinishedAt）在容器里计时。

## 风险 / 待办

- Final 一队一份；Dev 上传剩 23 次、每天 5 次；10-12 20:00 UTC 后上传不再跑。
- 容器启动开销在主办方机器上未知（他们会在 #24 发校准数据），小单元事件/秒会被压低，对所有队一样。
- 还可做：交易所/代理消息分发链、PriceLevel 总量缓存、更快的日志结构；每一步都要过 71 单元哈希核对。
