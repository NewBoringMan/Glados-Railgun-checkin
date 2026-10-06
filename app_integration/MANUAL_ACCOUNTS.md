# GLaDOS Account Center 2.0.10：恢复手动登录管理

## 此次范围

此版本恢复同一个 Account Center 的手动登录管理，移除自动收验证码、自动登录和自动更新 Cookie 的启动链与界面。保留账号清单、每日签到、独立账号开关、每账号兑换策略、全局兑换档位、连续签到、日历、运行记录、台北时区排程、独立 Edge 资料目录和内嵌 Safari 扩展。

完整原生源恢复在 `macos/Sources`，手动读取资源在 `macos/Resources`。原单 App 的 bundle identifier 不变。主程序从源构建为 `GLaDOSAccountCenter.real`，沿用原 launcher 和独立的 PolicyMenuPlugin / PolicyEditor，避免丢失已有的每账号兑换入口。

## 用户可见变化

1. 概览、账号、日历和兑换策略窗口以邮箱识别账号。邮箱不依赖最后一次刷新是否成功。没有可恢复邮箱的旧记录显示“待补邮箱”，提供本机补填入口；代码 ID 只作辅助信息。
2. 手动保存先把完整会话放入本机钥匙串，再同步到原来的独立 GitHub Secret。同步失败仍保留本机副本和重试入口。
3. 同一 App 增加导入、导出。文件为用户密码加密的 `.gladosbackup`，已有账号整条跳过，新账号才加入。只含邮箱的记录可保存，但不会创建没有登录信息的签到任务。
4. 保留原本机旧登录资料、邮箱目录与浏览器资料。旧 `active` / `candidate` 凭据可随显式导出携带，标记为旧格式资料。缺少原 User-Agent 或登录来源的旧记录不会伪装成已验证的新版会话。
5. 状态失败时保留邮箱；余额、天数等不会把旧缓存伪装成刚刷新成功。积分只读可用与签到接口接受会分开说明。

## 认证诊断依据与限制

2026-10-03（Asia/Taipei），一个账号在 22:20:13 的状态任务成功，22:33:18 的签到任务返回授权拒绝，两次相隔约 13 分钟：

