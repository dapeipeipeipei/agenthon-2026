# Track 2 打包与提交：一步一步做

这份说明从"引擎代码已经定稿"开始，一直讲到"在 CodaBench 上传 submission.zip"。
每一步都写了：**要敲什么、正常会看到什么、出错了怎么办**。

所有命令都在 **Git Bash** 里、在仓库根目录 `agenthon/` 下运行（PowerShell 只在第 7 步的备用方案里用到）。

---

## 先搞懂三件事（30 秒）

1. **我们交的不是代码，是一个 Docker 镜像。** 主办方把它拉下来，在每道题上跑一次
   `forecast --panels ... --text ... --asof ... --out ...`。镜像放在 GitHub 的镜像仓库
   GHCR（`ghcr.io/dapeipeipeipei/jinpei-t2`），用一串 `sha256:...` 的**摘要（digest）**唯一指定。
2. **上传到 CodaBench 的是一个小 zip**，里面只有两个文件：`submission.json`（写着镜像摘要）和
   `team-claim.json`（证明"这是我们队"的签名）。zip 由官方工具 `qfbench2 submission pack` 生成。
3. **Team Key 只在最后一步由你本人在隐藏输入框里敲一次。** 任何脚本都不保存、不打印、不要求你
   把它写进文件或命令行。别把它贴给任何人（包括 Claude）。

---

## 本机没有 Docker 也能完成（推荐路线）

这台电脑的 Docker Desktop 起不来（WSL2 报 `HCS_E_SERVICE_NOT_AVAILABLE`，需要在 BIOS 打开虚拟化
并以管理员身份启用"虚拟机平台"后重启）。所以**构建、在镜像里跑全部 104 题、推送镜像**都交给
GitHub Actions（工作流 `.github/workflows/t2-image.yml`）去做，本机只做第 0、4、5、6、7 步。

### 第 0 步 · 一次性：建本地检查环境（约 2 分钟）

```bash
bash t2-work/pack/make_venv.sh
```

- 作用：在 `t2-work/pack/.venv-docker/` 建一个 Python 3.13 环境，装的库版本和镜像里**一模一样**
  （见 `t2-work/requirements.lock`），外加官方工具包 qfbench2-common v2.6.0 和官方打分器。
- 正常结果：最后几行全是 `ok`，最后一行 `==> done: .../.venv-docker/Scripts/python.exe`。
- 出错：`Python 3.13 not found` → 装 Python 3.13（`py -0` 能看到 `-V:3.13`）。
  版本对不上 → `bash t2-work/pack/make_venv.sh --recreate`。

### 第 1 步 · 确认引擎代码已提交到 main

```bash
git status --short t2-work/engine      # 应该没有输出
git log --oneline -3
```

- CI 只用**已提交**的代码。有未提交改动就先提交（或者问写引擎的人）。

### 第 2 步 · 触发云端构建 + 全量测试 + 推送镜像（约 15–25 分钟）

```bash
git push origin main:ci/t2-image
gh run watch -R dapeipeipeipei/agenthon-2026 "$(gh run list -R dapeipeipeipei/agenthon-2026 -w t2-image -L 1 --json databaseId -q '.[0].databaseId')"
```

- 第一行把 main 推到 `ci/t2-image` 分支，GitHub 自动开始跑。（只推分支，不动 main。）
  如果提示 `rejected (non-fast-forward)`，说明分支上有 main 没有的提交，改用
  `gh workflow run t2-image -R dapeipeipeipei/agenthon-2026 --ref ci/t2-image` 前先找维护者合并。
- 第二行实时看进度。全部步骤打勾 = 成功。
- 它做了什么：按平台规则构建 linux/amd64 镜像 → 在镜像里用**和平台一样的限制**（只读根目录、
  uid 65534、256 进程、1024 文件句柄、断网、64 MiB `/tmp`）跑全部 104 题并计时 → 每题用官方
  输出目录规则 + 官方打分器门槛 g0–g3 检查 → 和不用容器直接跑的结果逐个数字对比 → 推送到 GHCR
  （**私有**）→ 打印镜像摘要。
- 去哪看结果：
  ```bash
  gh run view -R dapeipeipeipei/agenthon-2026 --web      # 浏览器打开，页面底部 Summary 里
  ```
  Summary 里有一行 `digest: ghcr.io/dapeipeipeipei/jinpei-t2@sha256:....`，**把 `sha256:` 开头那串
  （共 71 个字符）复制下来**，后面要用。还能看到 `units ok 104/104`、每题耗时、最慢的几题。
- 最后一个 "Anonymous pull check" 步骤显示失败是**正常的**：包还是私有的（第 3 步才改公开）。
- 出错：点开红叉的步骤看日志。
  - `Build image` 失败：多半是 Dockerfile 或依赖问题，把日志末尾 30 行发给维护者。
  - `Every unit in the image ...` 失败：日志里 `FAIL` 那几行写着是哪道题、哪道门没过；
    完整输出在页面底部 Artifacts 的 `t2-image-report` 里（`gh run download <run id> -R dapeipeipeipei/agenthon-2026`）。
  - `Push to GHCR` 失败：仓库 Settings → Actions → General → Workflow permissions 需要允许
    "Read and write permissions"。

