# Sub2API 账号导入

## 学到的 Sub2API 管理接口

根据 `Wei-Shaw/sub2api` 官方仓库源码：

- 管理员路由前缀：`/api/v1/admin`
- 管理员认证：
  - Admin API Key：请求头 `x-api-key: <admin-api-key>`
  - 管理员 JWT：请求头 `Authorization: Bearer <jwt-token>`
- 查询分组：`GET /api/v1/admin/groups/all?platform=openai&include_inactive=true`
- 批量创建账号：`POST /api/v1/admin/accounts/batch`
- 批量创建请求体：

```json
{
  "accounts": [
    {
      "name": "user@example.com",
      "platform": "openai",
      "type": "oauth",
      "credentials": {
        "access_token": "...",
        "refresh_token": "...",
        "id_token": "...",
        "client_id": "app_EMoamEEZ73f0CkXaXp7hrann",
        "chatgpt_account_id": "...",
        "organization_id": "...",
        "expires_at": 1999999999
      },
      "extra": {
        "import_source": "aBaiAutoplus"
      },
      "group_ids": [1],
      "concurrency": 10,
      "priority": 1,
      "rate_multiplier": 1,
      "auto_pause_on_expired": true,
      "confirm_mixed_channel_risk": true
    }
  ]
}
```

`group_ids` 是把账号导入指定分组的关键字段。

## 本项目提供的封装接口

```http
POST /api/integrations/sub2api/import
Authorization: Bearer <本项目管理员登录 token>
Content-Type: application/json
```

请求体：

```json
{
  "base_url": "https://your-sub2api.example.com",
  "admin_token": "sub2api-admin-api-key-or-jwt",
  "auth_header": "x-api-key",
  "group_id": "1",
  "group_name": "OpenAI 默认分组",
  "ids": [1, 2, 3],
  "select_all": false,
  "platform": "chatgpt",
  "target_platform": "openai",
  "concurrency": "10",
  "priority": "1",
  "rate_multiplier": "1",
  "dry_run": false
}
```

说明：

- `group_id` 优先；未填时会用 `group_name` 调 Sub2API 分组接口查询 ID。
- `ids` 为空且 `select_all=true` 时，会按当前平台/状态/搜索条件批量选择。
- `dry_run=true` 时只生成脱敏后的 Sub2API 请求预览，不会调用远程平台。
- 前端入口：账号池页（ChatGPT 标签）顶部工具栏 **“导入 Sub2Api 平台”**。

## 配置项

也可以通过本项目 `/api/config` 保存默认值：

- `sub2api_base_url`
- `sub2api_admin_token`
- `sub2api_auth_header`
- `sub2api_group_id`
- `sub2api_group_name`
- `sub2api_batch_endpoint`

## curl 示例

```bash
curl -X POST http://localhost:8000/api/integrations/sub2api/import \
  -H "Authorization: Bearer $LOCAL_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "base_url":"https://sub2api.example.com",
    "admin_token":"SUB2API_ADMIN_API_KEY",
    "auth_header":"x-api-key",
    "group_id":"1",
    "ids":[1],
    "dry_run":true
  }'
```
