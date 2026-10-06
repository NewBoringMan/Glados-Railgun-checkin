# Security Model

## 敏感数据

GLaDOS Cookie 是敏感登录凭据。本项目只读取：

- `koa:sess`
- `koa:sess.sig`

规则：

- 不在 UI 显示完整 Cookie；
- 不写入 APP 状态缓存；
- 不写入 `accounts.json`；
- 不写入 workflow；
- 不写入普通日志；
- 不进入命令行参数；
- 写 GitHub Secret 时通过 `gh secret set ...` 的 stdin 传入；
- 看板日常刷新不需要把 Secret 拉回本机，而是通过只读 GitHub Actions 使用 Secret。

## GitHub

账号 Cookie 分离存储为：

`GLADOS_ACCOUNT_<16_HEX_ACCOUNT_KEY>`

新增/更新一个账号不会读取、合并或覆盖其他账号 Secret。

## 浏览器权限

Safari 扩展只允许：

- `https://glados.cloud/*`
- `https://*.glados.cloud/*`
- `https://railgun.info/*`
- `https://*.railgun.info/*`

不请求 `<all_urls>`。

## 自动化边界

不实现：

- CAPTCHA / challenge 绕过；
- 浏览器指纹欺骗；
- IP/代理轮换；
- 反自动化检测规避；
- 任意站点 Cookie 导出；
- 任意 shell 命令执行入口。

## Fail-Closed

- 未验证积分方案不能自动兑换；
- 兑换方案目录异常时自动兑换不运行；
- 401/403 和验证挑战立即停止；
- 积分未知时不兑换；
- 自动兑换关闭时，即使积分充足也不调用兑换接口。
