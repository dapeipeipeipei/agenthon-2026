# Track 4（可解释性）进度 — 2026-10-06

一句话：**能交、稳、比官方基线好一截**。不靠大模型，11 个公开 unit 全部合格、0 条虚假引用；
本地近似打分 0.68 对官方基线 0.46。镜像由 GitHub Actions 构建并在平台同等限制下跑通（见下方 CI 一节）。

## 它在做什么（白话）

每道题给一张小表（1–12 行，比如几家银行、几个国债期限），要我们给每行一个预测值或标签、一个 90% 区间，
外加"证据"引用。我们的程序（`agent/`）：

1. 先把截止日之后的文档全部扔掉，再读剩下的（防"未来文件"陷阱）。
2. 从文档里找能用的数字：最近一季 EPS 同比、修订历史表、历史序列表格、"持续经营存在重大疑虑"之类的措辞。
3. 用这些数字做保守的预测；找不到就把上期值原样带过去。
4. 每条证据都是**原文逐字截取**，并且只从标给这家实体的文档里取 —— 这样官方的虚假引用检查必过。
5. 写最多 3 条推理理由（给决赛后的 LLM 评审看，加分项）。
6. 任何一步出错都会退回一份"最小合法答案"，永远写出文件、永远 exit 0。

## CI（镜像在平台同等限制下实跑）

GitHub Actions `t4-image`（分支 `ci/t4-image`，run 37531031242）全绿：linux/amd64 构建 + 构建时自测，
11 个公开 unit 在镜像里跑（只读根目录、uid 65534、无网络、pids 256、nofile 1024），官方 schema / 引用规则 /
推理理由检查 / 打分器全过，结果与本机逐项一致；异常用例 9/9、House 假服务器 4/4。镜像（GHCR 私有）：

`ghcr.io/dapeipeipeipei/jinpei-t4@sha256:8f519356912d15db41da47c7e431199b05026d454ed601558843e0f7ae54e538`

## 本地成绩

真实结果是**凭记忆写的近似值**、naive 规则是**猜的**（官方都不公开），所以只看"我们 vs 基线"的相对差距。

| unit | 我们 | 官方基线 | 说明 |
|---|---|---|---|
| EXAMPLE（苹果 EPS beat/inline/miss） | 0.82 | 0.70 | 都判 inline，我们区间更紧 |
| credit-event（8 家信用事件） | 0.88 | 0.31 | 7/8 对（YELL 漏判）；基线 3 条虚假引用 |
| eps-growth（8 家银行 EPS 增速） | 0.64 | 0.38 | MAE 9.2 vs 16.0 |
| eps-yoy（6 家 EPS 升降） | 0.84 | 0.32 | 5/6 对（AMGN 错） |
| postearn（财报次日反应） | 0.75 | 0.39 | 2/3 对 |
| cpi-comp（CPI 分项） | 0.53 | 0.56 | 唯一略输：那个月各分项都回到 0 附近，"预测 0"碰巧更准 |
| fomc 2022 / 2024（国债收益率变化） | 0.50 / 0.50 | 0.50 / 0.50 | 无信息时零变化就是最优；实际涨了 60–110bp，谁也猜不到 |
| auction / cot / macrorev | 合格 | 合格 | 我不确定真实结果，未打分 |
| **8 个可打分 unit 平均** | **0.68**（榜面约 0.60） | **0.46**（榜面约 0.31） | 榜面 = −0.27 + 1.27 × 分 |

另外：我们 202 条 claim **0 条虚假**；基线 78 条里 17 条虚假。异常输入 9 个用例全过
（未来文件陷阱、spans 格式、坏文件、无 manifest、无语料目录、陌生家族、陌生标签、单行排序、8MB 大文件）。
House 模型层用本地假服务器测了 4 种情况（正常 / 胡言乱语 / 401 / 连不上），都不影响答卷合法性。
同一输入换不同 `QFBENCH_SEED` 输出完全一致。

## 文件在哪

| 路径 | 是什么 |
|---|---|
| `PLAN.md` | 一页可行性结论（评分规则、做法、预期、风险） |
| `agent/` | 提交的程序，纯标准库 |
| `bin/analyze` | 镜像里的命令入口 |
| `Dockerfile` | linux/amd64，非 root，带 `qfbench2.interface_version="2.0"`，构建时自测 |
| `harness/run_local.py` | 跑全部公开 unit + 官方检查 + 官方打分器（`--docker-image` 可在镜像里跑） |
| `harness/robustness.py` / `mock_house.py` | 异常输入 / House 层测试 |
| `harness/approx_truth.py` | 近似真实结果和猜的 naive 规则（**仅本地诊断**，程序不读） |
| `submission/submission.json` | 描述文件草稿（已用工具箱校验并封签；镜像地址、team_id 是占位） |
| `../.github/workflows/t4-image.yml` | CI：构建、在镜像里跑全部 unit、推 GHCR |

## 怎么复现

```bash
cd t4-work
PYTHONUTF8=1 ../.venv/Scripts/python harness/run_local.py      # 约 30 秒，最后有汇总表和 GATE
PYTHONUTF8=1 ../.venv/Scripts/python harness/robustness.py
PYTHONUTF8=1 ../.venv/Scripts/python harness/mock_house.py
```

（本机已装：`pip install -e track4-analysis-public --no-deps`、`transformers sentencepiece protobuf` 和两个
judge 的分词器文件（几 MB，没下 3.5GB 权重）。Windows 上官方打分器要靠 `harness/winshim.py` 补 `O_DIRECTORY`，
并把 git 自动转换的 CRLF 还原，否则 manifest 校验不过。）

## 接下来要做的（按重要性）

1. **镜像必须能匿名拉取**：比赛要求公开镜像（或事先和组委会确认的私有镜像）。CI 推的是 GHCR 私有包，
   **交之前需要老板决定**把 `ghcr.io/dapeipeipeipei/jinpei-t4` 改为 public，或推到别的公开仓库。
2. 填 `submission/submission.json`：镜像 digest（CI 汇总里有）、正式仓库名；`team_id` 由
   `qfbench2 submission pack --team-number <号>` 用 Team Key 自动生成（Key 只有老板有）。然后重新封签。
3. 用 1–2 次 Development 提交确认线上能跑、分数与本地相对一致（每天 5 次、总共 20 次）。截止 10-12 23:59 AoE。
4. 可选：拿到 House 模型访问后再决定是否打开 `T4_USE_HOUSE=1`（打开后 `models[]` 要填 House 那一行）。
   在没测过真实端点前**不要打开**。
5. 可选改进：密封题大多是没见过的家族，最值得加的是更多"通用表格序列"识别和更好的推理理由文本。
