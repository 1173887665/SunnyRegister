<div align="center">

# SunnyRegister

**A GPT account registration and management tool for accounts you own and authorized testing environments**

[![CI](https://github.com/pxygit/SunnyRegister/actions/workflows/ci.yml/badge.svg)](https://github.com/pxygit/SunnyRegister/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/pxygit/SunnyRegister?display_name=tag&sort=semver)](https://github.com/pxygit/SunnyRegister/releases)
[![License](https://img.shields.io/badge/license-MIT-10b981.svg)](./LICENSE)
[![Stars](https://img.shields.io/github/stars/pxygit/SunnyRegister?style=flat)](https://github.com/pxygit/SunnyRegister/stargazers)

[简体中文](./README.md) | [English](./README_en.md)

[Features](#features) · [Quick Start](#quick-start) · [Deployment](#deployment-guides) · [Security](./SECURITY.md) · [MIT License](./LICENSE)

</div>

## Overview

SunnyRegister provides a unified web console for managing mailboxes, phone numbers, proxies, registration tasks, sessions, and reverse-proxy integrations. Registration and login workflows are executed by an isolated browser automation worker.

## Technology Stack

- **Frontend**: React, TypeScript, Vite, GSAP
- **Backend**: Go, GORM, SQLite
- **Automation worker**: Python, FastAPI, Playwright, Camoufox
- **Deployment**: Docker Compose, Xvfb, noVNC

## Features

- Outlook mailbox pool import, grouping, status management, and mail retrieval
- Batch GPT account registration or login with concurrency controls and live logs
- Self-managed phone pool plus SMSBower and SMSPool integrations
- Registration proxy pool, connectivity checks, and outbound proxy controls
- Session, access token, and account metadata management and export
- sub2api configuration, target group selection, and account import
- Simplified Chinese / English and Light / Dark modes

## Supported Platforms

- Windows 10/11
- Mainstream Linux distributions; Ubuntu 22.04/24.04 is recommended
- Docker Desktop or Linux Docker Engine with Docker Compose v2

Visible browsers run through Xvfb in Linux containers. noVNC is intended for temporary troubleshooting only and must not be exposed directly to the internet.

## Quick Start

### Docker (recommended)

Windows:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\docker-up.ps1
```

Linux:

```bash
bash scripts/docker-up.sh
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000) after startup.

### Linux production deployment

```bash
cp .env.production.example .env
# Configure your domain and review the production settings
nano .env
./scripts/deploy-production.sh
```

Production binds to `127.0.0.1:8000` by default. Publish it through a controlled HTTPS reverse proxy or tunnel.

### Native deployment

Windows:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup-windows.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\start-windows.ps1
```

Linux:

```bash
bash scripts/setup-linux.sh
bash scripts/start-linux.sh
```

## Deployment Guides

- [Docker deployment](./docs/DOCKER_DEPLOY.md)
- [Windows / Linux native deployment](./docs/NATIVE_DEPLOY.md)
- [Linux production deployment](./docs/PRODUCTION_DEPLOY.md)

## Notes

- Never commit `.env`, `secrets/`, `data/`, databases, exports, logs, or backups.
- SQLite may contain mailbox credentials, OAuth tokens, and sessions. Encrypt production disks and off-site backups.
- Public deployments require HTTPS, strong administrator credentials, and access controls. Do not expose ports `8000`, `8765`, `5900`, or `6080` directly.
- Configure proxy, mailbox, and SMS provider credentials after deployment. Never post them in source code, issues, or public logs.
- A concurrency value of 1 is recommended for browser tasks on a 2-core, 4 GB server.
- Report security issues privately according to [SECURITY.md](./SECURITY.md).

## Disclaimer

This project is intended solely for lawful account administration, automation testing, and technical research. It is not affiliated with or endorsed by OpenAI, Microsoft, SMSBower, SMSPool, sub2api, or any other third-party provider. Users must have authorization for every account, mailbox, phone number, proxy, and external service they use, and must comply with applicable laws and service terms.

The software is provided as-is, without any guarantee of availability, registration success, or permanent compatibility with third-party APIs. Users are solely responsible for account restrictions, data loss, service charges, compliance risks, and other consequences arising from its use, modification, or deployment.

## License

SunnyRegister is released under the [MIT License](./LICENSE).
