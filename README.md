# SunnyRegister

SunnyRegister 是基于 **Go + React + SQLite + Python Worker** 的 GPT 账号注册与管理系统。

项目核心能力：

- 自建 Outlook 邮箱池管理、分组、状态维护与邮件查询。
- 工作台批量注册 / 登录 ChatGPT 账号，并统一管理 Session、Access Token、套餐类型与账户状态。
- 自建手机号池与接码配置，为后续 Codex 接码绑定阶段提供资源管理。
- sub2api 反代平台配置与账号导入能力。
- 代理池管理，支持注册机对外请求走代理池或服务器系统出口。
- Light / Dark 主题与中文简体 / English 国际化切换。

## 本地启动

建议启动顺序：

1. Python Worker
2. Go 后端
3. React 前端

### 1. 初始化并启动 Python Worker

首次运行：

```powershell
cd D:\Scripts\auto-gpt-register
.\scripts\setup-python-worker.ps1
.\scripts\start-python-worker.ps1
```

后续只需要：

```powershell
cd D:\Scripts\auto-gpt-register
.\scripts\start-python-worker.ps1
```

Worker 默认地址：

```text
http://127.0.0.1:8765
```

### 2. 启动 Go 后端

```powershell
cd D:\Scripts\auto-gpt-register\backend
$env:PYTHON_WORKER_URL="http://127.0.0.1:8765"
$env:PYTHON_TASK_TYPES="sunny_register,sunny_login,sunny_refresh_session"
$env:ACCOUNT_MANAGER_DATABASE_URL="sqlite:///../data/account_manager.db"
go run .
```

后端地址：

```text
http://localhost:8000
```

### 3. 启动 React 前端

```powershell
cd D:\Scripts\auto-gpt-register\frontend
npm install
npm run dev
```

前端开发地址通常为：

```text
http://localhost:5173
```

## Docker 部署

```bash
git clone https://github.com/<your-org>/SunnyRegister.git
cd SunnyRegister
cp .env.production.example .env
# 修改 .env 中的 ADMIN_PASSWORD / PYTHON_WORKER_TOKEN
docker compose up -d --build
```

访问：

```text
http://服务器IP:8000
```

Linux 云服务器无桌面环境时，Python Worker 会在容器内通过 `Xvfb + noVNC` 提供虚拟浏览器窗口。默认 noVNC 只监听容器宿主机 `127.0.0.1:6080`，推荐使用 SSH 隧道查看：

```bash
ssh -L 6080:127.0.0.1:6080 root@服务器IP
```

然后打开：

```text
http://127.0.0.1:6080/vnc.html
```

完整部署说明见：[`docs/DOCKER_DEPLOY.md`](docs/DOCKER_DEPLOY.md)

## 默认账号

默认管理员账号：

```text
admin
```

如果未设置 `ADMIN_PASSWORD`，首次启动会自动生成密码。

本地模式密码文件：

```text
D:\Scripts\auto-gpt-register\data\admin_password.txt
```

Docker 模式查看：

```bash
docker compose exec sunnyregister cat /app/data/admin_password.txt
```

## 注意

- `.env`、数据库、日志、虚拟环境、构建产物与本地 Agent 工作目录不会提交到仓库。
- 首次启动前请根据 `.env.example` 或 `.env.production.example` 配置必要环境变量。
