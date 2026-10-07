# STATUS — 进度存档

**最后更新：2026-10-07** · 说「agenthon 2026」即可从这里继续。
完整来龙去脉看 [WORKLOG.md](WORKLOG.md)（10-07 见 §12），设计和命令看 [README.md](README.md)。

---

## 倒计时（以 2026-10-07 为准）

| 时间 | 事件 | 剩余 |
|---|---|---|
| **2026-10-12 20:00 UTC**（纽约 16:00） | **最后一次 Dev 运行必须在此之前启动**；之后上传的不会再跑（10-13 08:00–12:00 UTC 维护） | **约 5 天** |
| 2026-10-12 23:59 AoE（= 10-13 11:59 UTC） | Registration + Development 正式关闭 | 约 6 天 |
| **2026-10-13 12:00 UTC → 10-25 23:59 AoE** | **Final + Verification**：每赛道**只能交一份** final；主办方用新种子复跑榜首做复现审核；**同分时先上传者胜** → 选定后尽早传 | |
| 10-13 之前 | 主办方公布各赛道 Final 的单 unit 时限、House 请求上限（T4#25、T2#17） | |
| 2026-12-09 → 12-13 | NeurIPS Atlanta，获奖队展示 | |

**Dev 上传次数**：每队每赛道每天 5 次、Dev 期间共 20 次；`Failed` 不扣，held/cancelled 扣。
**已用**：T2 **4/20**（今天 4/5），T4 **3/20**。

---

## 一句话现状

**两个赛道都已经在 CodaBench 上真实出分，管道全部打通。** T2 第一次上传（旧 v4）−2.3626；T4 第一次上传 0.5394（第 7/83）。
之后又传了 T2 的 rev3 / v5a / v5b 和 T4 的 B / C，等出分。剩下的事是**选 Final 版本**和**两个要人拍板的决定**（见最后）。

---

## 所有 CodaBench 上传（全部是 Development 阶段）

T2 榜面 = −（71 张 validation 卡的平均损失），**越高越好**，M0 参考行 ≈ −2.64。T4 榜面 = −0.27 + 1.27 × analysis。
「估计」是本地按新规则算的，不是平台分。

| 赛道 | 上传 ID | 版本 | 镜像 digest（前 8 位） | 平台分 | 本地估计 / 备注 |
|---|---|---|---|---|---|
| T2 | **966110** | v4 旧常数（10-06 行：F2 1.0 / F4 1.1） | `c558843f` | **−2.3626** | 本地估 −2.31…−2.38，说明本地评估器是准的 |
| T2 | 966344 | v4 rev3（F2 1.25 / F4 2.0，当前 main 默认） | `56ec5652` | 待出分 | ≈ −2.02 |
| T2 | 966459 | v5a（F4 按尺度偏向压力方向，无大模型） | `4cf052ac` | 待出分 | ≈ −1.8（区间 −1.71…−1.90） |
| T2 | 966461 | v5b = v5a + House 方向层 | `58ee9247` | 待出分 | 无本地估计（House 只在平台可连）；最坏情况约 v5a + 0.025 |
| T4 | **966114** | 第一版确定性程序 | `bf2a1647` | **0.5394**（第 7/83） | 本地复刻 0.533，误差 ≤ 0.01 |
| T4 | 966354 | B（确定性 + House） | `8ab8388e` | 待出分 | Dev 分可能被模型「记忆」美化，不能单凭它选 |
| T4 | 966417 | C（确定性 v3，推荐 Final） | `c9b5ddcd` | 待出分 | ≈ 0.537 |

还备着没传的：T2 `v5a_h`（F4 宽度 1.5 的对冲版，`1cc9f77c`）、`v5b_h`（`fb120f0e`）；T4 `D`（C + House，`140251fb`）。
完整 digest、CI run、描述文件见 `t2-work/V5_NOTES.md` §6、`t4-work/STATUS.md` 顶部表格。

### 榜单背景（agenthon.net，10-07 抓取，详见 `research/COMPETITIVE.md` §2）

| | #1 | #4 | #5 | #10 | 我们 |
|---|---|---|---|---|---|
| T2（101 队） | −1.34 | −1.78 | −1.84 | −2.00 | −2.3626（旧 v4） |
| T4（83 队） | 0.988 | 0.772 | 0.592 | — | 0.5394（#7） |