### 第 3 步 · 把镜像包改成公开（网页上点几下，约 1 分钟）

主办方**不带任何账号**去拉镜像，私有包会直接拉不到、整次提交白费。

1. 打开 <https://github.com/users/dapeipeipeipei/packages/container/package/jinpei-t2>
   （用 dapeipeipeipei 账号登录）。
2. 右侧点 **Package settings**。
3. 拉到最下面 **Danger Zone** → **Change visibility** → 选 **Public** → 按提示输入包名
   `jinpei-t2` 确认。
- 注意：公开后**任何人**都能下载这个镜像，里面有我们的引擎代码。这是比赛规则要求的唯一公开路线
  （私有镜像需要事先和主办方另行约定）。
- 如果 Change visibility 是灰的：先在同一页把 "Inherit access from source repository" 取消勾选再试。

### 第 4 步 · 证明"不登录也能拉到"（约 20 秒）

```bash
bash t2-work/pack/verify_anonymous_pull.sh --digest sha256:<第 2 步复制的那串>
```

- 按官方文档的方法：先向 GHCR 要一个**匿名**令牌，再用它读这个摘要的清单；然后多做几步：
  核对是 linux/amd64、标签 `qfbench2.interface_version=2.0`、非 root、每一层都能匿名下载。
- 正常结果：全是 `ok`，最后一行 `PASS: ghcr.io/dapeipeipeipei/jinpei-t2@sha256:... is anonymously pullable`。
  还会显示压缩后的镜像大小。
- 出错：
  - `GHCR refused an anonymous token` 或 `HTTP 401/403` → 包还是私有，回第 3 步。
  - `HTTP 404` → 摘要抄错了，或仓库名不对。重新从第 2 步的 Summary 复制。

### 第 5 步 · 确认 `submission.json` 草稿在

```bash
ls t2-work/submission/submission.json
```

- 这个文件由另一位同学/agent 起草（12 个字段，`category: "api"`，`models: []` 或 House 模型那一行）。
  打包脚本**只会改里面 `image` 的三个值**，别的一个字都不动。
- 文件不存在 → 先找起草的人要。

### 第 6 步 ·（可选，建议）先只检查不打包

```bash
bash t2-work/pack/pack.sh --check-only --digest sha256:<摘要>
```

- 会先跑一遍第 4 步的匿名检查，然后把摘要填进 `submission.json`，用官方工具包校验（12 个字段、
  不许有多余字段、`interface_version` 2.0、`track` forecasting、`competition_id` 和 `phase` 对得上）。
- 正常结果：`image after : ghcr.io/dapeipeipeipei/jinpei-t2@sha256:...`，最后
  `check-only: descriptor filled and valid; not packing`。
- 出错：`FAIL: ...` 会写清是哪个字段的问题；文件会自动还原成原样。把那一行发给起草的人。

### 第 7 步 · 打包（需要你本人输入 Team Key）

先在网站上找到**队伍编号（team number，一个数字，不是秘密）**，然后：

```bash
bash t2-work/pack/pack.sh --team-number <队伍编号> --digest sha256:<摘要>
```

- 屏幕会出现 `Team Key (hidden):`。**粘贴或输入 Team Key，回车。输入时屏幕上什么都不显示，这是正常的。**
- 工具会：根据编号和 Key 算出 `team_id` → 重新封印 `submission.json` → 生成只含两个文件的 zip。
  脚本随后**独立核验** zip：恰好两个文件、摘要正确、`team_id` 格式对、签名绑定在这份 `submission.json` 上。
- 正常结果：最后一行 `READY: upload this file on the Track 2 CodaBench page:  .../t2-work/pack/dist/submission.zip`。
- 出错：
  - `this terminal cannot hide input` → 脚本会打印一行 PowerShell 命令。打开 PowerShell，
    粘贴运行，在 `Team Key (hidden):` 处输入 Key。然后回 Git Bash 单独核验 zip：
    `t2-work/pack/.venv-docker/Scripts/python.exe t2-work/pack/descriptor_tool.py verify-zip --zip t2-work/pack/dist/submission.zip --digest sha256:<摘要>`
    看到 `OK` 才上传。
  - `refused: descriptor team_id disagrees ...` → `submission.json` 里写了一个**错误的** `team_id`
    （`team-` 加 32 位十六进制）。让起草的人把这一项删掉，工具会自己算。
  - `refused: ...` 其他 → 编号或 Key 输错；重来一次。**不会**消耗上传次数。

### 第 8 步 · 上传到 CodaBench

