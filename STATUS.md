# STATUS — 进度存档

**最后更新：2026-10-06 晚** · 说「agenthon 2026」即可从这里继续。
完整来龙去脉看 [WORKLOG.md](WORKLOG.md)，设计和命令看 [README.md](README.md)，提交操作看 `t2-work/pack/README.md`。

---

## 倒计时（以 2026-10-06 为准）

| 时间 | 事件 | 剩余 |
|---|---|---|
| **2026-10-12 20:00 UTC**（纽约 16:00） | **最后一次 Dev 运行必须在此之前启动**；之后上传的不会再跑（10-13 08:00–12:00 UTC 维护） | **约 6 天** |
| 2026-10-12 23:59 AoE（= 10-13 11:59 UTC） | Registration + Development 正式关闭 | 约 7 天 |
| 2026-10-13 → 10-25 23:59 AoE | **Final + Verification 合并阶段**：每赛道**只能交一份** final；主办方用新种子复跑榜首做复现审核，不需要另交；同分时先上传者胜 | |
| 2026-12-09 → 12-13 | NeurIPS Atlanta，获奖队展示 | |

**Dev 上传次数**：T2/T4 每队每天 5 次、Dev 期间每赛道共 20 次；平台标 `Failed` 的不扣次数，held/cancelled 的扣。

---

## 一句话现状

**两个赛道的提交物都已就绪，只差人工步骤。** T2 默认引擎换成 v4（按榜单口径 5 种子 0.939，旧 v3 其实是 1.139），
T4 是确定性程序（11/11、0 条虚假引用）。两个镜像都在 GitHub Actions 里按平台限制测过、digest 已填进描述文件并封印。
**还从没上传过**——下面「人工步骤」做完，在 10-12 20:00 UTC 前每赛道各上传 1–2 次。

---

## 已完成

| 项 | 时间 | 证据 |
|---|---|---|
| 四赛道调研，定主攻 T2、副攻 T4 | 09-08 | `research/T1…T4-*.md` |
| 本地打分台、引擎 v1/v2/v3、58 组参数实验 | 09-13 | commit `44968e8`、`a1d1c46` |
| 上游规则梳理（截止延期、zip 上传、BYO 撤销、25 次请求、v2.6.0、M0、输出目录规则） | 10-06 | WORKLOG §8 |
| **F4 事件表核对**：31/31 行改写，删掉事后结果列 | 10-06 | `a726934`，`research/F4-events-audit.md` |
| **T2 规则合规**：月度步数 bug、rationale 由真实推导生成、sha256 种子、看门狗；`--platform-env` 104/104、0 回退 | 10-06 | `ccdc264`、`634f5ee`，`t2-work/submission/CHECKLIST.md` |
| **T2 引擎 v4** 设为默认 + **独立审计**（90/90 卡与官方源码零差）+ 审计后放宽常数 | 10-06 | `bae4ab0`、`d681d93`、`fa26d6e`，`t2-work/V4_NOTES.md`、`AUDIT_T2.md` |
| **T2 镜像**：CI run 37536643119，镜像内 104/104、与本机一致 | 10-06 | `c8d2ee4`、`62835f4`、`7dd1d6f` |
| **Track 4**：确定性程序 + 对抗审查；CI run 37533228853，镜像内 11/11 | 10-06 | `06eb855` … `d630d5f`、`1c53b8d`，`t4-work/STATUS.md` |
| 描述文件填好 digest 并重新封印；加 Apache-2.0 LICENSE | 10-06 | `7dd1d6f`、`1c53b8d`、`97b4c52` |
| WORKLOG / README / STATUS 文档 | 10-06 | `e796a26` 及本次提交 |

### 成绩（榜单口径：÷ M0，单卡裁剪 [0,4]，90 张卡算术平均，越低越好）

