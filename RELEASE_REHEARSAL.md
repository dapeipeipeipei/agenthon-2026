# 上传前发布演练（2026-10-07）

目的：用主办方平台尽可能一致的方式，把**最终发布的两个镜像**和**四个已打包的 zip** 从头验一遍。
本机 Docker 起不来（WSL2 虚拟化不可用），容器部分在 GitHub Actions（ubuntu-24.04）上跑：
工作流 [`.github/workflows/release-rehearsal.yml`](.github/workflows/release-rehearsal.yml)（只能手动触发）。

- 正式结果：run **37573765226**（<https://github.com/dapeipeipeipei/agenthon-2026/actions/runs/37573765226>），结论 success
- 前一次 run 37572420280 结果一致（104/104、11/11、parquet 104/104 字节相同），只是当时没输出 meta 的差异字段

## 结论一览

| 项目 | 结果 |
|---|---|
| 匿名拉取（`docker logout ghcr.io` + 空 Docker 配置，不用任何 token） | **两个镜像都 PASS**；registry 逐层匿名可取（各 15 项 ok、0 FAIL） |
| T2 × runc（平台 Development 实际运行时） | **104/104** 可接纳（退出 0 + 输出树规则 + 官方 g0–g3），0 个 fallback |
| T2 × runsc（gVisor release-20260928.0） | **104/104** 可接纳，0 个 fallback |
| T4 × runc | **11/11** 通过（schema、0 条虚假引用、理由检查、官方评分器、输出树） |
| T4 × runsc | **11/11** 通过 |
| 确定性 runsc vs runc | T2：104/104 `forecast.parquet` 字节相同；唯一不同的是 `forecast_meta.json` 里的 `engine.elapsed_s`（耗时，102 个单元）。T4：11/11 `answer.json` 字节相同 |
| 单元耗时（含容器创建→退出） | T2 最大 3.7 s / 平均 1.9–2.1 s（预算 1800 s）；T4 最大 1.3 s / 平均 0.7–0.8 s（预算 600 s） |
| 四个 zip（本机工具包 v2.6.0 校验） | **全部 OK**（见下） |

**没有发现会在平台上失败的问题。** 需要注意的事项见最后一节。

## 1. 匿名冷拉取

每个镜像拉取前先清空本地镜像库（`docker image prune -af`），所以共享的基础层不会被复用，是真正的冷拉取。

| 镜像 | 冷拉取 | 压缩大小 | 解压大小 | 标签 / 用户 / ENTRYPOINT |
|---|---:|---:|---:|---|
| `ghcr.io/dapeipeipeipei/jinpei-t2@sha256:c558843f…7d74` | 6.2 s | 125.9 MB | 368.2 MB | interface_version 2.0 / 65534:65534 / 无 / amd64 |
| `ghcr.io/dapeipeipeipei/jinpei-t4@sha256:bf2a1647…8e01c` | 2.8 s | 47.4 MB | 127.7 MB | interface_version 2.0 / 65534:65534 / 无 / amd64 |

（GitHub runner 到 GHCR 的带宽很高，平台上的拉取时间可能更长，但镜像很小，与 1800 s / 600 s 预算相比可忽略。）

## 2. 运行方式（与平台对齐的参数）

每个单元的实际命令（取自运行日志，T2 第一个单元）：

```
docker run --runtime=<runc|runsc> --rm --read-only --user 65534:65534 --cap-drop=ALL --security-opt no-new-privileges \
  --tmpfs /tmp:rw,noexec,nosuid,nodev,size=64m --pids-limit 256 --ulimit nofile=1024:1024 --ulimit nproc=256:256 \
  --cpus 4 --memory 14g --memory-swap 14g --network=none \
  -v <暂存后的单元目录>:/input:ro -v <out>:/output -e QFBENCH_SEED=0 -e QFBENCH_NETWORK=restricted \
  ghcr.io/dapeipeipeipei/jinpei-t2@sha256:c558843f… forecast --panels /input/panels/ --text /input/text/ --asof <card data_cutoff> --out /output/forecast.parquet
```

- T2 输入按主办方 `stage_bundle` 的做法暂存：单元根目录的 `*.parquet` 移进 `panels/`，其余原样（复用 `t2-work/pack/local_run.sh`）。
- T4：单元目录直接挂到 `/input`，动词 `analyze --task /input/task.json --corpus /input/corpus/ --out /output/answer.json`（复用 `t4-work/harness/run_local.py`）。
- 不传 `HOME`、不传任何 `MODEL_*` 变量。两个镜像的 House 调用都需要显式开关（`JINPEI_USE_HOUSE=1` / `T4_USE_HOUSE=1`），所以平台注入 `MODEL_*` 不会改变行为。
- runner 只有 4 vCPU / 15 GiB，CPU/内存配额按 runner 上限给（平台是 16 CPU / 128 GiB，只会更宽松）。
- gVisor 按 gvisor.dev 官方 apt 源安装、`runsc install` 注册，冒烟确认容器内核是 `4.19.0-gvisor`（`Starting gVisor...`）。
  平台 Development 选定的运行时是 runc（DEVELOPMENT-RUNTIME.md），runsc 是额外的保险。

