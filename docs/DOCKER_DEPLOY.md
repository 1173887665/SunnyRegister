# SunnyRegister Docker 一键部署指南

本项目的服务架构固定为：

- `sunnyregister`：Go 后端 + React 静态前端，端口 `8000`。
- `python-worker`：Python Worker + Playwright Chromium，用于注册/登录浏览器自动化，端口 `8765` 仅容器内访问。
- `sunnyregister-data`：Docker volume，保存 SQLite 数据库、管理员密码和运行数据。
- `noVNC`：Python Worker 内置的浏览器远程查看入口，默认映射到宿主机 `127.0.0.1:6080`。

## 1. 服务器准备

Linux 云服务器建议最低配置：

- 2 核 CPU / 4GB 内存；并发注册较高时建议 4 核 / 8GB。
- Docker Engine 24+ 与 Docker Compose v2。
- 出站网络可访问 ChatGPT、Outlook、接码供应商、sub2api 等目标服务。

安装 Docker 后，拉取源码：

```bash
git clone https://github.com/<your-org>/SunnyRegister.git
cd SunnyRegister
cp .env.production.example .env
```

编辑 `.env`，至少修改：

```env
ADMIN_PASSWORD=请改成强密码
PYTHON_WORKER_TOKEN=请改成随机长字符串
TZ=Asia/Shanghai
```

## 2. 一键启动

```bash
docker compose up -d --build
```

查看状态：

```bash
docker compose ps
docker compose logs -f sunnyregister
docker compose logs -f python-worker
```

访问控制台：

```text
http://服务器IP:8000
```

如果没有设置 `ADMIN_PASSWORD`，首次启动会自动生成密码。查看方式：

```bash
docker compose exec sunnyregister cat /app/data/admin_password.txt
```

## 3. Linux 服务器上的“可视浏览器”问题

Windows 开发环境中，Playwright 启动的是本机真实桌面窗口；Linux 云服务器通常没有桌面显示器，直接启动可视浏览器会失败。

本项目 Docker 方案已经内置解决方案：

1. `python-worker` 容器启动 `Xvfb`，提供虚拟显示器 `DISPLAY=:99`。
2. Playwright 的可视 Chromium 会运行在这个虚拟显示器里。
3. 容器同时启动 `x11vnc + noVNC`，你可以通过浏览器远程查看这个虚拟桌面。

默认 noVNC 只绑定服务器本机 `127.0.0.1:6080`，避免公网裸露。推荐使用 SSH 隧道：

```bash
ssh -L 6080:127.0.0.1:6080 root@你的服务器IP
```

然后在本机浏览器打开：

```text
http://127.0.0.1:6080/vnc.html
```

这样注册任务仍然可以选择“可视浏览器自动”，浏览器窗口会出现在 noVNC 页面中。遇到人机验证或需要人工介入时，也可以在 noVNC 中处理。

> 不建议把 `NOVNC_BIND` 改成 `0.0.0.0` 后直接公网暴露，因为 noVNC 默认没有登录鉴权。若必须公网访问，请放到带鉴权的反向代理后面。

## 4. 常用运维命令

更新代码并重启：

```bash
git pull
docker compose up -d --build
```

停止：

```bash
docker compose down
```

停止并删除数据卷（会清空数据库，谨慎执行）：

```bash
docker compose down -v
```

备份 SQLite 数据：

```bash
mkdir -p backups
docker compose exec sunnyregister sh -lc 'cp /app/data/account_manager.db /tmp/account_manager.db'
docker cp sunnyregister-go:/tmp/account_manager.db ./backups/account_manager-$(date +%Y%m%d-%H%M%S).db
```

## 5. 端口与环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `SUNNYREGISTER_PORT` | `8000` | Web 控制台端口 |
| `ADMIN_USERNAME` | `admin` | 管理员账号 |
| `ADMIN_PASSWORD` | 空 | 管理员密码，空则自动生成 |
| `PYTHON_WORKER_TOKEN` | 空 | Go 后端与 Worker 的内部鉴权 Token，生产建议必填 |
| `TZ` | `Asia/Shanghai` | 容器时区 |
| `ENABLE_XVFB` | `true` | 启用虚拟显示器 |
| `ENABLE_NOVNC` | `true` | 启用 noVNC 远程查看 |
| `NOVNC_BIND` | `127.0.0.1` | noVNC 端口绑定地址 |
| `NOVNC_PORT` | `6080` | noVNC 端口 |
| `XVFB_WHD` | `1600x900x24` | 虚拟屏幕尺寸 |

## 6. GitHub 上传注意事项

已经提供 `.gitignore` 与 `.dockerignore`，会排除：

- `.env`、数据库、运行日志。
- 本地虚拟环境、Node 依赖、编译产物。
- Windows `.exe` 本地构建文件。

上传前建议检查：

```bash
git status --ignored
```

确认没有把 `data/`、`.env`、数据库和真实 Token 提交到仓库。