| profile | 总体 | F1 | F2 | F3 | F4 |
|---|---|---|---|---|---|
| M0 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| v3（旧默认） | 1.139 | 1.719 | 1.089 | 1.056 | 0.876 |
| **v4（默认，5 种子平均）** | **0.939** | **0.969** | **1.007** | **0.932** | **0.870** |

选参流程严格留出：留一时代 0.937 / 前向 0.993；v4 − M0 = −0.078，95% CI [−0.112, −0.044]。
**剩余风险**：若封存的 F4 卡完全没有冲击，F4 会输给 M0（压力测试 k=0 时 1.294）。

T4（近似真实值，只看相对差距）：8 道可评题平均 analysis 约 0.68 vs 官方基线 0.46；11/11 可接纳；202 条 claim 0 条虚假。

### 提交物

| 赛道 | 镜像 | 描述文件 |
|---|---|---|
| T2 | `ghcr.io/dapeipeipeipei/jinpei-t2@sha256:c558843f6826941ae2e9320417ffc02e036076e252c6717335183348df6b7d74` | `t2-work/submission/submission.json`（Dev）、`submission.final.json`（Final） |
| T4 | `ghcr.io/dapeipeipeipei/jinpei-t4@sha256:bf2a164756244a4ede12e5ea92bf164b60f47c2ba664f9cd955c117f8c68e01c` | `t4-work/submission/submission.json`（Dev；**Final 版待准备**） |

两者都是 `category: "api"`、`models: []`、`license: "Apache-2.0"`；`team_id` 是占位，由 `pack` 用 Team Key 写入。
⚠ `t4-work/STATUS.md` 的 CI 一节记着旧 digest `8f5193…`，**以上表为准**。

---

## 10-06 冲刺：五条线（全部完成）

| # | 线 | 状态 | 结果 |
|---|---|---|---|
| 1 | T2 规则合规 | **完成** | 104/104、0 回退（`--platform-env`）；CHECKLIST 逐条对照 |
| 2 | T2 模型 v4 | **完成** | 默认 profile；榜单口径 0.939（5 种子），审计通过 |
| 3 | Docker 打包 | **完成**（改由 CI 构建） | T2 104/104、T4 11/11 镜像内通过；digest 已填 |
| 4 | Track 4 参赛 | **完成** | 11/11、0 虚假；对抗审查修了 3 个高危点 |
| 5 | F4 事件表核对 | **完成** | 31/31 改写，无事后结果 |

---

## 下一步：人工步骤（按顺序）

详细说明在 `t2-work/pack/README.md` 第 3–8 步和 README 开头的表格。

1. **两个 GHCR 包改公开**（github.com 上 `jinpei-t2`、`jinpei-t4` → Package settings → Change visibility → Public）。
2. **匿名拉取验证**，两个都要看到 `PASS`：
   ```bash
   bash t2-work/pack/verify_anonymous_pull.sh --digest sha256:c558843f6826941ae2e9320417ffc02e036076e252c6717335183348df6b7d74
   bash t2-work/pack/verify_anonymous_pull.sh --repo dapeipeipeipei/jinpei-t4 --digest sha256:bf2a164756244a4ede12e5ea92bf164b60f47c2ba664f9cd955c117f8c68e01c
   ```
3. **确认队长已批准入队、确认队伍编号**（记录为 299，以网站为准）。
4. **打包**（Team Key 只在隐藏提示符里输入）：
   ```bash
   bash t2-work/pack/pack.sh --team-number <N> --digest sha256:c558843f6826941ae2e9320417ffc02e036076e252c6717335183348df6b7d74
   t2-work/pack/.venv-docker/Scripts/qfbench2 submission pack --descriptor t4-work/submission/submission.json --team-number <N> --out <T4 zip 路径>
   ```
5. **用队伍指定的 CodaBench 账号上传**两个 zip，每赛道先 1–2 次 Dev 上传，看逐题结果全部可接纳。**10-12 20:00 UTC 前**。
6. **10-13 → 10-25 指定 final**：T2 用 `submission.final.json`（`pack.sh --descriptor ... --out ...`）；T4 先照样准备 `-final` 描述文件。只交 Dev 上真实跑过的版本。

