# 网页工作台

工作台沿用 [OBot-EP](https://github.com/Floating-Ocean/OBot-EP) 的 Vue 3、Element Plus、插件清单与主题体系，与机器人共享 Service、账号和数据库。当前提供个人与群榜单、可用指令目录、超管运行状态，以及 PickOne、手动比赛列表的提交、审核与应用。内容模块说明见 [内容管理](curation.md)。

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

迁移 `0003_dashboard` 只新增网页登录和会话表，不搬迁或清空现有榜单、身份和凭据。部署前同时备份应用数据库、加密密钥及 LocalStore 中的 `nonebot_plugin_orm` 迁移缓存；回滚需恢复匹配版本的数据库、缓存和源码。进程使用 `python -m backend.bot` 启动，工作目录为仓库根目录，以便 NoneBot 正确发现插件包。

## QQ 登录与权限

私聊机器人发送 `/dashboard`，打开一次性链接并点击“以 QQ 身份继续”。默认链接有效五分钟，重新申请使旧未使用链接失效；打开或预览不会自动登录。令牌位于 URL 片段，前端启动时立即移除，仅保存在内存；服务端原子兑换，只保存摘要。

群主、群管理员或超管也可以在群内 `@机器人 /dashboard`。机器人通过已验证的私聊身份发送一次性链接，群内只回复发送结果。该链接及兑换后的会话绑定发起群，仅可管理本群榜单和专属图库；不能切换个人或其他群，也不能访问公共图库审核、指令目录和运行状态。群权限失效或角色记录过期后，网页及图片请求会被拒绝。图库隔离见 [PickOne](pickone.md)。

私聊路由只使用已关联的 canonical 私聊身份，或通过现有群验证流程明确授权的私聊账号，不假设 `member_openid` 等于 `user_openid`。首次没有关联时，先私聊 `/dashboard`，在网页获取验证码并由本人在目标群执行一次。QQ 单聊发送还受好友关系、用户允许主动消息及平台能力限制；发送失败撤销未送达链接，不在群内公开链接。

会话默认十二小时，使用 HttpOnly、SameSite=Strict 的主机 Cookie，HTTPS 下启用 Secure。每账号最多八个会话；退出登录撤销当前会话。`OBOT_WEB_LOGIN_TTL`、`OBOT_WEB_SESSION_TTL` 可调整有效秒数。变更请求检查同源 Origin、JSON、CSRF 与请求大小；网页不持有内部机器令牌，不在浏览器存储登录令牌。

网页映射到现有 canonical User，每次请求重新查询持久化 superuser 标记及 `OBOT_SUPERUSERS`，撤权立即影响 API。网页群权限来自机器人接收的平台原生群角色，不接受浏览器提交角色或群目标。群绑定和推送开关继续在 QQ 群执行。若使用仅 NoneBot `SUPERUSERS` 配置的原生权限，应先通过现有管理入口登记 canonical superuser，网页才能获得同样身份权限。

普通账号可查看个人榜单和私聊场景可用指令；超管另外查看维护、调试指令和运行状态。导航过滤只是展示，后端独立鉴权。运行状态来自现有遥测，不保存消息正文，不展示用户外部身份或凭据；无连接、无记录时明确显示空状态。

`/board/bind#令牌` 仍支持不登录的一次性绑定，现由 Vue 页面完成。群绑定仍需原发起人在原群确认。工作台可切换个人与已授权群配置，修改打星、排除 UID、注册日期筛选，生成和下载今日/昨日榜单；其余已有榜单能力继续使用 QQ 指令。

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

## 群榜单权限

超管可管理机器人已有的全部群榜单。普通网页登录账号默认可访问其 canonical User 对应的、最近验证为群主或群管理员的群配置，不要求是原绑定人。普通群员没有网页群管理权限。个人与群使用独立范围选择，同名配置不会混用；群选择器仅发送不透明标识，不发布群 openid。

机器人接收群消息时，通过 Platform 的原生群管检查记录身份与角色。角色记录默认十五分钟有效（`OBOT_WEB_GROUP_TTL`），新的消息会更新记录；观察到普通成员身份立即拒绝访问。平台无法提供有效角色时不放行，离线期间未收到的撤权不能实时发现，因此记录过期后需要重新验证，不把旧群管身份永久保存为授权。

QQ 群身份与私聊身份无法自动对应时，登录网页点击“验证管理群”，复制一次性 `/dashboard-verify 验证码`，由本人在目标群 @机器人发送，然后刷新页面。验证码有效五分钟，重新申请会作废旧码，只有平台验证的群管或机器人超管可以兑换。指令绑定的是当前网页账号与该群的管理身份，不合并全局账号，不在群中发送登录链接。后续权限仍依赖验证者的最新原生角色及有效期，普通群员不能拿公开的验证码获得授权。

列表和设置接口仅返回名称、公开 OJ 地址、打星/UID/日期筛选及推送开关状态。凭据密文、管理员名称/密码、密钥、发起人身份、群消息目标均不序列化到浏览器；网页不能修改凭据或绑定范围。每个读取、修改、生成请求重新检查对应群权限；慢速绘图完成后再次检查。显示设置改变时保留历史快照，在读取时重新计算打星标记。

迁移 `0004_scene_access` 新增群角色记录、网页群授权关联和验证挑战表，不修改已有账号、会话、榜单配置或密钥。SceneAccessService 负责跨入口授权，原生群角色仍由平台适配层判断，HTTP 控制器复用 BoardService。

迁移 `0005_group_login` 给一次性登录和网页会话添加可空的群范围。原有私聊会话保持原权限；群内新申请的会话固定范围，前后端同时限制可访问工具。权限判断复用 NoneBot 原生角色组合，命令通过 `scene_permissions` 声明群场景需要 `group_admin`，帮助展示与执行使用同一检查。
