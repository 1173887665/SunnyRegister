# SunnyRegister Docker 使用说明

SunnyRegister 提供两份 Compose：

| 文件 | 用途 | Web 绑定 |
| --- | --- | --- |
| `docker-compose.yml` | 本地开发、局域网试用 | 默认 `0.0.0.0:8000` |
| `docker-compose.production.yml` | 云服务器生产部署 | 默认 `127.0.0.1:8000` |

生产服务器必须使用 `docker-compose.production.yml`。公网接入与生产安全配置见 [生产部署说明](PRODUCTION_DEPLOY.md)。

## 服务结构

| 服务 | 内容 | 网络边界 |
| --- | --- | --- |
| `postgres` | PostgreSQL 16 | 开发环境仅绑定 `127.0.0.1:5432`；生产环境不发布端口 |
| `sunnyregister` | Go API + React 静态前端 | 开发环境发布 8000；生产环境仅回环地址 |
| `python-worker` | FastAPI + Camoufox + Playwright | 只在 `sunnyregister-worker` 网络提供 8765 |
| noVNC | Xvfb 虚拟桌面 | 默认关闭；启用后仅 `127.0.0.1:6080` |
| `sunnyregister-postgres` | PostgreSQL 数据目录 | Docker named volume |
| `sunnyregister-data` | 管理员密码与审计导出 | Docker named volume |

Go 与 Python Worker 使用同一个 PostgreSQL 数据库。容器重建不会删除 named volume，除非显式执行 `down -v`。

## 本地 Docker 启动

Windows Docker Desktop：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\docker-up.ps1
```

Linux：

```bash
bash scripts/docker-up.sh
```

脚本会创建本地 `.env`、生成管理员密码、Worker Token 与 PostgreSQL 密码、构建镜像并等待健康检查。启动后访问：

```text
http://127.0.0.1:8000
```

## Windows 本地完整测试

本地开发机可使用单一入口完成与 CI 一致的检查并启动完整运行栈：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\test-local.ps1
```

脚本依次执行：

1. 校验 Docker Compose 配置。
2. 执行前端 lint/build、Go test/vet、Python compile/pytest/pip check。
3. 构建并启动 PostgreSQL、Go 后端和 Python Worker。
4. 实际查询 PostgreSQL，并验证 Worker、`/api/ready` 与 `/api/health`。

首次执行需要访问 Docker Hub、npm 和 PyPI，并会下载浏览器运行环境。后续快速复验可复用现有镜像：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\test-local.ps1 -NoBuild
```

只验证运行栈、跳过宿主机三端测试：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\test-local.ps1 -SkipUnitTests
```

失败时脚本会输出 Compose 服务状态和末尾日志。Docker Hub 返回 `EOF` 或超时时，应先检查 Docker Desktop 的代理、镜像源和 `docker info`，然后重新执行；这类错误发生在镜像下载阶段，不是应用健康检查失败。Worker 镜像使用固定 Camoufox 浏览器版本的官方 Release 直链，避免首次构建受 GitHub 匿名 API 限流影响。

停止本地环境：

```bash
bash scripts/docker-down.sh
```

```powershell
.\scripts\docker-down.ps1
```

## 生产 Docker 启动

```bash
cp .env.production.example .env
nano .env
./scripts/deploy-production.sh
```

生产脚本使用 Docker secrets：

```text
secrets/admin_password
secrets/python_worker_token
secrets/postgres_password
secrets/database_url
```

不要在生产环境改用普通 `docker-compose.yml`，也不要把 `SUNNYREGISTER_BIND` 改成 `0.0.0.0` 后直接暴露公网。

检查生产服务：

```bash
docker compose -f docker-compose.production.yml --env-file .env config --quiet
docker compose -f docker-compose.production.yml --env-file .env ps
docker compose -f docker-compose.production.yml --env-file .env logs --tail=200
curl -fsS http://127.0.0.1:8000/api/ready
```

## 可视浏览器

后台浏览器使用 Camoufox Headless。可视浏览器使用 Xvfb 中的 Chromium；需要排查时临时开启：

```dotenv
ENABLE_NOVNC=true
```

重新部署后建立 SSH 隧道：

```bash
ssh -L 6080:127.0.0.1:6080 user@server
```

本地打开 `http://127.0.0.1:6080/vnc.html`。noVNC 当前为本机免密模式，不得绑定到公网地址，排查完成后应重新关闭。

## 宿主机本地代理

Worker 容器内的 `127.0.0.1` 不是宿主机。项目会在容器模式下将注册代理配置中的 `127.0.0.1`、`localhost` 或 `::1` 转换为 `host.docker.internal`。

Linux 宿主机上的代理服务必须监听 Docker 网桥可访问的地址。更推荐直接使用代理池中的远程代理地址。

## 日志与重启

本地环境：

```bash
docker compose logs -f sunnyregister
docker compose logs -f python-worker
docker compose restart python-worker
```

生产环境：

```bash
docker compose -f docker-compose.production.yml --env-file .env logs -f --tail=200
docker compose -f docker-compose.production.yml --env-file .env restart
```

## PostgreSQL 备份

使用 `pg_dump` 生成一致性备份：

```bash
timestamp="$(date +%Y%m%d_%H%M%S)"
mkdir -p backups
docker compose -f docker-compose.production.yml --env-file .env exec -T postgres \
  pg_dump -U "${POSTGRES_USER:-sunnyregister}" -d "${POSTGRES_DB:-sunnyregister}" -Fc \
  > "backups/sunnyregister_${timestamp}.dump"
```

更新脚本会自动执行同类备份：

```bash
./scripts/update-production.sh v0.2.0
```

数据库 volume 不等于备份。应定期把 dump 导出到加密对象存储，并进行恢复演练。SQLite 旧数据迁移见 [PostgreSQL 迁移说明](POSTGRESQL_MIGRATION.md)。

## 资源建议

| 服务器 | 建议注册并发 | `WORKER_SHM_SIZE` |
| --- | --- | --- |
| 2 核 / 4 GB | 1 | `1gb` |
| 4 核 / 8 GB | 1-2 | `1gb` 到 `2gb` |
| 8 核 / 16 GB | 3-4 | `2gb` |

实际并发还受代理质量、浏览器页面、邮箱收码和接码平台速度影响。

## 故障排查

Worker 不健康：

```bash
docker compose -f docker-compose.production.yml --env-file .env logs --tail=200 python-worker
```

检查虚拟显示：

```bash
docker compose -f docker-compose.production.yml --env-file .env exec python-worker \
  sh -c 'echo "$DISPLAY"; xdpyinfo -display "$DISPLAY" >/dev/null && echo OK'
```

检查 Worker 数据库连接标识（不会返回密码）：

```bash
docker compose -f docker-compose.production.yml --env-file .env exec python-worker \
  curl -fsS http://127.0.0.1:8765/health
```
