# SunnyRegister 生产部署说明

生产环境采用以下边界：

```text
Cloudflare Tunnel / 现有反向代理
                |
                v
       127.0.0.1:8000
                |
                v
      SunnyRegister Docker Compose
```

本项目不启动 Caddy、Nginx 或 cloudflared。域名入口交给服务器现有的 Cloudflare Tunnel 或反向代理管理。

## 生产配置

复制配置模板：

```bash
cp .env.production.example .env
```

关键配置：

```dotenv
SUNNYREGISTER_DOMAIN=register.example.com
SUNNYREGISTER_BIND=127.0.0.1
SUNNYREGISTER_PORT=8000
SUNNYREGISTER_PUBLIC_CHECK=false
ADMIN_USERNAME=admin
```

The registration task proxy is used for Outlook IMAP before trying a direct
IPv4 connection. To override it, configure a dedicated HTTP CONNECT or SOCKS5
proxy in `.env`:

```dotenv
OUTLOOK_IMAP_DIRECT_FIRST=false
OUTLOOK_IMAP_PROXY=socks5://user:password@proxy.example.com:1080
```

The registration task proxy is used as a fallback when no dedicated IMAP proxy
is configured. Many rotating HTTP proxies only allow CONNECT to port 443, so a
SOCKS5 proxy or an outbound firewall rule for `outlook.office365.com:993` is
preferred.

`SUNNYREGISTER_BIND=127.0.0.1` 是生产安全边界。Cloudflare Tunnel 以宿主机方式运行时，Published application 的 Service 设置为 `http://127.0.0.1:8000`。

## 首次部署

```bash
./scripts/deploy-production.sh
```

管理员密码与 Worker Token 生成在：

```text
secrets/admin_password
secrets/python_worker_token
```

这两个文件权限为 `0600`，且已被 Git 和 Docker 构建上下文忽略。

## Cloudflare 接入

推荐在现有 Tunnel 中新增：

```text
Public hostname: register.example.com
Service:         http://127.0.0.1:8000
```

如果 cloudflared 运行在 Docker 中，不能使用容器自身的 `127.0.0.1`。生产 Compose 会创建 `sunnyregister-edge` 网络；将 cloudflared 加入该网络后，Service 使用 `http://sunnyregister:8000`。不要使用宿主机网关绕过本项目的回环地址绑定。

如果服务器已有 Nginx、Traefik、1Panel 或宝塔，则新增反向代理到 `http://127.0.0.1:8000`，Cloudflare 继续使用现有代理 DNS 和 SSL/TLS 策略。反向代理运行在 Docker 中时，将其加入 `sunnyregister-edge` 并使用 `http://sunnyregister:8000`。

## 更新

```bash
./scripts/update-production.sh v0.2.0
```

或：

```bash
./scripts/update-production.sh origin/main
```

更新前会执行 SQLite 在线备份。部署后会检查容器健康和本机端口；当 `SUNNYREGISTER_PUBLIC_CHECK=true` 时，还会检查 Cloudflare 公网域名。失败时自动回退到更新前提交。

## 备份

SQLite 与配置保存在 `sunnyregister-data` volume。更新脚本生成的 `backup_*.db` 也位于该 volume 中。生产环境还必须配置加密异地备份，并定期进行恢复演练。

## 上线检查

```bash
docker compose -f docker-compose.production.yml --env-file .env config --quiet
docker compose -f docker-compose.production.yml --env-file .env ps
curl -fsS http://127.0.0.1:8000/api/ready
curl -fsS https://register.example.com/api/ready
```

不要对公网开放 `8000`、`8765`、`5900` 或 `6080`。
