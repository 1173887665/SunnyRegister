# SunnyRegister 原生部署

Docker 是推荐方案。原生部署用于无法使用 Docker，或者 Windows 上需要直接看到桌面浏览器窗口的环境。

## Windows

要求：

- Windows 10/11 或 Windows Server。
- PowerShell 5.1+。
- Python 3.12+。
- Node.js 22+。
- Go 1.23+。

首次安装并启动：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start-windows.ps1
```

脚本会在缺少构建产物时自动执行 `setup-windows.ps1`。服务在后台运行，日志写入 `logs/`。

停止：

```powershell
.\scripts\stop-windows.ps1
```

源码更新后重新构建：

```powershell
.\scripts\stop-windows.ps1
.\scripts\setup-windows.ps1
.\scripts\start-windows.ps1
```

## Linux

要求：

- Python 3.12+。
- Node.js 22+。
- Go 1.23+。
- Debian/Ubuntu 推荐。
- 可执行 `playwright install --with-deps chromium` 所需的 sudo/root 权限。
- 可下载 Camoufox 运行时；安装脚本会自动执行 `python -m camoufox fetch`。

启动：

```bash
bash scripts/start-linux.sh
```

停止：

```bash
bash scripts/stop-linux.sh
```

无桌面的 Linux 会启动 Xvfb。默认同时启动本地 noVNC：

```text
http://127.0.0.1:6080/vnc.html
```

远程服务器请使用 SSH 隧道，不要直接公开 noVNC 端口。

## 运行目录

- `bin/`：Go 构建产物。
- `python-worker/.venv/`：Python 虚拟环境。
- `data/`：SQLite 与管理员密码。
- `logs/`：后端、Worker、Xvfb 和 noVNC 日志。
- `.runtime/`：PID 文件。

这些目录均已加入 `.gitignore`。