**为什么不追 Dev 第一**（`t2-work/HEADROOM.md`、`t4-work/HEADROOM.md`）：
- T2：就算每张卡事后挑最优宽度，也只到约 −1.71；就算知道每张卡波动有多大、只是不知道方向，也只到约 −1.36。
  所以 −1.34 必须同时猜对很多卡的**方向和幅度**——这在 Dev 上能做到，是因为练习卡全是 2003–2024 的著名事件，House 模型记得结果。
- T4：把所有点预测和标签都换成真实答案、保留我们的区间宽度，也只有榜面约 0.904；前 4 名（0.988 … 0.772）超过诚实上限。
- 结论：Dev 头部很可能靠的是「记得训练截止前的历史」；**封存的 Final 题在截止之后，Dev 名次 ≠ Final 名次**。
  规则 §7 禁止反推标签，我们**不**用泄漏去追 Dev 第一。

---

## Final 方案（10-13 开放后尽早上传）

**T2**：在三个里选一个，取决于主办方对 [T2#26](https://github.com/Agenthon-2026/track2-forecasting-public/issues/26) 第 2 问的回答
（Final 的 F2/F4 窗口是不是**按事件挑的**）：

| 主办方回答 | 选 | 理由 |
|---|---|---|
| 按事件挑的（和练习卡一样） | **v5a**（F4 宽 2.0 + 偏压力方向） | 押 F4 窗口里真有冲击；留出验证 F4 2.57 vs rev3 3.22 |
| 事前定的 / 可能很平静 | **v5a_h**（F4 宽 1.5，对冲） | 平静世界里只比 rev3 多亏 +0.03（全体平均） |
| 连 F4 压力方向本身都存疑 | **rev3**（main 默认） | 最保守 |

v5b 只有在 Dev 分**明显好于 v5a 超过它 +0.025 的最坏情况**、且看起来不是「记忆」时才考虑。
**T4 Final = C**：核实真值下本地分最高、NLI 矛盾检查和 reasons 检查全干净、不依赖 House。

---

## 下一步

1. **等 5 个待出分的上传**（T2 966344 / 966459 / 966461，T4 966354 / 966417），把分数填进上表。
   核对：每张卡都可接纳、没有失败卡；v5a ≈ −1.8、C ≈ 0.537 是否兑现。
2. **盯 T2#26 Q2** 和 10-13 前的 Final 资源公告（单 unit 时限、House 上限），据此定 T2 Final 版本，核对超时参数。
3. **准备 Final 描述文件**：T2 main 的 `submission.final.json` 目前指向 rev3（`56ec5652`）；若选 v5a / v5a_h，
   要按同样方式生成对应的 final 描述文件并封签。T4 C 的已有：`t4-work/submission/submission.c.final.json`。
4. **10-12 20:00 UTC 前**：剩余 Dev 次数只用来确认要交 Final 的那个镜像在平台上跑通（Final 只交 Dev 上真实跑过的版本）。
5. **10-13 12:00 UTC 起**：两个赛道的 Final 尽早上传（同分先传者胜）。

### 怎么打包（持 Team Key 的人，在仓库根目录）

```
.venv\Scripts\python pack_all.py                       # 四个默认 zip（T2/T4 的 dev + final）
.venv\Scripts\python pack_all.py t2-v5a t2-v5b         # 按候选名打包，输出到 ../agenthon-submissions/
.venv\Scripts\python pack_all.py t4-c t4-c.final       # T4 C 的 Dev / Final
```

默认在隐藏提示符里输入 Team Key；也可以加 `--key-file <路径>` 从本机文件读（Windows 过不了工具包的 chmod-600 检查时用）。
**密钥文件由持有人自己保管，不进仓库、不写进任何文档、不告诉任何 agent。**

---

## 待人拍板的决定

| 决定 | 建议 | 为什么 |
|---|---|---|
| **仓库可见性**：`dapeipeipeipei/agenthon-2026` 现在是 **PUBLIC**，网页搜索 "Agenthon 2026 forecasting github" 排第一 | **转私有，并把队长 Yuren 加为 collaborator** | 对手能看到我们的方法、参数和 F4 押注；主办方的 ARTIFACT_PROVENANCE 只要求随源码保存可供核验，不要求公开 |
| **T2 Final 选哪个**（v5a / v5a_h / rev3） | 等 T2#26 Q2；10-12 前仍无答复则倾向对冲版 v5a_h | 见上「Final 方案」 |

---

## 已完成（按时间）

| 项 | 时间 | 证据 |
|---|---|---|
| 四赛道调研，定主攻 T2、副攻 T4 | 09-08 | `research/T1…T4-*.md` |
| 本地打分台、引擎 v1/v2/v3、58 组参数实验 | 09-13 | commit `44968e8`、`a1d1c46` |
| 10-06 冲刺五条线：T2 规则合规、v4 + 独立审计、CI 构建镜像、T4 参赛、F4 事件表核对 | 10-06 | WORKLOG §11 |
| GHCR 两个包改公开，匿名拉取验证 PASS | 10-07 | `RELEASE_REHEARSAL.md` §1 |
| **发布演练**：匿名冷拉取 + runsc 和 runc 两种运行时全量跑，T2 104/104、T4 11/11，四个 zip 校验 OK | 10-07 | run 37573765226，`RELEASE_REHEARSAL.md` |
| **T2 按 10-06 新规则重选常数**（v4 rev3：F2 1.25、F4 2.0），新镜像 `56ec5652` | 10-07 | `025a063`、`8250695`，`t2-work/V4_NOTES.md` 修订 3 |
| CodaBench 账号关联队伍 299，首批 Dev 上传出分（T2 −2.3626，T4 0.5394） | 10-07 | 上表 |
| `pack_all.py`：候选名打包、`--key-file` | 10-07 | `77f4eb1`、`9d0fd94` |
| 上限分析 T2 / T4、竞争情报 | 10-07 | `t2-work/HEADROOM.md`、`t4-work/HEADROOM.md`、`research/COMPETITIVE.md` |
| T2 候选 v5a / v5a_h / v5b / v5b_h（CI 构建，镜像内 104/104） | 10-07 | `t2-work/V5_NOTES.md`，`ea5f0c1` |
| T4 候选 A / B / C / D（CI 构建，镜像内 11/11，NLI 与 reasons 检查干净） | 10-07 | `t4-work/STATUS.md`，`128caa8` |

---

## 已知的结构性问题（不是 bug）

- **F4 押注**：rev3 和 v5a 都在 F4 上加宽（v5a 还往压力方向偏）；若封存 F4 很平静，会比 M0 差（v5a 平静世界 F4 1.92，rev3 1.47）。这是 T2#26 Q2 决定的事。
- **F2 基本没有信号**：确定性文本方向信号过不了验证；v5b 的 House 方向层被限制在 q ≤ 0.6，收益和风险都小。
- **本地 Dev 估计是样本内的**：v5a 的 F4 行就是在这些练习卡上选的；留出估计看 `V5_NOTES.md` §3。
- **14/104 题无法本地打分**（EM transfer、月度宏观、最晚两个 as-of），只检查门槛。
- **本机 Docker 起不来**（WSL2 `HCS_E_SERVICE_NOT_AVAILABLE`）；镜像一律走 GitHub Actions。

---

## 环境坑（每次都会踩）

| 坑 | 解法 |
|---|---|
| 读语料 `UnicodeDecodeError: 'gbk' codec` | **每次跑脚本都 `export PYTHONUTF8=1`** |
| 官方 `qfbench2-smoke` / `load_ref_scale` 在 Windows 拒跑（`O_NOFOLLOW` / `O_DIRECTORY`） | 用 `run_all_gates.py`、`t2-work/pack/check_outputs.py`、`audit_metric.py`，或在 CI 里跑 |
| 上游仓不在 / 过时 | 克隆在**本仓库根目录**；每次开工先 `git -C <上游仓> pull` 看 CHANGELOG |
| 装 `qfbench2-common` | 只能 git URL，钉 **`@v2.6.0`**，Python ≥ 3.13 |
| 镜像构建 | 本机不行，推 `ci/t2-image` / `cand/t2-<profile>` / `cand/t4-<x>` 分支触发 GitHub Actions |
| `verify_anonymous_pull.sh` 在没建 `.venv-docker` 的机器上无输出退出 1 | 先建 venv（`RELEASE_REHEARSAL.md` §5 第 2 条） |

---

## 纪律

- **Team Key 等同密码**，不进仓库、不进文档、不贴聊天、不告诉任何 agent。只在隐藏提示符输入，或由持有人用 `--key-file` 指向自己保管的文件。
- **schema 赢过文档**。裁决顺序：scorer 源码 > `card.toml` > starter-pack `AGENTS.md` > 赛道 README。
- **不针对公开题调参**；新常数至少 5 种子平均 + 留出验证 + 压力测试。
- **不能把记住的历史结果写进预测**，也不追 Dev 第一（规则 §7 禁止反推标签；rationale 会被人工筛查）。
- **Final 只有一份**：只交在 Dev 平台上真实跑过、逐题全部可接纳的版本；选定后尽早传。
- commit 只署用户一人，不加 Co-Authored-By。
