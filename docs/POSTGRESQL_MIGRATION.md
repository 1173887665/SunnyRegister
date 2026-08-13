# PostgreSQL 部署与 SQLite 数据迁移

SunnyRegister 的运行数据库已切换为 PostgreSQL。SQLite 仅作为旧数据迁移源和测试数据库，不再支持用作后端或 Python Worker 的运行数据库。

## 推荐架构

Docker Compose 默认启动 PostgreSQL 16，并将数据库限制绑定在宿主机 `127.0.0.1:5432`。生产 Compose 不发布数据库端口，只有后端和 Worker 所在的内部网络可以访问。

数据卷：

- `sunnyregister-postgres`：PostgreSQL 数据目录。
- `sunnyregister-data`：管理员密码、审计导出等应用文件，不再保存运行数据库。

## Docker 初始化

```bash
cp .env.example .env
bash scripts/docker-up.sh
```

Windows：

```powershell
Copy-Item .env.example .env
.\scripts\docker-up.ps1
```

启动脚本会自动替换默认 PostgreSQL 密码。已有 volume 初始化后，修改 `.env` 中的密码不会自动修改数据库用户密码；需要显式执行 `ALTER USER` 或新建 volume。

## 原生 Windows / Linux

先安装 PostgreSQL 14+（推荐 16），创建用户和数据库：

```sql
CREATE USER sunnyregister WITH PASSWORD 'replace-with-a-strong-password';
CREATE DATABASE sunnyregister OWNER sunnyregister;
```

在 `.env` 配置：

```dotenv
DATABASE_URL=postgresql://sunnyregister:replace-with-a-strong-password@127.0.0.1:5432/sunnyregister?sslmode=disable
```

远程托管 PostgreSQL 应使用服务商提供的连接串，并将 `sslmode` 设为 `require` 或 `verify-full`。

## 从 SQLite 迁移

迁移前必须停止旧版 SunnyRegister 写入，并备份 SQLite 的主文件、`-wal` 和 `-shm` 文件，或先执行 SQLite 在线备份得到一致的单文件副本。

1. 创建空 PostgreSQL 数据库。
2. 用新版后端创建 schema。
3. 执行一次性迁移工具。
4. 启动新版服务并核对各页面计数。

Docker：

```bash
docker compose up -d postgres
docker compose up -d sunnyregister
docker compose run --rm -v /absolute/backup:/migration:ro python-worker \
  python /app/tools/migrate_sqlite_to_postgres.py \
  --sqlite /migration/account_manager.db \
  --postgres "postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB}?sslmode=disable"
docker compose up -d
```

Windows 原生：

```powershell
$env:DATABASE_URL = "postgresql://sunnyregister:password@127.0.0.1:5432/sunnyregister?sslmode=disable"
Push-Location backend; go run .; Pop-Location
.\python-worker\.venv\Scripts\python.exe .\python-worker\tools\migrate_sqlite_to_postgres.py `
  --sqlite C:\backup\account_manager.db `
  --postgres $env:DATABASE_URL
```

Linux 原生：

```bash
export DATABASE_URL='postgresql://sunnyregister:password@127.0.0.1:5432/sunnyregister?sslmode=disable'
(cd backend && go run .)
python-worker/.venv/bin/python python-worker/tools/migrate_sqlite_to_postgres.py \
  --sqlite /backup/account_manager.db --postgres "$DATABASE_URL"
```

默认情况下，目标任一业务表已有数据就会拒绝迁移。`--allow-non-empty` 会使用 `ON CONFLICT DO NOTHING`，仅适合明确了解冲突处理结果的恢复场景。

## 备份与恢复

Docker 备份：

```bash
mkdir -p backups
docker compose exec -T postgres pg_dump -U sunnyregister -d sunnyregister -Fc > backups/sunnyregister.dump
```

恢复到空数据库：

```bash
docker compose exec -T postgres pg_restore -U sunnyregister -d sunnyregister --clean --if-exists < backups/sunnyregister.dump
```

数据库备份包含邮箱凭据、OAuth Token 和 Session，必须加密保存并限制访问权限。
