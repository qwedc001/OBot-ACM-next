# Git 更新与独立守护进程

仅超级管理员可使用 `/git` 与 `/update`，群管理员无权更新代码。

| 指令 | 用途 |
| --- | --- |
| `/update`、`/git pull` | 拉取配置的分支、构建、迁移并重启 |
| `/git restart` | 重启当前可用版本 |
| `/git status` | 当前提交与更新状态 |
| `/git fetch` | 获取远端版本，不切换运行版本 |
| `/git log` | 最近五条运行版本提交 |
| `/git plog` | 最近一次操作结果和最后 50 行日志 |

每个子指令使用独立的 Alconna handler。机器人通过本机 Unix socket 发出固定操作，不能通过聊天传入分支、路径或 shell 命令。守护进程运行于 Linux，作为独立 Python 工具安装，不从更新中的应用目录导入代码。

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
```

保留旧服务和数据库备份，执行 `obotctl bootstrap` 预先构建首个 Git 版本；然后将系统服务的启动入口改为 `obot-supervisor`。保留原来的驱动、端口、公开 URL、绝对数据库路径等环境配置。首次启动必须确认 QQ 连接成功再撤销旧服务的恢复准备；bootstrap 不代表启动已经验证。配置和状态目录应分别为 0600、0700。

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

状态目录保留版本、快照和日志，不在 Git 中。当前仅支持同一台服务器的 SQLite 和共享目录；外部数据库、外部副作用需要独立的恢复方案。快照不等同于异地备份。定期离线备份，并按磁盘容量归档已完成操作的旧快照/版本；不能删除当前 good、正在操作的 candidate 或尚需恢复的快照。守护进程本身的升级需单独安装新工具并重启系统服务；普通 `/update` 不替换正在运行的守护程序。
