# 网页工作台

工作台沿用 [OBot-EP](https://github.com/Floating-Ocean/OBot-EP) 的 Vue 3、Element Plus、插件清单与主题体系，与机器人共享 Service、账号和数据库。当前提供个人榜单、可用指令目录及超管运行状态。PickOne、比赛列表等工具随对应业务迁移后再接入。

## 目录与启动

```text
backend/
  bot.py             NoneBot 入口
  framework/         共享 Service、Client、身份、模型与图片视图
  plugins/           原生命令入口
  web/               HTTP 会话、API 插件发现与 Vue 静态文件托管
  migrations/        ORM Alembic 修订
  tests/             后端与原生事件测试
frontend/
  src/plugins/       各工具的 manifest、routes、api、views
  src/layout/        共享导航与工具外壳
  src/stores/        会话与主题
  tests/             前端单元和浏览器测试
third-party/         固定版本的生成器 submodule
```

从仓库根目录运行，环境文件、数据和密钥仍保留在根目录原有位置：

```sh
git submodule update --init --recursive
uv sync --locked --extra render
pnpm --dir frontend install --frozen-lockfile
pnpm --dir frontend build
uv run --no-sync python -m framework.migrate
uv run --no-sync python -m backend.bot
```

默认由同一个 FastAPI 服务提供网页、`/api/*` 和 QQ 接入。`OBOT_FRONTEND_DIST` 可覆盖默认的 `frontend/dist`。不要把开发服务器暴露到公网。

开发时运行 `pnpm --dir frontend dev`，Vite 将 `/api` 代理到本机 8080。将本地运行配置的 `OBOT_PUBLIC_BASE_URL` 设为 `http://127.0.0.1:5173`，并用相同地址打开页面；HTTP 只允许字面回环地址或 localhost，生产必须使用 HTTPS 根地址。

生产先构建前端，再把根路径和 `/api/*` 代理到同一个后端，保留 `/qq/*`，阻止公网 `/internal/*`。例如：

```caddyfile
bot.example.com {
    @internal path /internal /internal/*
    respond @internal 404
    reverse_proxy 127.0.0.1:8080
}
```

迁移 `0003_dashboard` 只新增网页登录和会话表，不搬迁或清空现有榜单、身份和凭据。部署前同时备份应用数据库及加密密钥；回滚需恢复匹配版本的数据库和源码。已有进程启动入口改为 `backend/bot.py`，工作目录仍为仓库根目录。

## QQ 登录与权限

私聊机器人发送 `/dashboard`，打开一次性链接并点击“以 QQ 身份继续”。默认链接有效五分钟，重新申请使旧未使用链接失效；打开或预览不会自动登录。令牌位于 URL 片段，前端启动时立即移除，仅保存在内存；服务端原子兑换，只保存摘要。

会话默认十二小时，使用 HttpOnly、SameSite=Strict 的主机 Cookie，HTTPS 下启用 Secure。每账号最多八个会话；退出登录撤销当前会话。`OBOT_WEB_LOGIN_TTL`、`OBOT_WEB_SESSION_TTL` 可调整有效秒数。变更请求检查同源 Origin、JSON、CSRF 与请求大小；网页不持有内部机器令牌，不在浏览器存储登录令牌。

网页映射到现有 canonical User，每次请求重新查询持久化 superuser 标记及 `OBOT_SUPERUSERS`，撤权立即影响 API。网页不具有 QQ 群现场权限，因此只管理本人的榜单配置；群绑定和推送管理继续在 QQ 群执行。若使用仅 NoneBot `SUPERUSERS` 配置的原生权限，应先通过现有管理入口登记 canonical superuser，网页才能获得同样身份权限。

普通账号可查看个人榜单和私聊场景可用指令；超管另外查看维护、调试指令和运行状态。导航过滤只是展示，后端独立鉴权。运行状态来自现有遥测，不保存消息正文，不展示用户外部身份或凭据；无连接、无记录时明确显示空状态。

`/board/bind#令牌` 仍支持不登录的一次性绑定，现由 Vue 页面完成。群绑定仍需原发起人在原群确认。工作台可列出个人配置、切换打星显示、生成和下载今日/昨日榜单；其余已有榜单能力继续使用 QQ 指令。

## 扩展工具

后端在 `backend/web/plugins/<slug>.py` 声明 `PLUGIN = WebPlugin(...)`，提供 `build_router(web)`；启动时自动发现。HTTP 层只验证请求、权限并转换结果，业务逻辑调用已有 Service，不复制进控制器。

前端在 `frontend/src/plugins/<slug>/` 提供 `index.js`，导出 manifest、routes 和 api；注册器自动发现。清单提供名称、说明、主页、顺序、颜色和 superuser 标记。路由按需加载视图，API 通过统一 HTTP 客户端携带会话及 CSRF。前后端使用相同 slug，无需修改总路由或手写工具卡片列表。新增业务仍按原生 Plugin → Service → Client 分工设计。

## 验证

```sh
uv run --extra render pytest -q
uv run ruff check .
uv run ruff format --check .
uv lock --check
pnpm --dir frontend test
pnpm --dir frontend format:check
pnpm --dir frontend build
pnpm --dir frontend test:e2e
```

浏览器测试启动独立回环服务，使用临时 SQLite 和合成身份，不加载真实环境；Hydro 数据为固定测试数据，图片使用真实 Peeper 生成器。优先使用已安装 Chrome，可通过 `OBOT_EP_BROWSER` 指定路径；没有浏览器时先安装 Playwright Chromium。合成测试不能代替真实 QQ 登录和 Hydro 凭据验收。

设计约定见 [视觉设计](design.md)，机器人架构见 [框架说明](framework.md)。
