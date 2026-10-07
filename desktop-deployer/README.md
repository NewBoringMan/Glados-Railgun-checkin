# GLaDOS Quick Deploy

**版本 1.0.0 · Windows 10/11 x64 / macOS 13+ Apple Silicon / macOS 13+ Intel**

GLaDOS Quick Deploy 是一个桌面部署向导：用户在需要时完成 GitHub 官方设备授权与 GLaDOS 网页登录，应用自动建立专用部署仓库、保存 Actions Secrets、配置计划任务并读取首次运行结果。普通用户直接使用安装包，操作说明见 [中文使用指南](USER-GUIDE.zh-CN.md)。

本软件不是 GLaDOS 官方客户端。软件构建、逻辑测试与原生窗口烟雾检查，都不能代替真实账号的端到端签到验收；本说明不宣称已使用真实账号完成登录、部署、签到及兑换的全流程验证。最终结果以用户首次运行时 GLaDOS 的响应和 GitHub Actions 的实际记录为准。

## 来源与版本固定

| 项目 | 地址 / 版本 |
| --- | --- |
| 本软件源码 | [NewBoringMan/Glados-Railgun-checkin · build/quick-deploy-desktop-20261007 · desktop-deployer](https://github.com/NewBoringMan/Glados-Railgun-checkin/tree/build/quick-deploy-desktop-20261007/desktop-deployer) |
| 签到上游 | [lankerr/2026-glados-checkin](https://github.com/lankerr/2026-glados-checkin) |
| 固定上游提交 | [`b4ed1f9abeba4ef6244c0e7fd99333970b81d341`](https://github.com/lankerr/2026-glados-checkin/tree/b4ed1f9abeba4ef6244c0e7fd99333970b81d341) |
| 默认任务 | 每日 **09:30，UTC+8**；`plan500`，500 积分换 100 天 |
| 官方 GitHub CLI | `2.102.0`；构建时从 [cli/cli 官方发行包](https://github.com/cli/cli/releases/tag/v2.102.0)取得，并检查固定 SHA-256 |

Actions 在运行时引用固定的上游提交，不自动跟随上游主分支。上游或网站发生变化后，应重新评估兼容性，再更新受控版本。固定代码版本不能保证 GLaDOS 服务端接口和认证规则保持不变。

## 应用会部署什么

点击“一键部署”后，应用使用当前 GitHub 身份自动创建或更新一个**专用、公开、轻量的部署仓库**。它通过 GitHub API 写入自己的配置，并由 Actions 引用上述上游代码。检测到同名但不属于本应用管理的仓库时，会寻找新的可用名称，不覆盖无关项目。

公开配置包含运行时间、兑换计划、上游版本和账号 ID。账号清单 `.github/glados-accounts.json` 使用不含邮箱的账号标识；Cookie、邮箱和原始登录会话不写入该清单。

每个账号使用一个独立的 JSON Secret，名称为 `GLADOS_ACCOUNT_<账号ID>`。登录 Cookie、本次浏览器 User-Agent 和登录来源放在该 Secret 中，由 GitHub Actions Secrets 保存；它们不写入公开仓库文件。程序另外配置串行签到、工作流并发控制和每月保活任务。

**同一仓库的多个账号共享每日运行时间、兑换计划和暂停状态。** 增加或重新部署账号时，所选的时间与兑换计划对该仓库生效。需要不同配置的账号，应分别部署到不同仓库。暂停一个仓库会影响其中全部账号。

## 桌面流程与浏览器范围

1. 自动识别可用浏览器，优先选择已发现的 Brave；也可选择应用内登录窗口。
2. 检查当前官方 `gh` 身份。需要授权时，在 GitHub 官方页面完成设备授权。
3. 打开专用 GLaDOS 登录窗口。用户自行完成登录、验证码及官网要求的验证。
4. 在同一浏览器会话中核验账号身份，自动配置仓库与账号 Secret。
5. 启动或接续验证，读取真实运行状态；应用区分积分增加、今日已签到、工作流成功、排队和失败。

浏览器发现代码覆盖 Brave、Chrome、Edge、Firefox、Opera、Vivaldi、Chromium，以及 macOS 上的 Arc。Chromium 系浏览器通过专用实例连接，Firefox 使用其兼容连接方式。发现结果取决于安装位置、浏览器版本和对应自动化协议；本版本没有逐台、逐版本实测所有这些浏览器，不能把“已发现”理解为全面兼容认证。

Safari 可以参与 GitHub 网页授权，但本版本不通过 Safari 自动取得 GLaDOS 会话。Mac 用户可使用其他已发现的浏览器或应用内登录窗口完成 GLaDOS 登录。浏览器未被识别或兼容连接失败时，应用内窗口提供无需额外安装扩展的选择。

## 数据与凭据处理

| 数据 | 处理方式 |
| --- | --- |
| GLaDOS Cookie 和浏览器身份 | 登录采集后在主进程内处理，上传至对应 GitHub Secret；不经部署界面 IPC 传递，不写入本地部署状态 JSON。 |
| 外部浏览器登录数据 | 使用应用创建并标记归属的临时 profile，不接管日常浏览器 profile。正常结束或取消时关闭并清理；系统占用或异常退出造成的遗留会在后续启动时按归属规则重试清理。 |
| 应用内登录数据 | 使用独立、非持久化 Electron session；结束时销毁窗口并清理会话。 |
| GitHub OAuth 会话 | 由官方 `gh` 的标准登录及存储机制管理。应用可复用已有身份，不退出或清空用户现有会话；其实际存储方式取决于 `gh` 与系统凭据服务。 |
| 本地部署记录 | 保存仓库、账号标识、邮箱、运行编号、结果和应用设置，便于下次打开后管理；不保存 GLaDOS Cookie。 |
| 桌面界面状态 | 使用当前运行期的内存，不调用 `localStorage`、`sessionStorage` 或 `IndexedDB`。 |

因此，“不在部署记录中保存 Cookie”不等于“所有凭据绝不触及磁盘”：外部浏览器的专用临时 profile 和官方 `gh` 的会话存储有各自生命周期。

在可写的 `/Volumes/MacData` 存在时，Mac 数据优先使用 `/Volumes/MacData/Applications/GLaDOSQuickDeploy/Data`；其他情形使用系统应用数据位置。界面底部可查看本次实际使用的数据目录。

部署完成后可以退出应用，已建立的 GitHub Actions 定时任务继续运行。退出应用或取消本机等待，不会撤回已经提交的 GitHub 运行。需要暂停云端定时任务时，应使用账号卡片中的暂停操作。

## 安装包与签名状态

| 目标 | 产物形式 | 说明 |
| --- | --- | --- |
| Windows x64 | `.exe` 安装程序 | 可选择安装目录；应用随包携带所需运行组件与对应架构的官方 `gh`。 |
| macOS ARM64 | `.dmg` / `.zip` | 适用于运行 macOS 13 Ventura 或更新系统的 Apple Silicon Mac。 |
| macOS x64 | `.dmg` / `.zip` | 适用于运行 macOS 13 Ventura 或更新系统的 Intel Mac。 |

Mac 最低系统要求来自本版本使用的 [Electron 44 官方支持范围](https://www.electronjs.org/blog/electron-44-0)，不能在 macOS 12 或更早系统上作为受支持版本使用。

版本 1.0.0 未使用微软代码签名证书。Mac 构建采用 ad-hoc 签名，未使用 Apple Developer ID，也未完成开发者公证。首次运行可能出现系统来源提示；应先确认安装包来源和校验值，再按系统提供的正常安装提示处理。不要关闭系统安全机制来运行来历不明或完整性不符的包。

## 工程结构

| 位置 | 职责 |
| --- | --- |
| `src/main.cjs` / `src/preload.cjs` | 原生窗口、有限 IPC 接口、应用退出与运行组件路径。 |
| `src/controller.cjs` | 登录、部署、取消、运行结果和账号管理的状态协调。 |
| `src/browser.cjs` | 浏览器发现、专用登录会话、身份核验与临时目录清理。 |
| `src/github.cjs` | 官方 `gh` 进程调用、仓库配置、Secrets 写入和 Actions 结果读取。 |
| `src/workflow.cjs` | 固定上游版本、签到工作流、结果封装和保活任务。 |
| `src/state.cjs` | 本地配置字段白名单及原子写入。 |
| `renderer/` | 原生 HTML / CSS / JavaScript 桌面界面。 |
| `test/` / `scripts/` | 逻辑验证、依赖准备、原生构建及烟雾检查。 |

## 开发与验证方式

本节仅供源码维护者使用。普通安装包用户不需要 Node.js、Python、Git 或开发环境。

工程要求 Node.js 22.12 或更新版本；当前 CI 使用 Node.js 24 和 Python 3.12。Python 用于解压官方 `gh` 发布包。请在本源码目录操作，使用目标平台的原生构建环境。

若已有随版本固定的 `package-lock.json`，使用 `npm ci` 安装；没有锁文件时，先生成一次并在各构建平台共用同一份：

```sh
npm install --package-lock-only --ignore-scripts --no-audit --no-fund
npm ci --no-audit --no-fund
npm run check
npm test
npm run prepare:gh
node scripts/make-icons.cjs
npm start
```

三个原生构建命令分别为：

```sh
npm run dist:win
npm run dist:mac-arm
npm run dist:mac-intel
```

每条命令应在对应平台 / 架构的构建环境中运行。开发模式的窗口检查使用 `npm run smoke`；检查打包产物时，将真实可执行文件路径传给 `scripts/smoke.cjs`。例如 Apple Silicon Mac 的默认产物：

```sh
node scripts/smoke.cjs "dist/mac-arm64/GLaDOS Quick Deploy.app/Contents/MacOS/GLaDOS Quick Deploy"
```

烟雾模式检查原生进程、真实界面、IPC、Node 隔离、页面横向溢出、随包 `gh --version` 的实际启动、`puppeteer-core` 的加载，以及页内锚点跳转后的 IPC，并输出截图和 JSON 记录。它不登录真实账号，也不触发真实签到。

[原生构建工作流](quick-deploy-desktop.yml)的方式是：生成统一依赖锁文件，分别在 Windows x64、macOS ARM64 与 macOS Intel 运行逻辑检查、校验官方 `gh`、打包、启动真实产物，再生成摘要和上传构建产物。工作流定义本身不代表构建已经成功；请核对对应提交的 Actions 运行和产物记录。

## 结果与限制

- “已加分”需要可核实的积分增加；“今日已签到”需要对应账号的明确结果。
- “工作流成功”仅说明 GitHub 工作流成功，不能单独证明实际签到加分。
- “验证排队 / 正在验证”说明尚未取得最终结果；已有其他运行时，应用会按串行原则等待接续。
- GLaDOS 可以调整认证、会话期限、积分计划或自动化检测。会话拒绝需要重新完成官方登录；本软件不能保证未来长期免维护，也不能保证服务端接受自动签到。
- 签到成功与兑换成功是两个结果。已取得积分但兑换失败时，应保留加分事实，同时明确本次任务异常。

本项目代码许可见 [LICENSE](LICENSE)，第三方依赖说明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
