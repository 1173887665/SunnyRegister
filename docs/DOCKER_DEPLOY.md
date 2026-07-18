# SunnyRegister Docker 使用说明

SunnyRegister 提供两份 Compose：

| 文件 | 用途 | Web 绑定 |
| --- | --- | --- |
| `docker-compose.yml` | 本地开发、局域网试用 | 默认 `0.0.0.0:8000` |
| `docker-compose.production.yml` | 云服务器生产部署 | 默认 `127.0.0.1:8000` |

生产服务器必须使用 `docker-compose.production.yml`。完整 Cloudflare 接入流程见根目录 [README.md](../README.md)。

## 服务结构

| 服务 | 内容 | 网络边界 |
| --- | --- | --- |
| `sunnyregister` | Go API + React 静态前端 | 开发环境发布 8000；生产环境仅回环地址 |
| `python-worker` | FastAPI + Camoufox + Playwright | 只在 `sunnyregister-worker` 网络提供 8765 |
| noVNC | Xvfb 虚拟桌面 | 默认关闭；启用后仅 `127.0.0.1:6080` |
| `sunnyregister-data` | SQLite 与运行数据 | Docker named volume |

Go 与 Python Worker 共享 `/app/data/account_manager.db`。容器重建不会删除 named volume，除非显式执行 `down -v`。

## 本地 Docker 启动

Windows Docker Desktop：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\docker-up.ps1
```

Linux：

```bash
bash scripts/docker-up.sh
```

脚本会创建本地 `.env`、生成管理员密码与 Worker Token、构建镜像并等待健康检查。启动后访问：

```text
http://127.0.0.1:8000
```

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

## SQLite 在线备份

不要在服务运行时使用普通 `cp` 复制 SQLite 主文件。使用 Python SQLite Backup API：

```bash
timestamp="$(date +%Y%m%d_%H%M%S)"
docker compose -f docker-compose.production.yml --env-file .env exec -T python-worker \
  python -c "import sqlite3; s=sqlite3.connect('/app/data/account_manager.db'); d=sqlite3.connect('/app/data/backup_${timestamp}.db'); s.backup(d); d.close(); s.close()"
```

更新脚本会自动执行同类备份：

```bash
./scripts/update-production.sh v0.2.0
```

volume 内备份不等于异地备份。应定期导出到加密对象存储，并进行恢复演练。

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

检查 Worker 数据库路径：

```bash
docker compose -f docker-compose.production.yml --env-file .env exec python-worker \
  curl -fsS http://127.0.0.1:8765/health
```