1. 用**队伍指定的那个 CodaBench 账号**登录，打开 Track 2 的比赛页面（链接在参赛公告里）。
2. 进入 **My Submissions**（或 Participate → Submit），确认阶段是 **Development**。
3. 选择文件 `t2-work/pack/dist/submission.zip` 上传。
- 次数：Development 阶段每队**每天 5 次、总共 20 次**；被 hold 或取消的也算一次（平台标成 `Failed`
  的不算）。所以第 2–7 步都在本地/CI 把错查完再传。
- 上传后看状态。正常会排队、运行、出分。若显示 held/cancelled，把提交编号和状态记下来，
  通过比赛的支持渠道问主办方（**消息里绝不能带 Team Key**）。

---

## 本机有 Docker 时的路线（以后修好虚拟化再用）

```bash
gh auth refresh -h github.com -s write:packages,read:packages   # 一次性：给 gh 推镜像的权限（会开浏览器）
bash t2-work/pack/build.sh                 # 构建 + 自检；打印镜像 ID 和大小
bash t2-work/pack/local_run.sh --sample    # 4 道代表题（F1 月度 / F2 / F3 / F4），平台同款限制
bash t2-work/pack/local_run.sh --all       # 全部 104 题，约 10 分钟
bash t2-work/pack/push.sh                  # 推到 GHCR（会让你输入 push 确认），打印摘要
```

然后从第 3 步继续（第 4、6、7 步不用再写 `--digest`，脚本会读 `t2-work/pack/state/pushed.env`）。

| 脚本 | 做什么 | 常见报错 |
|---|---|---|
| `build.sh` | 静态检查 Dockerfile → 构建 linux/amd64 → 打标签 `jinpei-t2:<提交号>-<镜像ID前12位>` → 在平台限制下跑 `forecast --help` | `Docker engine not reachable`：Docker Desktop 没开；`-dirty` 警告：引擎有未提交改动 |
| `local_run.sh` | 每题复制一份输入（面板移进 `panels/`，和主办方一样）→ 按 `card.toml` 的 asof 跑容器 → 计时 → 官方门槛检查 | 某题 `EXIT 1`：看 `logs/<题号>.log`；挂载失败：加 `--runs-root C:/t2runs` |
| `push.sh` | 推送并打印 `ghcr.io/...@sha256:...` | `lacks the write:packages scope`：先跑上面的 `gh auth refresh` |
| `verify_anonymous_pull.sh` | 匿名拉取检查（第 4 步） | 见第 4 步 |
| `pack.sh` | 填摘要、校验、官方打包、核验 zip（第 6–7 步） | 见第 7 步 |

`local_run.sh --native` 不用 Docker、直接用 `.venv-docker` 跑同样的题（同样的输入布局、参数、种子），
没有 Docker 时也能做快速自查。

---

## 依赖版本为什么这样定（给维护者）

- 镜像和 `.venv-docker` 都装 `t2-work/requirements.lock`：numpy 2.5.3、pandas 3.0.5、pyarrow 25.0.1
  ——就是引擎调参、打分（v3 = 0.900，104/104 可接纳）时用的版本，且都有 Python 3.13 linux/amd64 的官方轮子。
- 2026-10-06 实测：用旧 Dockerfile 的版本（pandas 2.2.3 / numpy 2.1.3 / pyarrow 18.1.0）和锁定版本
  分别跑全部 104 题（引擎为当时 HEAD 的 v3），**104 题输出逐值完全相同**（最大差 0），两边都 104/104 通过门槛。
  所以 pandas 2 → 3 不影响结果；锁定版本只是为了"本地测的就是镜像里跑的"。
- 官方工具包 qfbench2-common v2.6.0 在镜像里用 `--no-deps` 安装（引擎不导入它；完整依赖会多带
  约 100 MB 的 scipy，而冷启动拉镜像的时间算在每题的 1800 秒里）。
- 线程数全部限制为 1（Arrow I/O 2）：平台的 `--cpus` 只是配额，不限制"看得见的核数"，不限制的话
  数学库会按宿主机核数开线程，可能撞上 256 个进程/线程的上限。引擎本身是单线程的，不受影响。

## 文件一览

| 文件 | 用途 |
|---|---|
| `t2-work/Dockerfile` | 提交镜像的配方 |
| `t2-work/.dockerignore` | 构建时只带 `engine/` 和锁文件（排除几百 MB 的输出目录） |
| `t2-work/requirements.lock` | 镜像与本地共用的精确版本 |
| `t2-work/pack/*.sh` | 上面各步的脚本；`common.sh` 是共用配置（镜像名等） |
| `t2-work/pack/check_outputs.py` | 输出目录规则（官方工具包的判定函数）+ 官方门槛 g0–g3 + 数值对比 |
| `t2-work/pack/descriptor_tool.py` | 填摘要 / 校验 `submission.json` / 核验 zip |
| `.github/workflows/t2-image.yml` | 云端构建、全量测试、推送 |
| `t2-work/pack/state/`、`dist/`、`.venv-docker/` | 本机生成的状态、zip、环境（不进 git） |
