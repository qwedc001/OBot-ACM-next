# Git 更新与独立守护进程

仅超级管理员可使用 `/git` 与 `/update`，群管理员无权更新代码。

| 指令 | 用途 |
| --- | --- |
| `/update`、`/git pull` | 更新稳定分支（默认 master），构建、迁移并重启 |
| `/update test <branch-name>` | 拉取远端功能分支的最新提交，保留稳定版备份后启动测试 |
| `/update restore` | 恢复本轮测试前的稳定代码和完整数据 |
| `/git restart` | 重启当前可用版本 |
| `/git status` | 当前提交与更新状态 |
| `/git fetch` | 获取远端版本，不切换运行版本 |
| `/git log` | 最近五条运行版本提交 |
| `/git plog` | 最近一次操作结果和最后 50 行日志 |

每个子指令使用独立的 Alconna handler。机器人通过本机 Unix socket 发出固定操作；只有 test 接受经过校验的远端分支名，不接受提交表达式、路径或 shell 命令。守护进程运行于 Linux，作为独立 Python 工具安装，不从更新中的应用目录导入代码。

## 开发与分支测试

新功能从 master 创建 `codex/<功能名>` 分支，本地完成实现和测试后推送该分支。不要直接在 master 开发；测试部署不会自动合并代码。

```sh
git switch master
git pull --ff-only
git switch -c codex/example-feature
# 实现、测试、提交
git push -u origin codex/example-feature
```

向机器人发送 `/update test codex/example-feature`，或以服务器上的机器人用户执行：

```sh
obotctl update test codex/example-feature --wait
obotctl status
# 放弃本轮测试，恢复测试前版本及数据
obotctl update restore --wait
```

首次进入测试分支时，先构建候选版本，再停止机器人，将**当时已经可用的稳定版提交与完整 data 快照**记录为一对基线。不会把尚未运行验证的远端 master 提交当作备份版本。构建、拉取或快照失败时不启动测试版。普通重启、再次测试同一分支或切换另一个测试分支，都保留这一基线；守护进程重启后也能恢复该记录。

测试期间启动、迁移失败或切换中断，恢复该基线的代码和数据。拉取或构建失败时当前进程继续运行。普通运行期崩溃仍只重启当前版本，避免自动丢弃已产生的数据；需要放弃测试时主动执行 `/update restore`。恢复前也会备份当前数据，测试数据不会因恢复动作被删除，但不再作为运行数据使用。

测试通过后，通过 PR 或保留提交历史的 merge / fast-forward 将功能分支合并到 master，再执行普通 `/update`。它会检查当前测试提交是远端 master 的祖先，然后保留当前数据继续迁移，成功后结束本轮测试。这个检查不把 squash / rebase 后的新提交视为同一已测提交；若采用这类合并，需要先恢复稳定基线再更新，或重新测试最终提交。不要直接对带有测试版数据的运行实例强制降级。

`state.json` 的 `baseline` 是当前测试基线，`baselines` 保留历次基线记录；每条记录包含 release、commit、branch 和 snapshot。结束测试仅解除当前基线，不删除历史代码和快照。手动恢复本身启动失败，会回到恢复动作前的测试版及其数据；恢复磁盘操作失败时保留恢复意图并拒绝新更新，修复磁盘后重启守护进程继续恢复。

已有安装需要先单独升级守护工具到 0.11.0 或更新版本并重启服务，才能接受 test / restore 操作：`uv tool install --force /srv/obot/source/supervisor`（源码路径为示例）。普通机器人更新不会升级守护进程本身。

## 首次安装

使用与机器人相同的非 root 用户安装：

```sh
uv tool install /srv/obot/source/supervisor
mkdir -p ~/.config/obot
chmod 700 ~/.config/obot
```

在 `~/.config/obot/supervisor.toml` 写入部署配置；路径均为示例，换成实际路径。不要把真实配置提交到 Git。

```toml
state_dir = "/srv/obot/update"
data_dir = "/srv/obot/shared/data"
env_file = "/srv/obot/shared/.env.dev"
repository = "https://github.com/example/obot.git"
branch = "master"
uv = "/home/obot/.local/bin/uv"
pnpm = "/usr/bin/pnpm"
startup_timeout = 90
stable_seconds = 5
extras = ["render"]
```

守护工具 0.12.0 起可用 `extras` 选择应用的可选依赖。启用 PickOne OCR 时改为 `["render", "ocr"]`，并单独升级守护工具；后续 CLI 更新和回滚重建会沿用此配置。默认仅安装 render。

保留旧服务和数据库备份，执行 `obotctl bootstrap` 预先构建首个 Git 版本；然后将系统服务的启动入口改为 `obot-supervisor`。保留原来的驱动、端口、公开 URL、绝对数据库路径等环境配置。首次启动必须确认 QQ 连接成功再撤销旧服务的恢复准备；bootstrap 不代表启动已经验证。配置和状态目录应分别为 0600、0700。共享 data 中的全部文件和目录必须允许服务用户读写，特别检查历史维护命令留下的 root 私有文件；完整快照失败时更新会中止并恢复原进程。

systemd 示例：

```ini
[Unit]
Description=OBot supervisor
Wants=network-online.target
After=network-online.target

[Service]
User=obot
Group=obot
Environment=ENVIRONMENT=dev
Environment=PYTHONUNBUFFERED=1
ExecStart=/home/obot/.local/bin/obot-supervisor
Restart=on-failure
RestartSec=5
TimeoutStopSec=60
KillMode=mixed
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full

[Install]
WantedBy=multi-user.target
```

## 日常发布

本地通过测试并提交、推送后，在服务器上以机器人用户执行：

```sh
obotctl update --wait
obotctl status
obotctl plog
```

无需从开发电脑打包或同步源码。守护进程先 fetch，在独立 Git worktree 安装锁定依赖、更新固定 submodule、安装并构建前端。准备失败时原进程继续工作；重复更新请求被拒绝。切换时停止整个进程组，备份共享 data（包括 SQLite/WAL、ORM 缓存、密钥），执行迁移，然后等待服务就绪、平台连接和稳定期。配置中的插件任一加载失败，应用直接退出，不能以残缺功能通过检查。失败会恢复完整 data 与最近可用代码，再启动旧版本。中断操作会在守护进程恢复时检查持久化阶段，必要时先恢复数据。普通运行期崩溃只重启当前版本，不恢复历史数据库。

更新日志持久化前过滤 endpoint、常见凭据格式和控制字符，保留最后 50 行。重启后优先用原消息的被动回复窗口发送 Markdown；平台拒绝 Markdown 时尝试文本，被动窗口到期时尝试主动接口。QQ 主动发送受平台权限约束，发送失败仍保留报告，可用 `/git plog` 获取。报告可能分多条，已发送页有持久化进度；进程在发送成功但进度保存前崩溃时，可能重发该页。

状态目录保留版本、快照和日志，不在 Git 中。当前仅支持同一台服务器的 SQLite 和共享目录；外部数据库、外部副作用需要独立的恢复方案。快照不等同于异地备份。定期离线备份，并按磁盘容量归档已完成操作的旧快照/版本；不能删除当前 good、正在操作的 candidate、活动 baseline 对应的代码和数据，或尚需恢复的快照。守护进程本身的升级需单独安装新工具并重启系统服务；普通 `/update` 不替换正在运行的守护程序。
