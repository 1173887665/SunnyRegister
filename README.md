# SunnyRegister

SunnyRegister 是基于 **Go + React + SQLite + Python Worker + Playwright/Camoufox** 的 GPT 账号注册与管理系统。

## 功能概览

- Outlook 自建邮箱池、分组、状态、套餐类型与邮件查询。
- ChatGPT 账号批量注册/登录、并发控制、实时日志与任务中断。
- 自建手机号池、SMSBower、SMSPool 接码配置。
- sub2api 反代导入、代理池与 Session 管理。
- Light/Dark 主题以及简体中文/English 切换。

## 系统架构

```text
Browser
   |
   | HTTPS: sunnyregister.xingyu013.work
   v
Cloudflare Tunnel / 服务器现有反向代理
   |
   | HTTP: 127.0.0.1:8000
   v
SunnyRegister Go + React
   |
   +---- SQLite volume
   |
   +---- Python Worker (Docker internal network: 8765)
             |
             +---- Camoufox / Playwright / Xvfb
```

生产环境不需要 Caddy。SunnyRegister 默认只监听服务器本机 `127.0.0.1:8000`，由 Cloudflare Tunnel 或服务器上已有的反向代理接入。

> Cloudflare DNS 记录不能把普通的 `https://subdomain.example.com` 直接映射到任意 `8000` 端口。应使用 Cloudflare Tunnel 的 Published application，或复用服务器上当前为 OpenClaw、sub2api 提供服务的反向代理。

## 生产部署

以下流程以 `sunnyregister.xingyu013.work` 为生产域名，不会影响现有的：

- `openclaw.xingyu013.work`
- `www.xingyu013.work`

### 1. 服务器要求

- 推荐 Ubuntu 22.04/24.04 或其他主流 Linux 发行版。
- 最低建议 `2 CPU / 4 GB RAM`；批量浏览器任务建议更高配置。
- 已安装 Git、OpenSSL、Docker Engine 和 Docker Compose v2。

确认环境：

```bash
git --version
openssl version
docker --version
docker compose version
docker info
```

### 2. 从私有 GitHub 仓库拉取

开源前先使用私有仓库。服务器建议配置只读 GitHub Deploy Key，然后执行：

```bash
sudo mkdir -p /opt/sunnyregister
sudo chown "$USER":"$USER" /opt/sunnyregister
git clone git@github.com:pxygit/SunnyRegister.git /opt/sunnyregister
cd /opt/sunnyregister
```

GitHub 并不是运行 SunnyRegister 的必要条件，但使用私有仓库可以简化版本更新、回滚和后续开源流程。

### 3. 配置生产环境

```bash
cp .env.production.example .env
nano .env
```

默认配置已经适配当前域名：

```dotenv
SUNNYREGISTER_DOMAIN=sunnyregister.xingyu013.work
SUNNYREGISTER_BIND=127.0.0.1
SUNNYREGISTER_PORT=8000
SUNNYREGISTER_PUBLIC_CHECK=false
ADMIN_USERNAME=sunnyadmin
TZ=Asia/Shanghai
```

说明：

- 不要把 `SUNNYREGISTER_BIND` 改成 `0.0.0.0`，除非已经通过防火墙严格限制来源。
- `SUNNYREGISTER_PUBLIC_CHECK=false` 表示部署时只检查本机服务。Cloudflare 路由配置完成后可改为 `true`。
- 管理员密码和 Python Worker Token 不写入 `.env`，部署脚本会生成到被 Git 忽略的 `secrets/`。
- `SUNNY_ENV=production` 下如果密钥过短或仍是占位值，后端会拒绝启动。

### 4. 启动 Docker 服务

```bash
chmod +x scripts/*.sh
./scripts/deploy-production.sh
```

部署脚本会：

1. 生成强管理员密码和 Worker Token。
2. 校验生产 Compose 配置。
3. 构建 Go/React 与 Python Worker 镜像。
4. 启动容器并等待健康检查。
5. 验证 `http://127.0.0.1:8000/api/ready`。

检查服务：

```bash
docker compose -f docker-compose.production.yml --env-file .env ps
docker compose -f docker-compose.production.yml --env-file .env logs --tail=200
curl -fsS http://127.0.0.1:8000/api/ready
```

查看首次生成的管理员密码：

```bash
cat /opt/sunnyregister/secrets/admin_password
```

请立即将该密码保存到密码管理器，不要通过聊天、Issue 或日志发送。

### 5. 配置 Cloudflare 子域名

#### 方式 A：复用现有 Cloudflare Tunnel（推荐）

如果 OpenClaw 或 sub2api 已经使用 Cloudflare Tunnel，无需新建 Tunnel。在 Cloudflare Zero Trust 中找到该 Tunnel，新增 Published application。

当 `cloudflared` 作为宿主机 systemd 服务运行时：

```text
Subdomain: sunnyregister
Domain:    xingyu013.work
Type:      HTTP
Service:   http://127.0.0.1:8000
```

当 `cloudflared` 自身运行在 Docker 容器中，容器里的 `127.0.0.1` 指向 cloudflared 容器，不是宿主机。生产 Compose 会创建固定名称的 `sunnyregister-edge` 网络。将现有 cloudflared 容器加入该网络，然后设置：

```text
Subdomain: sunnyregister
Domain:    xingyu013.work
Type:      HTTP
Service:   http://sunnyregister:8000
```

如果 cloudflared 使用 Compose 管理，在其 Compose 中加入：

```yaml
services:
  cloudflared:
    networks:
      - sunnyregister-edge

networks:
  sunnyregister-edge:
    external: true
```

