# STATUS — 进度存档

**最后更新：2026-09-22** · 说「agenthon 2026」即可从这里继续。

---

## 倒计时

| 日期 | 事件 | 剩余 |
|---|---|---|
| **2026-09-28 23:59 UTC**（纽约时间 19:59） | **注册截止 + Development 阶段结束** | **6 天** |
| 2026-09-29 | 队伍名单锁定，Final 阶段开始 | |
| 2026-09-29 → 10-12 | 最终评测，在封存题集上跑。每赛道只能指定一份提交，**失败即永久淘汰该赛道** | |
| 2026-10-13 → 10-25 | 复现审核 | |
| 2026-12-09 → 12-13 | NeurIPS Atlanta，获奖队展示 | |

---

## 一句话现状

Track 2 的预测引擎已经做到 v3，**比主办方的 text-blind 基线好 10%，104/104 道公开题全部通过门槛**。
但**还没打包成 Docker 镜像，也还没走过一次官方提交流程**——这是剩下 6 天里唯一真正要紧的事。

---

## 已完成

### 调研
- 四个赛道全部读完，报告在 `research/T1-coding.md` … `T4-analysis.md`
- 结论：主攻 T2（合约小、不依赖大模型、官方 SOLVER-PLAYBOOK 近似答案），副攻 T4
- `research/F4-events.md`：31 道危机题的历史背景，Claude 预填，**待人工核对**

### 引擎（`t2-work/engine/`，约 1300 行）
三档 profile，默认 v3：

| profile | 做法 |
|---|---|
| v1 | 块自助采样代替正态、近期 0.6 + 全史 0.4 波动混合、多资产共用块索引、2000 抽样 |
| v2 | 加逐路径尺度混合（p=0.2, k=2.0）+ 全局宽度 1.15，治 v1 的过度自信 |
| **v3** | 确定性语料事件检测器驱动放宽 + 尾部偏斜 + 二簇混合，响应按 `1/sqrt(n_cells)` 衰减，全局宽度退回 1.0 |

回退链 v3 → v2 → 高斯，任何异常都仍写出三个合法文件并 exit 0。

### 成绩（90 道可本地还原真实值的题，ratio 越低越好，1.0 = 打平基线）

| profile | 总体 | F1 | F2 | F3 | F4 | 出 90% 区间比例 |
|---|---|---|---|---|---|---|
| 基线 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 23.7% |
| v1 | 0.925 | 0.712 | 1.009 | 0.849 | 1.076 | 26.3% |
| v2 | 0.939 | 0.842 | 1.006 | 0.874 | 0.999 | 11.4% |
| **v3（默认）** | **0.900** | **0.804** | **1.002** | **0.872** | **0.907** | **13.1%** |

三档全部 104/104 可接纳、零回退。理想的出区间比例是 10%。

### 工具链
- `run_all_gates.py` 跑遍 104 题查四道门（约 5 分钟）
- `realized.py` 从兄弟题面板还原真实值（90/104 覆盖）
- `score_local.py` 用官方打分器算分算比值（约 4 秒）
- `grid_v2.py` / `ablate_v3.py` 网格与消融（58 组配置的结果已存表）
- `events_scan.py` 把 104 题的事件特征打成一张表

---

## 下次开工，按这个顺序

### 第 0 步 · 确认环境还在（2 分钟）

```bash
cd ~/OneDrive/Desktop/01-项目代码/agenthon
export PYTHONUTF8=1
ls track2-forecasting-public/units | wc -l          # 应为 104；若上游仓没了，见 README 环境搭建
.venv/Scripts/python -c "from qfbench2_common.contracts import CONTRACT_SET; print(CONTRACT_SET)"   # 1.1.0
```

### 第 1 步 · 确认引擎没坏（5 分钟）

```bash
.venv/Scripts/python t2-work/run_all_gates.py --engine engine --out-root t2-work/out_engine_v3
# 期望：admissible 104/104  fallbacks 0
.venv/Scripts/python t2-work/score_local.py --out-dir t2-work/out_engine_v3 --baseline-dir t2-work/out --name v3
# 期望：overall ratio_geo 0.900，F1 0.804 / F2 1.002 / F3 0.872 / F4 0.907
```