- [状态成功记录](https://github.com/NewBoringMan/Glados-Railgun-checkin/actions/runs/37129226319/job/111220813045)
- [稍后签到失败记录](https://github.com/NewBoringMan/Glados-Railgun-checkin/actions/runs/37129998205/job/111223045324)

这比较的是两个不同 API，不能证明 Cookie 的固定有效期是 13 分钟。随后有状态失败，也有再次成功，现有日志没有记录足够信息区分期间是否重新登录或替换过 Secret。

恢复的 App 源码还有一个 15 分钟的资料刷新门槛：启动或重新连接时，如果距离上次刷新超过 15 分钟，就重新查询状态。这可能影响用户何时看到错误，但它不是 Cookie 过期计时器，也不是每 15 分钟自动登录的任务。

旧代码确有三项可核查缺陷：捕获只关注旧会话字段；云端使用固定 Chrome/150 User-Agent，没有保存真实登录浏览器；多个域名尝试仅保留最后一个错误。公开的新版实现说明也记录了 `gld:sess` / `gld:sess.sig` 与真实 User-Agent 的变化，但这不是官方的过期时间政策：

- [2026-glados-checkin v2026.9.30](https://github.com/lankerr/2026-glados-checkin/releases/tag/v2026.9.30)
- [GLaDOS 公开登录前端](https://glados.cloud/app.bundle.js)：前端调用同源 `/api/user/session` 并以 `code === 0` 判断登录状态；这只证明端点及认证语义存在，不承诺所有响应都有 ID、邮箱。

此次修复保存完整、同一来源的会话上下文。原 `GLADOS_ACCOUNT_<KEY>` Secret 可以存放 `glados.manual-session` v1 JSON，仍通过既有 `GLADOS_COOKIES` 环境变量传给脚本，不改变账号或排程文件。解析器先解析完整 JSON，避免 Cookie 中的 `&` 被旧多账号分隔规则拆开。

新结构化会话的邮箱由 App 本机账号目录提供，GitHub Actions 的公开结果不再输出该邮箱，并对独立凭据值注册日志遮罩。旧原始 Cookie 任务的结果格式保持兼容。

新结构化会话固定到实际登录域名。写入签到或兑换前，通过同源资料接口核对 ID、邮箱；资料字段不足时才使用已经确认存在的同源 session 端点补充。字段冲突、明确认证拒绝或仍不能核验时停止，保留具体原因。未改成自动重新登录，也不保存服务端响应中的 Set-Cookie 来自动续会话。

旧的原始 Cookie Secret 仍可读取；因缺少实际浏览器和来源，界面会提醒手动补齐。服务端仍可以撤销会话、要求网页验证或拒绝自动签到；本版本不承诺无限有效或绕过此类要求。

## 构建与验证

```sh
python3 -m unittest discover -s tests -v
python3 macos/Tests/validate_package.py
bash app_integration/test-native.sh
bash app_integration/build-manual.sh /absolute/staging/GLaDOS-Account-Center.app
```

前两项分别验证云端业务/会话逻辑和 JavaScript/资源包；后两项需要 macOS，使用隔离存储做原生账号与加密备份测试，并构建、签名验证主 App 和 Safari 扩展。GitHub Actions 的 `GLaDOS Account Center build` 生成 Apple Silicon 候选包，不使用任何生产凭据。

### 生产部署顺序

先把匹配的 `checkin.py`、`status.py`、`session_context.py` 和 `.github/glados/session-format.json` 在同一提交中部署到 App 使用的生产 `master`，再安装新 App、保存或导入新会话。新 App 在写入 Secret 前核对生产分支的格式声明；未部署、声明无效或版本不受支持时，保留本机资料并等待云端升级，不覆盖现有 Secret。

新 JSON 会话一旦写入，后续状态和签到应运行支持该格式的代码版本，不要重新运行旧 SHA 的任务。需要回退 App 时，云端仍需保留兼容解析器；不能直接把云端回退到只支持原始 Cookie 的代码。

无本机原包的 CI 构建只用于编译与包结构验证。正式替换需要先核对实际 App 的物理位置与摘要，确保 MacData 软链接或实际安装位置不会被猜错；在该原包上重新构建，保留未知的其他资源。

```sh
python3 app_integration/install-manual.py inspect --app '/actual/path/GLaDOS Account Center.app'
bash app_integration/build-manual.sh '/staging/path/GLaDOS Account Center.app' '/actual/path/GLaDOS Account Center.app'
python3 app_integration/install-manual.py install \
  --app '/actual/path/GLaDOS Account Center.app' \
  --candidate '/staging/path/GLaDOS Account Center.app' \
  --expected-sha256 '<fingerprint from inspect>' \
  --backup-dir '/existing/project/build/rollback'
```

安装器不会启动 GUI。应先按本机允许的后台 GUI 流程关闭 App，再执行已经核对过的安装步骤。只停止 `com.enoch.glados-account-center.login-refresh`，保留旧数据库和钥匙串；不触碰其他定时任务。

## 仍需实际电脑验收

- 当前已安装 App 的位置、版本、组件差异，以及现有本机凭据是否能读回。
- 原位安装后，账号/兑换策略/排程/日历保持，失效账号仍显示邮箱。
- 在实际钥匙串中保存、重启后读取；导出、再导入的去重和缺凭据提示。
- 从用户正常登录的浏览器手动读取一次，验证服务端身份、只读状态和后续真实签到结果；记录同一凭据指纹的时间线，才能继续判断是否存在真实短时撤销。

2026-10-06 本轮修复时，Mac 连接返回 404/429，因此代码和 CI 的结果不代表上述本机验收已经完成。不要把候选包的编译成功写成已经安装或已经证实有效期问题解决。
