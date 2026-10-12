# T1 本地代理评测（2026-10-12）

目的：House（Nemotron-3-Super-120B）只能在平台上访问、Dev 队列一天一次且已堵 20+ 小时，
所以用本机 GPU 上的开源模型当"代理 House"，在公开题 + 官方 checker 上比较 agent 变体的通过数。

## 搭建

| 项 | 值 |
|---|---|
| 推理服务 | Ollama 0.40.3（Windows，`ollama serve` 由我们启动），RTX 4070 Ti 12 GB |
| 模型 | `qwen3:8b`（Q4_K_M，支持思考开关）；`qwen3:30b-a3b` 也已拉取，但 12 GB 放不下、4 并发合计仅 ~80 tok/s，没跑整轮 |
| 服务参数 | `OLLAMA_CONTEXT_LENGTH=32768`、`OLLAMA_NUM_PARALLEL=3`、`OLLAMA_KV_CACHE_TYPE=q4_0`、`OLLAMA_FLASH_ATTENTION=1` → 100% GPU，3 路并发每路 ~50 tok/s（6k token 提示词） |
| 协议适配 | `harness/ollama_shim.py`（:11435）：`/v1/chat/completions` → Ollama `/api/chat`；`chat_template_kwargs.enable_thinking` → `think`（缺省=开，和 House 一样）；`max_tokens`→`num_predict`（思考 token 计入，和 House 的 4000 上限一致）；思考放 `reasoning_content`；`finish_reason=length`；超上下文直接 400（模拟预拒）|
| 跑题 | `harness/proxy_eval.py`：源码跑 agent（独立 venv `~/.venvs/agenthon-t1`，与镜像相同的 numpy 2.2.6/pandas 2.2.3/scipy 1.15.3… 钉版本），再用 `check_units.py` 跑每题官方 checker（全局 C:\app 等 junction，加了文件锁防并发） |
| 比较 | `harness/proxy_compare.py <run dir>...`：边跑边评已完成的题，输出每题 PASS/子测试/请求数/秒 |
| 时间 | 本地每个请求 50–400 s（平台据他队数据 ~15–45 s），所以代理**取消时间压力**（`JP_UNIT_BUDGET_SEC=4000`、`JP_CARD_MARGIN_SEC=-5000`、调用超时 1800 s），只比答案质量；否则 v3 的裁判几乎从不触发 |
| 子集 | `harness/proxy_subset12.txt`：平台上 v1/v2 过过的 10 题 + zero-coupon + lookback（31 题子集 `proxy_subset30.txt` 一轮要 ~8 h，放弃）|

命令（`t1-work/` 下）：

```
ollama serve   # 带上面的环境变量
python harness/ollama_shim.py --port 11435 --log harness/out/proxy/shim.jsonl
python harness/proxy_eval.py --variant v3 --units-file harness/proxy_subset12.txt --parallel 1
python harness/proxy_compare.py harness/out/proxy/v3__qwen3-8b ...
```

## 结果（qwen3:8b，12 题，checker 全过才算 PASS）

| 题 | v1 式 | v3 | v4b | v4b 重复 | v3 重复 |
|---|---|---|---|---|---|
| t1-bs-greeks-pde | fail 32/39 r7 668s | fail 33/39 r18 976s | fail 32/39 r11 602s | fail 29/39 r9 499s | fail 29/39 r7 239s |
| t1-cir-bond-pricing | fail 0/35 r7 473s | fail 0/35 r15 2009s | fail 0/35 r18 947s | fail 28/35 r18 1020s | fail 0/35 r17 584s |
| t1-double-sort | PASS 19/19 r2 54s | PASS 19/19 r7 142s | PASS 19/19 r10 132s | PASS 19/19 r10 138s | PASS 19/19 r6 80s |
| t1-earnings-surprise-calculator | PASS 8/8 r2 26s | PASS 8/8 r5 154s | PASS 8/8 r4 62s | PASS 8/8 r5 89s | PASS 8/8 r5 69s |
| t1-interest-rate-cap-floor | PASS 7/7 r2 29s | PASS 7/7 r4 73s | PASS 7/7 r5 54s | PASS 7/7 r5 188s | PASS 7/7 r6 127s |
| t1-lookback-options | fail 4/25 r5 243s | fail 4/25 r20 860s | fail 4/25 r16 501s | fail 19/25 r19 742s | fail 4/25 r15 389s |
| t1-merton-jump-diffusion | fail 0/28 r12 1075s | fail 9/28 r10 484s | fail 27/28 r12 322s | fail 18/28 r20 1319s | fail 0/28 r15 1875s |
| t1-momentum-backtest | fail 18/26 r2 58s | fail 19/26 r10 231s | fail 22/26 r12 301s | fail 16/26 r10 183s | fail 17/26 r10 342s |
| t1-pca-factor-portfolio | fail 1/8 r5 189s | PASS 8/8 r14 287s | PASS 8/8 r6 146s | PASS 8/8 r4 47s | PASS 8/8 r5 63s |
| t1-sma-crossover-spy | fail 16/20 r3 133s | fail 11/20 r10 311s | fail 10/20 r19 775s | fail 17/20 r9 256s | fail 14/20 r9 266s |
| t1-variance-swap-replication | fail 16/17 r7 364s | fail 0/17 r10 204s | PASS 17/17 r11 354s | PASS 17/17 r10 175s | fail 0/17 r16 323s |
| t1-zero-coupon-bootstrapping | fail 1/6 r10 417s | fail 4/6 r6 218s | fail 5/6 r10 479s | fail 4/6 r10 177s | fail 4/6 r10 119s |