若 `t2-work/out/`（基线那一列）不存在，先跑
`.venv/Scripts/python t2-work/run_all_gates.py --out-root t2-work/out`（约 5 分钟）。
若 `t2-work/realized/` 不存在，先跑 `.venv/Scripts/python t2-work/realized.py`（约 1 分钟）。

### 第 2 步 · 最高优先：打包并跑通提交（**卡在这里**）

**前置：用户需要手动启动 Docker Desktop。** 上次检查时未运行。

```bash
docker build --platform=linux/amd64 -t jinpei-t2:dev -f t2-work/Dockerfile t2-work
mkdir -p /tmp/t2out
docker run --rm --network=none --cpus=4 --memory=16g \
  -v "$PWD/track2-forecasting-public/units/t2-F4-gbp-brexit-2016":/input:ro \
  -v /tmp/t2out:/output jinpei-t2:dev \
  forecast --panels /input --text /input/text --asof 2016-05-31 --out /output/forecast.parquet
```

然后依次：推镜像到公开 registry → **用匿名 token 验证真的可拉取**（`docker pull` 用你自己的凭证，
验证不了任何东西，具体命令在 `research/T1-coding.md` 的 RUNTIME-ENVIRONMENT 摘录里）→ 用
`qfbench2_common.contracts.descriptor.seal_descriptor_digest` 封印 `submission.json` → 在官方平台提交。

**注意 `submission.json` 严格 12 个字段**，`house_endpoint_only` 是官方文档亲手埋的雷，加了直接被拒。
`team_id` 从 CodaBench 个人页取。

### 第 3 步 · 若还有时间

按 README「想改进从哪下手」的顺序。最划算的两项：

1. 核对 `research/F4-events.md`（不用写代码）
2. 扩充 `engine/events.py` 的关键词表，用 `events_scan.py` 找压力分数偏低但实际是危机的题

---

## 阻塞项

| 事项 | 谁 | 状态 |
|---|---|---|
| **启动 Docker Desktop** | 用户 | 未启动。Claude 不管进程 |
| **Yuren 批准入队申请** | 队长 | 上次检查时 Pending Approval。**不入队就不算参赛、不能上榜、不能获奖** |
| 官方提交入口 | 主办方 | 公告说 "further instructions to come"，需登录 agenthon.net 看 Announcements |
| 核对 F4 事件表 | 用户/队友 | 未开始 |

---

## 已知的结构性问题（不是 bug）

- **F2 的 27 道汇率题恒为 1.00。** 靠宽度调不动，必须真读懂文本判断方向。等 `MODEL_ENDPOINT` 上线。
- **真实的左尾比任何对称设置都重。** v3 的偏斜缓解了一部分，按题族分设不对称参数应能更进一步。
- **14/104 题无法本地打分**（EM transfer 题、月度宏观题、最晚两个 as-of），只检查门槛。
- **Track 4 未开工。** 报告在 `research/T4-analysis.md`。

---

## 环境坑（每次都会踩）

| 坑 | 解法 |
|---|---|
| 读语料 `UnicodeDecodeError: 'gbk' codec` | **每次跑脚本都 `export PYTHONUTF8=1`** |
| 官方 `qfbench2-smoke` 报 `O_NOFOLLOW unavailable` | Windows 上用不了，用我们的 `run_all_gates.py`（调的是同一个官方打分器），或上 WSL/Docker |
| 上游仓不在 | 四个 track 仓要克隆到**本仓库的父目录**，见 README |
| 装 `qfbench2-common` | 只能用 git URL 且必须钉 `@v2.3.1`，PyPI 上没有，装分支会让本地和评测结果分叉 |

---

## 纪律

- **Team Key 等同密码**，不进仓库、不进文件、不贴聊天。仓库已扫描确认干净。
- **schema 赢过文档**。裁决顺序：scorer 源码 > `card.toml` > starter-pack `AGENTS.md` > 赛道 README。
- **不针对这 104 道公开题调参**。最终排名用封存题集，选参原则一直是「四个题族都不输」。
- **不能把记住的历史结果写进分布中心**。主办方会发布闭卷回忆基线专门抓这个。我们只在有事件信号时
  把分布**放宽到包含**那个方向。
- commit 只署用户一人，不加 Co-Authored-By。