## 3. 检查口径

- T2：`t2-work/pack/check_outputs.py` —— 工具包 v2.6.0 的 `artifact_tree.validate_listing` 输出树规则 + README 的 64 MiB / 4096 节点上限，
  再用**官方 track 评分器**（`scoring/scoring.py score`）跑 g0–g3，并逐单元比较 runsc 与 runc 的数值。
- T4：`run_local.py --gate`（官方 schema、`check_claim_rules`、`claim_penalty_preview`、`check_submitted_reasons`、官方 `score_unit`），
  外加同一套输出树规则（只要求 `answer.json`）。本地近似分 0.682（与之前一致；3 个单元本地无参考答案，记为 unrankable，不算失败）。
- T2 单元用的是 **track2-forecasting-public 最新 main `60509df`（10-06 晚更新）**，不是此前 CI 固定的 `2299dab`，见下节。

## 4. 四个 zip（`../agenthon-submissions/`，本机 `.venv` + 工具包 v2.6.0）

| zip | competition_id | phase | 镜像 digest | 结果 |
|---|---|---|---|---|
| t2-dev.zip | agenthon2026-forecasting-dev | dev | jinpei-t2 `c558843f…7d74` | OK |
| t2-final.zip | agenthon2026-forecasting-final | final | jinpei-t2 `c558843f…7d74` | OK |
| t4-dev.zip | agenthon2026-analysis-dev | dev | jinpei-t4 `bf2a1647…8e01c` | OK |
| t4-final.zip | agenthon2026-analysis-final | final | jinpei-t4 `bf2a1647…8e01c` | OK |

每个 zip 都检查了：恰好两个成员（`submission.json`、`team-claim.json`，工具包的确定性写法：STORED、1980 时间戳、0644）；
描述文件通过 `SubmissionDescriptor.from_mapping`（C5）解析；`descriptor_digest` 重新封印后一致；
`category=api`、`image_access=public`、`models=[]`；`team_id` 为派生格式（四个 zip 同一个），`site_team_id=299`；
team-claim 四个键、schema 2.0、`descriptor_sha256` 等于 zip 内 `submission.json` 字节的 sha256、proof 为 64 位小写十六进制、
字节与 `build_team_claim` 的序列化一致、≤1024 字节。
工具包**没有**不带 Team Key 的 proof 验证器（HMAC 只有主办方能用其存的密钥复算），所以 proof 只做了结构校验；全程没有读取或要求 Team Key，也没有打印 proof。
T2 两个 zip 另用 `t2-work/pack/descriptor_tool.py verify-zip` 复核，同样 OK。

## 5. 需要注意的事项（不会导致平台失败，但要知道）

1. **T2 官方仓 10-06 晚有大更新（`60509df`）**：评分改为「除以 M0 对该题卡的*期望*误差」，封顶与失败值从 4.0 改为 8.0；
   20 个练习单元改名；所有题卡删掉 `[metadata].difficulty`；练习单元文本语料大幅扩充。本次演练用的就是新版单元和新版评分器，104/104 全部可接纳。
   但 **README/WORKLOG 里的「榜单口径 0.939」是旧规则下的数字，和新榜单不可比**，需要按新规则重测（scale 文件不随题卡发布，要按 M0-BASELINE.md §5 自己算）。
   `t2-image.yml` 里固定的 `TRACK2_REF` 仍是旧的 `2299dab`。
2. `t2-work/pack/verify_anonymous_pull.sh` 有个小毛病：在还没建 `.venv-docker` 的机器上，`pick_python` 里的 `die` 会让 `$(...)` 子 shell 直接退出，
   脚本**无任何输出地以 1 退出**（`|| command -v python3` 的回退不会执行）。本机有 venv 所以没暴露；本工作流已把它放在建 venv 之后。不影响平台。
3. 主办方真正的 `stage_bundle.py` 不公开；据 cutoff.py 说明，它会把题卡里的具体目标日期删掉。引擎只读 `targets.asset_ids` / `horizons`，不读目标日期，所以不受影响，但这一点本地无法完全复现。
4. 运行时间这里全是秒级；平台上 runner 规格、镜像拉取和排队都不同，但离 1800 s / 600 s 的预算有几个数量级的余量。

## 复现

```bash
gh workflow run release-rehearsal.yml --ref main            # 可选输入 track2_ref / track4_ref
gh run watch <run id>
gh run download <run id> -n release-rehearsal               # summary.md、rehearsal.json、各单元输出与日志
```
