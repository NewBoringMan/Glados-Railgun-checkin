# GLaDOS Account Center 2.0.14：手动登录管理与读取修复

## 2.0.14 手动读取连接修复

旧读取流程每次分配新调试端口，但专用 Chromium / Edge 窗口会保留运行。同一资料目录已有进程时，新的启动请求可能转交给原进程，网页打开不代表新端口已启用；参见 [Chromium 启动进程交接源码](https://chromium.googlesource.com/chromium/src/+/refs/heads/main/chrome/app/chrome_main_delegate.cc)。这会使流程在“读取账号”确认对话框出现前停止。

2.0.14 先核实同一用户、可执行文件、专用资料目录、进程启动时间和回环监听归属，复用已存在的连接；确认没有实例后才按原目录启动一次。用户点击“读取账号”后重新核实进程与浏览器端点。无法核实时停止并保留原窗口，不自动关闭、切换资料目录或重建登录环境。

读取确认对话框、页面读取、只读身份核验和结果构造使用不同的固定错误提示。原始浏览器异常和响应正文不会展示，通用读取失败也不再被解释为登录过期。服务端是否接受既有登录资料仍需通过实际身份接口与后续任务结果确认，连接修复本身不能恢复被服务端拒绝的会话。

## 2.0.13 查询中断修复

“GitHub 查询暂未完成”发生在已取得任务编号后的结果读取阶段。2026-10-07（Asia/Taipei）在实际 Mac 上只读查询任务 `37492857403`，`gh api` 成功返回已完成状态，但实测用时 14.42 秒；该任务完整日志成功读取用时 18.49 秒。2.0.12 每次 GET 仅允许 6 秒，会提前中止这类正常响应。这些实测确认了查询时限缺陷，不代表已经从本机回执核实截图对应的任务编号。

2.0.13 为任务状态和账号任务明细设置每次 30 秒、日志每次 60 秒的上限，最多重试三次只读请求，所有请求与间隔共用整体查询截止时间。剩余时间不足时不再强行延长到至少一秒。GET 和日志查询均固定到 `github.com`。日志可能需要下载归档或逐项读取，参见 [GitHub CLI 日志读取说明](https://cli.github.com/manual/gh_run_view)。

任务身份与账号明细核对后，先保存服务端已完成的结论；日志尚未取回时保持可重查状态，重启或点击刷新仍读取原编号。日志解析失败也从最新回执更新读取状态，避免用查询前的旧副本抹掉完成结论。界面区分任务状态、账号明细及日志读取中断，旧版回执仍可读取。

本次不改账号、凭据、签到与兑换逻辑。GitHub 查询中断不能证明 GLaDOS 会话过期；GLaDOS 自身的授权拒绝仍按对应服务端日志单独诊断。

## 2.0.12 刷新修复

用户刷新时出现的“此次派发未返回可核对的任务编号”属于 GitHub 工作流回执问题，不能据此判断 GLaDOS 登录信息过期。2.0.11 依赖 `gh workflow run` 的可选任务链接输出；没有链接时会留下未绑定编号的回执，后续刷新又反复恢复同一条记录。

2.0.12 通过指定 `2026-03-10` 版本的 GitHub REST 接口派发，直接核对响应中的 `workflow_run_id` 及两个任务地址，并先保存派发确认再解析响应。参见 [GitHub 工作流派发接口](https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event) 和 [GitHub CLI 工作流命令](https://cli.github.com/manual/gh_workflow_run)。

手动点击各处刷新按钮时，允许新的只读查询替代没有任务编号的旧查询；原记录保留并标明被替代。已经取得编号的任务仍按原编号查询。自动刷新不替代未知任务，签到不允许用此方式再次派发，也不从“最近运行”猜测编号。账号、登录资料及已发布凭据不因查询失败或替代而改变。

原生测试直接编译实际工作流客户端，使用隔离存储与内存进程替身覆盖成功、空响应、失败退出、超时、重启和查询恢复，并验证每个派发意图最多一次 POST。这些测试不使用生产凭据；真实账号是否被 GLaDOS 接受仍须独立核对。

## 此次范围

此版本恢复同一个 Account Center 的手动登录管理，移除自动收验证码、自动登录和自动更新 Cookie 的启动链与界面。保留账号清单、每日签到、独立账号开关、每账号兑换策略、全局兑换档位、连续签到、日历、运行记录、台北时区排程、独立 Edge 资料目录和内嵌 Safari 扩展。以实际已安装的 2.0.10 / build 20042 为兼容基准，另行保留其中的签到失败通知、通知开关和已派发任务的查询恢复能力。

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

对实际安装的 build 20042 源码检查确认：CDP 手动读取已支持 `gld`，但浏览器内验证使用完整 Cookie 与实际 UA，保存时却经 `edge_login_support.js` 缩为 `koa` 或 `gld` 中的一组两枚 Cookie，没有保存真实 UA、采集时间与完整会话上下文。非 CDP 旧路径仍只识别 `koa`，使用固定 Chrome/150 UA，并可能把积分可读取误当作完整认证通过。旧云端多域名尝试还只保留最后一个错误。

这些是可修复的实现缺陷，但尚不能证明某一项单独导致了这次十几分钟后的授权拒绝。原 HTTP 自动登录传输使用 CookieJar，登录时确实接受 Set-Cookie，不能笼统声称整个 App 不处理 Set-Cookie。原时间模块安排按月或已知到期时间维护，没有设置十几分钟的登录寿命。

实际项目的 `BUILD_20042_ACCEPTANCE.md` 还记录过旧 worker 删除共享 Edge 资料中的同域 Cookie，造成其他账号本地登录受影响；记录称 20042 已改为临时隔离 BrowserContext，但未完成真实新凭据和云端验收。这项历史本地破坏机制也不等于已经证明服务端会短时撤销会话。此次不再保留该自动登录 worker。

公开的新版实现说明也记录了 `gld:sess` / `gld:sess.sig` 与真实 User-Agent 的变化，但这不是官方的过期时间政策：

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

安装器不会启动 GUI。应先按本机允许的后台 GUI 流程关闭 App，再执行已经核对过的安装步骤。停用 `com.enoch.glados-account-center.login-refresh`，保留旧数据库和钥匙串。签到失败提醒使用独立的只读监测入口，不再经过会自动维护登录的旧 `--tick`；原关闭或缺失的开关维持关闭，不主动开启提醒。

本轮已确认原 App 的物理路径是 `/Users/enoch/Applications/GLaDOS Account Center.app`，不是符号链接。原项目 `/Volumes/MacData/Projects/GLaDOS-Account-Center-integration` 有后续未提交修改，保持原样；本次修复在它的 `build/manual-restore-20261006/source` 隔离 worktree 中构建。MacData 已核实为 USB 外置卷。候选包与回滚副本也置于该任务目录。

## 仍需实际电脑验收

- 现有本机凭据是否能通过允许的数据入口读回；位置、版本及组件差异已确认。
- 原位安装后，账号/兑换策略/排程/日历保持，失效账号仍显示邮箱。
- 在实际钥匙串中保存、重启后读取；导出、再导入的去重和缺凭据提示。
- 从用户正常登录的浏览器手动读取一次，验证服务端身份、只读状态和后续真实签到结果；记录同一凭据指纹的时间线，才能继续判断是否存在真实短时撤销。

2026-10-06 前期 Mac 连接曾返回 404/429，随后恢复并已成功读取实际安装版源码。当前 DCF 后台控制的状态查询正常，但界面读取曾超时；LocalAnt 的 APP_DATA_GUARD 要求使用专门的 app_data_* 接口读取受保护账号目录，该接口尚未在本轮工具中暴露。本轮不通过通用 shell 或其他通道绕过该规则。代码、合成测试与构建结果不能代替实际安装、钥匙串和真实会话验收；各步骤以实际完成的记录为准。
