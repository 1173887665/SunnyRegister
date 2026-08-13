<div align="center">

# SunnyRegister

**面向自有账号与授权测试场景的 GPT 账号注册与管理工具**

[![CI](https://github.com/pxygit/SunnyRegister/actions/workflows/ci.yml/badge.svg)](https://github.com/pxygit/SunnyRegister/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-MIT-10b981.svg)](./LICENSE)
[![Go](https://img.shields.io/badge/Go-1.23%2B-00ADD8?logo=go&logoColor=white)](https://go.dev/)
[![React](https://img.shields.io/badge/React-19-61DAFB?logo=react&logoColor=111111)](https://react.dev/)
[![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-4169E1?logo=postgresql&logoColor=white)](https://www.postgresql.org/)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)](https://docs.docker.com/compose/)
[![Playwright](https://img.shields.io/badge/Playwright-1.61%2B-2EAD33?logo=playwright&logoColor=white)](https://playwright.dev/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110%2B-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![GSAP](https://img.shields.io/badge/GSAP-3-88CE02?logo=greensock&logoColor=111111)](https://gsap.com/)

[简体中文](./README.md) | [English](./README_en.md)

[主要功能](#主要功能) · [快速部署](#快速部署) · [部署文档](#部署文档) · [安全说明](./SECURITY.md) · [MIT License](./LICENSE)

</div>

## 项目简介

SunnyRegister 通过统一的 Web 控制台管理邮箱、手机号、代理、注册任务、Session 和反代配置，并由独立的浏览器自动化 Worker 执行注册与登录流程。

## 技术栈

- **前端**：React、TypeScript、Vite、GSAP
- **后端**：Go、GORM、PostgreSQL
- **自动化 Worker**：Python、FastAPI、curl_cffi、Playwright、Camoufox
- **部署运行**：Docker Compose、Xvfb、noVNC

## 主要功能

- Outlook/Hotmail 邮箱池导入、分组、状态管理与邮件查询，兼容 Graph API、IMAP/POP3 与 Graph/IMAP 双令牌凭证
- GPT 账号批量注册或登录、并发控制、实时日志与任务中断
- 纯 HTTP/TLS 协议注册优先并减少页面资源流量；当前用于“仅注册 ChatGPT”阶段，远端要求浏览器挑战时仅该协议任务自动切换到 Camoufox 后台无头浏览器继续，原后台/可视浏览器模式互不影响
- 自建手机号池以及 SMSBower、SMSPool 接码配置
- 注册流量代理池、可用性检测和出站代理控制
- Session、Access Token 和账号资料管理与导出
- sub2api 配置、目标分组选择与账号导入
- 简体中文 / English、Light / Dark 模式

## 支持平台

- Windows 10/11
- 主流 Linux 发行版，推荐 Ubuntu 22.04/24.04
- Docker Desktop 或 Linux Docker Engine + Docker Compose v2

Linux 容器中的可视浏览器通过 Xvfb 运行。noVNC 仅用于临时排查，不应直接暴露到公网。

## 快速部署

### Docker（推荐）

Windows：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\docker-up.ps1
```

Linux：

```bash
bash scripts/docker-up.sh
```

启动后访问 [http://127.0.0.1:8000](http://127.0.0.1:8000)。

### Linux 生产部署

```bash
cp .env.production.example .env
# 修改域名及其他生产配置
nano .env
./scripts/deploy-production.sh
```

生产环境默认只监听 `127.0.0.1:8000`，请通过受控的反向代理或隧道提供 HTTPS 访问。

### 原生部署

Windows：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup-windows.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\start-windows.ps1
```

Linux：

```bash
bash scripts/setup-linux.sh
bash scripts/start-linux.sh
```

## 部署文档

- [Docker 部署](./docs/DOCKER_DEPLOY.md)
- [Windows / Linux 原生部署](./docs/NATIVE_DEPLOY.md)
- [Linux 生产部署](./docs/PRODUCTION_DEPLOY.md)
- [PostgreSQL 部署与 SQLite 数据迁移](./docs/POSTGRESQL_MIGRATION.md)

## 注意事项

- `.env`、`secrets/`、`data/`、数据库、导出文件、日志和备份不得提交到 Git。
- PostgreSQL 中可能包含邮箱凭据、OAuth Token 和 Session，生产磁盘及异地备份应加密。
- 公网部署必须启用 HTTPS、强管理员密码和访问控制，不要直接开放 `8000`、`8765`、`5900`、`6080` 等端口。
- 代理、邮箱和接码平台凭据应在部署后配置，不要写入源码、Issue 或公开日志。
- 2 核 4 GB 服务器建议浏览器任务并发设为 1，实际容量取决于浏览器、代理和邮箱服务质量。
- 安全问题请按 [SECURITY.md](./SECURITY.md) 私下报告。

## 免责声明

本项目仅用于合法的账号管理、自动化测试和技术研究，不隶属于或受 OpenAI、Microsoft、SMSBower、SMSPool、sub2api 等第三方服务认可。使用者必须确保对所使用的账号、邮箱、手机号、代理和外部服务拥有合法授权，并遵守所在地法律及各平台服务条款。

项目按现状提供，不保证持续可用、注册成功率或与第三方接口永久兼容。因使用、修改或部署本项目产生的账号限制、数据丢失、服务费用、合规风险及其他后果由使用者自行承担。

## 许可证

本项目基于 [MIT License](./LICENSE) 开源。
