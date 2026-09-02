# MoMo Gateway Adapter

这个服务把两类不同方向的请求分开：

- `POST /webhooks/sunny`：作为 SunnyRegister“账号回调 URL”，验证 `X-Sunny-Signature` 后接收账号事件。
- `POST /payments/momo/create`：按 MoMo 商户 API 规范生成 HMAC-SHA256 签名并创建支付。
- `POST /payments/momo/ipn`：接收 MoMo 的支付结果通知并验证 MoMo 签名。

不要把 `https://payment.momo.vn/v2/gateway/api/create` 填到 SunnyRegister 的账号回调 URL；它是创建支付的商户 API，不是通用 Webhook 接收器。

## 配置

PowerShell：

```powershell
$env:MOMO_PARTNER_CODE = "PARTNER_CODE"
$env:MOMO_ACCESS_KEY = "ACCESS_KEY"
$env:MOMO_SECRET_KEY = "SECRET_KEY"
$env:MOMO_IPN_URL = "https://merchant.example/payments/momo/ipn"
$env:MOMO_REDIRECT_URL = "https://merchant.example/payments/momo/return"
$env:MOMO_ADAPTER_API_TOKEN = "LOCAL_API_TOKEN"
$env:SUNNY_WEBHOOK_SECRET = "与 SunnyRegister 回调配置相同的 Secret"

# 默认连接测试环境；正式环境审核完成后再设置：
# $env:MOMO_BASE_URL = "https://payment.momo.vn"

go run ./cmd/momo-gateway-adapter
```

反向代理应把公网路径转发到本机 `127.0.0.1:8788`。随后在 SunnyRegister 的“账号回调设置”中填写：

```text
https://merchant.example/webhooks/sunny
```

## 创建测试支付

```powershell
$headers = @{ Authorization = "Bearer LOCAL_API_TOKEN" }
$body = @{
  amount = 1000
  order_info = "SunnyRegister test order"
  extra_data = @{ source = "sunnyregister" }
  lang = "en"
} | ConvertTo-Json

Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8788/payments/momo/create" `
  -Headers $headers `
  -ContentType "application/json" `
  -Body $body
```

成功响应中的 `payUrl` 用于浏览器跳转；`qrCodeUrl` 是二维码内容，不是图片 URL。

商户密钥只通过环境变量注入，不写入源码、日志或前端代码。`/payments/momo/ipn` 中已留出订单持久化位置，接入正式业务时应按 `orderId` 做幂等更新。
