# SunnyRegister 架构

SunnyRegister 由三个运行层组成。

## React 前端

- 目录：`frontend/`
- 主要页面：`frontend/src/pages/SunnyRegister.tsx`
- 构建输出：`backend/static/`
- 生产构建会嵌入 Go 二进制，运行时不需要单独的 Node 服务。

## Go 后端

- 目录：`backend/`
- 默认端口：`8000`
- 数据库：SQLite，默认 `data/account_manager.db`
- 负责登录鉴权、配置和资源 CRUD、任务队列、日志、导入导出以及 Worker 调度。

## Python Worker

- 目录：`python-worker/`
- 默认端口：`8765`，生产环境仅供 Go 后端访问。
- 仅执行 `sunny_*` 任务。
- `sunny_core` 独立实现 Outlook 邮件读取、ChatGPT 注册/登录、Session、接码与代理逻辑。
- 不依赖工作区之外的 Python 项目，也不加载旧 runtime。

## 调用链

```text
Browser
  -> Go /api
    -> SQLite
    -> POST Python Worker /execute
      -> Playwright Chromium
      -> Outlook / SMS / ChatGPT / sub2api
      -> SQLite task_events / accounts / sessions
  -> Browser polling reads logs and results
```

## Docker

```text
sunnyregister-go ---- shared SQLite volume ---- sunnyregister-python-worker
       |                                         |
       +-- React static UI                       +-- Chromium
                                                 +-- Xvfb
                                                 +-- noVNC
```

Go 与 Worker 共享同一个 named volume。每个邮箱使用独立非持久化 Context、单一主页面和独立验证码读取器，任务之间不会共享浏览器 Profile 或邮箱状态。