| 变体 | 通过 | 子测试 | 平均请求 | 平均秒数 |
|---|---|---|---|---|
| v1 式 | 3/12 | 122/238 | 5.3 | 311 |
| v3 | 4/12 | 122/238 | 10.8 | 496 |
| v3 重复 | 4/12 | 110/238 | 10.1 | 373 |
| **v4b** | **5/12** | **159/238** | 11.2 | 390 |
| **v4b 重复** | **5/12** | **190/238** | 10.8 | 403 |

- v1 式 = v3 代码 + `JP_CANDIDATES=1 JP_THINK=0 JP_JUDGE=0`（单候选、全关思考、无裁判）。
- v4b = 分支 `t1/v4-quality` 的前两个提交（下节）。
- 两次独立跑都是 v4b 5 > v3 4 > v1 式 3；子测试 v4b 159/190 vs v3 122/110。v4b 稳定多过的是 variance-swap-replication（两次 17/17；v3 两次都没有候选跑通，只留下 `{}` 桩文件 → 0/17，v4b 靠修复路径跑通，与软检查无关）和 pca（v1 式没过）。
- sma-crossover 第一轮 v4b 10/20 < v1 式 16/20：不是软检查误报（三个候选都没跑通，软检查未触发），是采样运气 + 循环里 pandas FutureWarning 刷屏；第二轮 v4b 17/20。
- 单题方差很大（同一变体重跑：cir-bond 0/35 → 28/35），12 题上 ±1 题属于噪声范围；子测试总数更稳一些。

## 代理上看到的问题（对 Nemotron 同样适用的部分）

1. **修复不动点**：种子固定 + 温度 0.1，模型拿到「脚本 + 报错」后经常把**同一份脚本原样返回**；
   v3 的 2 轮候选修复 + 6 轮后期修复全部空转（smoke 里 zero-coupon 连续 9 次 1463 token 一模一样）。
   提高温度、换种子再问一次也常常还是原样照抄（抄输入的分布极尖）。
2. 完整但**解析失败**的回复被当成"太长"，走"写紧凑版"重写，丢掉了具体的 SyntaxError。
3. traceback 只给行号，模型看不到是哪一行；循环里的 pandas FutureWarning 会刷满 stderr 尾部。
4. 输出键名/列名与题面骨架不一致是 checker 失败的常见原因（`results.json` 少键、CSV 列名不同）。

## v4-quality 改动（分支 `t1/v4-quality`，基于 `t1/v3`，不动预算/时钟）

| 开关（默认 1） | 改动 |
|---|---|
| `JP_NOOP_RETRY` | 修复回复与已试过的脚本相同 → v4b：改发**全新重写**（`prompts.rewrite_messages`：只给失败报告、不给旧脚本），换种子；再相同就放弃这轮 |
| `JP_CODE_CONTEXT` | 运行报告附上 traceback 指向的源码行（带行号、`>>` 标出）|
| `JP_SKELETON` | 软检查：题面 JSON 骨架里的键（`unit.skeleton_keys`）、CSV "columns:" 后列出的名字（`unit.csv_layout_tokens`）必须出现在输出里；名字同时写进交付物 spec；软问题进修复报告和裁判提示，但不让候选变"不可用"；只为软问题做 1 轮修复，在侧工作区试跑，仍干净且软问题更少才采纳 |
| `JP_CAND_SEEDS` | 每个候选不同 seed（v3 三个候选同一 seed）|
| `JP_SYNTAX_REPAIR` | 完整但不能解析的脚本直接去跑，SyntaxError 进修复（不再走"紧凑版"重写）|
| （无开关）| 修复提示要求首行 `# FIX: <原因>` 且必须改动；子进程 `PYTHONWARNINGS` 忽略 Future/Deprecation 警告、stderr 重复行折叠、先清洗再截尾 |

骨架键抽取在 86 题上核对过：67 题抽到键，1435 个键里 80 个在 checker 源码里找不到（多为题面列出但 checker 不查的键，写上也无害）。
mock 回归：86/86 exit 0，flaky/truncated/disagree/think/413/garbage/500 全过，两道 oracle 仍过 checker，selftest ok。

## 局限

- 8B 代理比 Nemotron-120B 弱得多（语法错误、照抄脚本都远多于大模型），失败模式不同；第二个模型（30B-A3B）因速度没跑。
- 12 题样本小、单题方差大；结论是"v4b ≥ v3"，不是精确的增益估计。
- 代理不施加时间压力：平台上 v4 的额外请求（no-op 重写、软修复）会多占时间与请求额度（平均请求 v3 10.8 → v4b 11.2，基本持平）。