### 改了引擎之后（一般不需要）

改任何代码都要重新走：`run_all_gates.py --platform-env`（104/104）→ `v4_eval.py`（5 种子 + 压力测试，不按单一种子调参）→
推 `ci/t2-image` 触发 CI → 新 digest 填进描述文件。**新 digest 意味着前面的上传验证要重做**，截止前慎改。

---

## 阻塞项（只有人能做）

| 事项 | 谁 | 状态 |
|---|---|---|
| GHCR 包改公开 + 匿名拉取 PASS | dapeipeipeipei 账号 | 待办（目前私有，私有 = 主办方拉不到） |
| **批准 Wenshuo 入队** | 队长 Yuren | 09-22 时 Pending Approval，**需再确认**。不入队不算参赛 |
| 确认队伍编号（`--team-number`） | 任一队员 | 记录为 299，以网站为准 |
| 在 `pack` 隐藏提示符输入 Team Key | 持 Key 的人 | 待办 |
| CodaBench 上传 | 队伍指定账号持有人 | 待办，10-12 20:00 UTC 前 |
| T4 Final 描述文件 | 任一人/agent | 待准备，Final 阶段前 |

---

## 已知的结构性问题（不是 bug）

- **F4 押注**：v4 在 F4 上加宽并往压力方向偏；若封存 F4 很平静会输给 M0。审计后已把宽度从 1.25 收到 1.1。
- **F2 恒为 1.00 左右**：没有能过验证的确定性文本方向信号；House 模型默认关闭（没有端点可测，每题只有 25 次请求）。
- **本地分数是乐观的**：90 张公开卡、5 个时代，留出估计有约 ±0.02 噪声；真实期望大概在 0.94–0.99。
- **14/104 题无法本地打分**（EM transfer、月度宏观、最晚两个 as-of），只检查门槛。
- **Dev 榜是练习板**：打 71 道 validation 题，练习题之间互相泄题，名次没意义，看逐题分数。
- **本机 Docker 起不来**（WSL2 `HCS_E_SERVICE_NOT_AVAILABLE`，需 BIOS 虚拟化 + 「虚拟机平台」+ 重启）；镜像一律走 GitHub Actions。

---

## 环境坑（每次都会踩）

| 坑 | 解法 |
|---|---|
| 读语料 `UnicodeDecodeError: 'gbk' codec` | **每次跑脚本都 `export PYTHONUTF8=1`** |
| 官方 `qfbench2-smoke` / `load_ref_scale` 在 Windows 拒跑（`O_NOFOLLOW` / `O_DIRECTORY`） | 用 `run_all_gates.py`、`t2-work/pack/check_outputs.py`、`audit_metric.py`，或在 CI 里跑 |
| 上游仓不在 / 过时 | 克隆在**本仓库根目录**；每次开工先 `git -C <上游仓> pull` 看 CHANGELOG |
| 装 `qfbench2-common` | 只能 git URL，钉 **`@v2.6.0`**，Python ≥ 3.13 |
| 镜像构建 | 本机不行，推 `ci/t2-image` / `ci/t4-image` 分支触发 GitHub Actions |

---

## 纪律

- **Team Key 等同密码**，不进仓库、不进文件、不贴聊天、不告诉任何 agent。只在 `pack` 的隐藏提示符里由人输入。
- **schema 赢过文档**。裁决顺序：scorer 源码 > `card.toml` > starter-pack `AGENTS.md` > 赛道 README。
- **不针对公开题调参**；新常数至少 5 种子平均 + 压力测试。选参原则「四个题族都不输」。
- **不能把记住的历史结果写进分布中心**。主办方有闭卷回忆基线；rationale 会被人工筛查。
- **Final 只有一份**：只交在 Dev 平台上真实跑过、逐题全部可接纳的版本。
- commit 只署用户一人，不加 Co-Authored-By。