临时验证也可以执行：

```bash
docker network connect sunnyregister-edge <cloudflared容器名>
```

长期运行应修改 cloudflared 自身的 Compose，避免其容器重建后丢失网络连接。`sunnyregister-edge` 只连接 Go Web 服务，Python Worker 位于独立的 `sunnyregister-worker` 网络。

Cloudflare Tunnel 使用出站连接，不需要向公网开放 `8000` 端口。

#### 方式 B：复用服务器现有反向代理

如果 `openclaw.xingyu013.work` 和 `www.xingyu013.work` 是通过 Nginx、Traefik、1Panel、宝塔等现有反向代理发布，则按相同方式新增站点。反向代理运行在宿主机时：

```text
Hostname: sunnyregister.xingyu013.work
Upstream: http://127.0.0.1:8000
```

Cloudflare 中新增对应的代理 DNS 记录，并保持现有服务器使用的 SSL/TLS 模式。推荐使用 `Full (strict)`，前提是现有反向代理已经配置有效的源站证书。

反向代理自身运行在 Docker 中时，也可以加入 `sunnyregister-edge` 外部网络，并把 Upstream 改为 `http://sunnyregister:8000`。

不要直接把公网 URL 配置成 `https://sunnyregister.xingyu013.work:8000`，也不要将 `8000` 暴露给整个互联网。

### 6. 验证公网访问

```bash
curl -fsS https://sunnyregister.xingyu013.work/api/ready
```

浏览器打开：

```text
https://sunnyregister.xingyu013.work
```

公网路由验证通过后，可在 `.env` 中设置：

```dotenv
SUNNYREGISTER_PUBLIC_CHECK=true
```

后续部署和更新会同时检查本机服务与 Cloudflare 公网域名。

## 日常运维

### 查看日志

```bash
docker compose -f docker-compose.production.yml --env-file .env logs -f --tail=200
docker compose -f docker-compose.production.yml --env-file .env logs -f python-worker
docker compose -f docker-compose.production.yml --env-file .env logs -f sunnyregister
```

### 重启

```bash
docker compose -f docker-compose.production.yml --env-file .env restart
```

### 停止

```bash
docker compose -f docker-compose.production.yml --env-file .env down
```

不要添加 `-v`，否则会删除包含 SQLite 数据的 Docker volume。

### 可视浏览器与 noVNC

生产环境默认关闭 noVNC。需要排查可视浏览器时，在 `.env` 中临时设置：

```dotenv
ENABLE_NOVNC=true
```

重新部署后，从本地建立 SSH 隧道：

```bash
ssh -L 6080:127.0.0.1:6080 user@your-server
```

本地访问 `http://127.0.0.1:6080/vnc.html`。排查完成后重新关闭 noVNC。

## 更新与回滚

推荐使用版本 Tag：

```bash
cd /opt/sunnyregister
./scripts/update-production.sh v0.2.0
```

快速跟随主分支：

```bash
./scripts/update-production.sh origin/main
```

更新脚本会：

1. 拒绝覆盖存在本地改动的部署目录。
2. 使用 SQLite Backup API 创建一致性备份。
3. 拉取并切换到指定 Tag 或提交。
4. 使用 Docker 缓存增量构建。
5. 执行健康检查。
6. 新版本失败时自动切回旧提交并重新部署。

备份文件保存在 `sunnyregister-data` volume 内。仍应定期将备份同步到加密对象存储，并实际演练恢复。

## 数据与安全

- `.env`、`secrets/`、`data/`、数据库、日志和备份均不得提交到 Git。
- SQLite 中包含邮箱口令、OAuth Session 和 Token，服务器磁盘与异地备份必须加密。
- 管理端使用限速登录和 `HttpOnly + Secure + SameSite=Strict` 会话 Cookie。
- Python Worker 只存在于 Docker 内部网络，并通过独立随机 Token 鉴权。
- OTP、Access Token、Refresh Token 和代理口令不会写入任务事件详情。
- Cloudflare Tunnel 场景下只需保留 SSH 管理端口；不要开放 `8000`、`8765`、`5900` 或 `6080`。
- 建议在 `sunnyregister.xingyu013.work` 前增加 Cloudflare Access 策略，仅允许指定身份并启用 MFA；应用自身的管理员登录作为第二层校验。

## 开源前检查

当前仓库旧历史包含后来删除的 `original_runtime`。公开仓库时应建立全新的干净仓库，只提交当前净化后的快照，不要直接公开当前 Git 历史。

执行发布检查：

```powershell
powershell -File scripts/preflight-release.ps1 -RunBuild
```

公开前还应：

- 选择并添加项目许可证。
- 核对所有参考项目、迁移代码和依赖的许可证与署名要求。
- 开启 GitHub Secret Scanning、Push Protection、Dependabot 和 CodeQL。
- 删除测试截图、导出文件、真实邮箱、任务日志和数据库。
- 轮换任何曾经进入 Git 历史、终端输出或聊天记录的凭据。

安全问题请按照 [SECURITY.md](SECURITY.md) 通过 GitHub Security Advisory 私下报告。

## 本地开发

- Docker 开发部署：[docs/DOCKER_DEPLOY.md](docs/DOCKER_DEPLOY.md)
- 原生环境部署：[docs/NATIVE_DEPLOY.md](docs/NATIVE_DEPLOY.md)
- 生产环境细节：[docs/PRODUCTION_DEPLOY.md](docs/PRODUCTION_DEPLOY.md)
- 系统架构：[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
