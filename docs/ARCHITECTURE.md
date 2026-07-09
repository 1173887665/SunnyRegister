# 项目架构

当前项目拆为三个运行层：

1. **React + TypeScript 前端**
   - 目录：`frontend/`
   - 构建输出：`backend/static/`
   - 由 Go 服务直接托管静态文件，生产环境只暴露一个 Web 端口。

2. **Go + GORM 后端网关**
   - 目录：`backend/`
   - 数据库：SQLite，默认 `data/account_manager.db`
   - 负责管理员登录、账号池 CRUD、配置、任务队列、SSE 日志、导入导出、第三方平台集成。
   - 后端 API 都在 `/api/*` 下，除 `/api/auth/*`、`/api/health`、`/api/ready` 外均需要管理员登录 Token。

3. **Python 自动化 Worker**
   - 目录：`python-worker/`
   - 原 Python 功能逻辑副本：`original_runtime/`
   - Go 服务创建任务后，根据 `PYTHON_TASK_TYPES` 判断是否交给 Worker。
   - Worker 复用原项目 `application.tasks.execute_task(task_id)`，与 Go 共享同一个 SQLite 数据库，实现无感知任务状态、事件和结果回写。

## Docker Compose 服务

- `abai-autoplus`：Go 后端 + 静态前端，端口 `8000`。
- `python-worker`：FastAPI + Playwright/Camoufox/Chromium 自动化环境。
- 两个服务共享 `./data:/app/data`，因此任务、账号、配置和日志都在同一 SQLite 数据库中。

## 调用链

```text
Browser UI
  -> Go /api
    -> SQLite
    -> 如果是浏览器自动化任务：HTTP /execute
      -> Python Worker
        -> original_runtime 原功能逻辑
        -> SQLite task/task_events/accounts 回写
  -> Browser UI 通过轮询/SSE 读取任务结果
```

## 管理员权限

- 默认用户名：`admin`
- 密码来源优先级：`ADMIN_PASSWORD` > `APP_PASSWORD` > 首次启动自动生成到 `data/admin_password.txt`
- 业务接口需要前端登录后携带 Bearer Token。
