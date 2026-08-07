# GLaDOS 多账号自动签到 · Account Center V2

这是当前仓库的 V2 自动签到后端，面向多账号独立管理，并与 macOS `GLaDOS Account Center` 配套使用。

## 当前策略

- 定时：台湾时间每天 `05:00`、`17:00`
- 时区：`Asia/Taipei`
- 每个账号使用独立的 `GLADOS_ACCOUNT_<ACCOUNT_KEY>` GitHub Actions Secret
- 现有账号自动积分兑换默认关闭
- 自动兑换必须由账号级配置显式开启
- 兑换仅使用经过验证的方案目录 `.github/glados/exchange_plans.json`
- 当前已验证方案：100→10 天、200→30 天、500→100 天
- 当前最优方案：500→100 天，即 5 积分/天

## 最优兑换规则

对所有 `verified: true` 的方案计算：

`积分成本 = points / days`

选择顺序：

1. 每天积分成本最低；
2. 成本相同，优先兑换天数更短；
3. 再相同，优先所需积分更低；
4. 再相同，按 plan ID 稳定排序。

例如未来出现 800→200 天，则 800/200=4，比 500/100=5 更划算，自动兑换门槛会改为 800；如果出现 900→180 天，则成本同为 5，仍优先 500→100 天，因为周期更短。

未验证的新方案不会自动用于兑换。积分未达到最优方案门槛时，不调用兑换接口。

## 多账号配置

非敏感配置位于：

- `.github/glados/accounts.json`
- `.github/glados/exchange_plans.json`

Cookie 只保存在 GitHub Actions Secrets，不写入仓库文件。

主要工作流：

- `.github/workflows/gladosAccounts.yml`：多账号定时/手动签到
- `.github/workflows/gladosStatus.yml`：只读状态刷新，不签到、不兑换
- `.github/workflows/gladosCheck.yml`：历史兼容工作流，自动兑换强制关闭
- `.github/workflows/ci.yml`：回归测试

## 低干扰运行策略

- `glados.cloud` 为主域；主域成功后不再访问备用域
- 仅在网络或协议故障时 fallback 到 `railgun.info`
- 401/403 立即停止
- 检测到 CAPTCHA / challenge / access denied 后停止
- 429 按限流规则等待
- GET 网络错误/5xx 仅有限重试
- 签到 POST 和兑换 POST 不自动重试，避免重复副作用
- 每个账号每次 Action 最多兑换一次

本项目不实现验证码绕过、浏览器指纹伪装、代理轮换或其他反机器人机制规避。

## 手动兼容账号

历史 `GLADOS_COOKIES` 仍由 `gladosCheck.yml` 支持。V2 对该历史入口强制关闭自动积分兑换。建议后续通过 Account Center 将账号逐个迁移到独立 Secret，便于单账号开关和状态管理。

## 测试

```bash
pip install -r requirements.txt
python -m py_compile checkin.py status.py logging_config.py
python -m unittest discover -s tests -v
```

## 回滚

V2 升级前快照已保留在分支：

`backup/pre-v2-account-center-20260807`

该分支用于恢复代码状态；GitHub Secrets 不会以明文形式写入备份分支。
