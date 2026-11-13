# 校园网自动登录工具（安徽工程大学 · 统一身份认证版）

基于 **锐捷 ePortal 5.x + CAS 统一身份认证** 的校园网自动登录工具，支持图形界面配置、系统托盘常驻、开机自启、断线自动重连。

> 本版已从原 Dr.COM（城市热点）协议重写为安徽工程大学实际使用的认证体系。
> 许可证为 **PolyForm Noncommercial 1.0.0**（全文见 [LICENSE](LICENSE)）：个人学习、教学与
> 非商业用途可用；**禁止商业使用**；软件按"原样"提供，不附带任何担保。

## 功能特性

- **图形界面配置**：认证地址、学号、密码、运营商均可自定义，保存为 `config.json`
- **密码加密存储**：密码用 Windows DPAPI 加密，密文仅在当前用户+当前电脑可解，配置文件可安全分发
- **系统托盘常驻**：程序启动后驻留托盘，关闭窗口自动最小化到托盘，不退出
  - 图标颜色：🟢 绿 = 在线　⚪ 灰 = 离线　🔴 红 = 登录失败（悬停图标查看详细状态）
  - **右键托盘图标**：配置窗口 / 立即登录 / 检查状态 / 开机自动登录 / 断线自动重连 / 打开日志文件 / 退出
- **开机自启**：写入注册表 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`，静默自动登录
- **断线自动重连**：每 60 秒检测在线状态，掉线后按 1/2/4/8/15 分钟的退避间隔重连
  （可在托盘菜单开关）。密码错误、门户要求验证码、出口配置无效这类"重试也没用"的失败
  会**暂停自动重连**并只提示一次，避免把统一身份认证账号刷到锁定
- **失败不刷屏**：网络类失败只写日志，第一次失败才弹通知；账号类失败点「立即登录」
  或改完配置即自动解除暂停
- **配置写入安全**：临时文件 + `os.replace` 原子替换，断电不会留下半截 JSON；
  密码解不开（换了电脑/用户）时也不会把已存的密文冲掉
- **本地隐私**：学号/密码都用 DPAPI 密文存储；日志自动脱敏（学号/票据/会话号/MAC），
  超过 1 MB 轮转，托盘可一键清空
- **单实例**：重复启动会提示"已经在运行"，不会出现两个托盘图标、两套自动重连互抢
- **证书默认校验**：优先校验门户 TLS 证书，只有自签证书或网关 IP 才降级，并在日志里写明
- **通知不占线程**：登录结果走系统托盘气泡；没有托盘时才用主线程气泡，不额外新建 Tk 窗口
- **静默模式**：`--login` 参数启动时不显示窗口，仅托盘 + 右下角通知气泡
- **运营商自动识别**：配置窗口打开后自动从门户拉取可选出口（中国电信/中国移动/中国联通/校园网）

## 文件说明

| 文件/目录 | 说明 |
|------|------|
| `校园网自动登录.py` | 主程序（Python 源码） |
| `ahpu_portal_probe.py` | 协议调试脚本（查状态/列出口/单独验证认证），排查问题用 |
| `config.json` | 配置文件（首次保存后生成，与程序同目录，**不要外发**） |
| `login.log` | 运行日志（与程序同目录） |
| `requirements.txt` | 运行依赖，`pip install -r requirements.txt` |
| `tests\` | 单元测试（pytest，21 个用例，CI 也会跑） |
| `requirements-dev.txt` | 测试依赖 |
| `LICENSE` | 许可证（PolyForm Noncommercial 1.0.0） |
| `CHANGELOG.md` | 版本变更记录 |
| `stage_nuitka\` | 安装包用的载荷（Nuitka 编译产物，958 个文件约 60 MB） |
| `installer\` | Inno Setup 脚本 + 中文语言包 + 生成的安装包 |
| `tools\` | 图标、版本信息、签名脚本、自签名证书 |

> `.gitignore` 已排除 `config.json`、`login.log`、`stage_nuitka\`、`installer\output\` 等
> 个人数据和打包产物，不要用 `git add -f` 把它们提交上去。

> 打包过程中的 `dist\`、`stage\`、`nuitka_out\` 等中间目录不纳入版本管理，随时可用下面的命令重新生成。

## 快速开始

```bash
# 安装依赖（仅需一次）
pip install -r requirements.txt

# 启动（图形界面 + 托盘）
python 校园网自动登录.py

# 静默模式（开机自启使用）
python 校园网自动登录.py --login

# 只查在线状态，不启动界面
python 校园网自动登录.py --status
```

> **别用会关掉的终端窗口跑它**：`python.exe` 和终端属于同一个控制台进程组，
> 关终端时 Windows 会给它发 `CTRL_CLOSE_EVENT`，程序连同托盘图标一起退出。
> 日常使用请用无控制台的 `pythonw`，或者直接装打包版（exe 本身没有控制台）：
>
> ```bash
> pythonw 校园网自动登录.py
> ```
>
> 开机自启项本来就是用 `pythonw --login` 写的，不受这个问题影响。

### 首次配置

1. 运行程序 → 配置窗口
2. 认证地址保持默认 `https://sam.ahpu.edu.cn`
3. 填 **学号** 和 **统一身份认证密码**（与微校园、今日校园同一套密码）
4. 选择运营商出口：未办理运营商套餐的选「校园网」；办了宽带的选对应运营商
5. 按需勾选「开机自动登录」「显示登录通知」
6. 点击「保存并登录」

> 学号**不需要**任何 `@dx` / `@lt` 之类的后缀，填纯学号即可。

### 开机自启

- 配置窗口勾选「开机自动登录」保存，或托盘右键菜单勾选「开机自动登录」
- 原理：注册表 Run 项，登录 Windows 后以静默模式自动运行并认证
- 取消勾选即删除启动项

## 配置说明（config.json）

```json
{
    "portalHost": "https://sam.ahpu.edu.cn",   // 认证门户
    "userIdEnc": "AQAAANCMnd8BFdERjHoAwE/...", // 学号密文（DPAPI）
    "service": "校园网",                        // 出口：校园网/中国移动/中国联通/中国电信
    "autoStart": true,                         // 开机自启
    "showNotify": true,                        // 登录结果通知气泡
    "autoRelogin": true,                       // 断线自动重连
    "passwordEnc": "AQAAANCMnd8BFdERjHoAwE/..." // 密码密文（DPAPI）
}
```

**学号和密码都不是明文**：都用 Windows DPAPI 加密，密文只能由「当前 Windows 用户 + 当前这台电脑」解开。
旧版本留下的明文 `userId` / `password` 会在新版启动时自动迁移成密文（迁移完文件里就查不到明文了）。

**出口不会自动换**：新配置默认出口是「校园网」。如果配置的出口不在门户返回的可选列表里，
程序会照配置的出口去试，失败时明确提示"出口不在可选列表"，
不会静默换成列表里的第一家运营商（避免无意中走计费出口）。

- 把程序目录发给别人，对方解不开你的学号和密码，日志会提示「…无法解密，请重新输入」
- 换人使用：删掉 `config.json`，让对方重新填学号密码
- 学号/密码两项任一解不开时，保存会保留原密文，只是切开关不会把它们冲掉

### 本地数据与隐私

| 数据 | 位置 | 保护方式 |
|---|---|---|
| 学号、密码 | `config.json` | DPAPI 密文（当前用户 + 本机） |
| 认证门户、出口、开关 | `config.json` | 明文（不含个人信息） |
| 运行日志 | `login.log` | 自动脱敏：学号留前 4 位，`ticket` / `sessionId` / `userMac` 打码 |

- 日志超过 1 MB 自动轮转，保留 `login.log.1 ~ .3`；托盘右键「清空日志」一键删除
- 升级到 1.0.2 后，历史日志会按同样规则重写一遍，旧明文一并清掉
- **能防**：误发文件夹、误传 GitHub、云盘同步、同机其他 Windows 用户、换电脑后旧密文失效
- **防不住**：以你身份运行的恶意程序（它可以直接调 DPAPI 解密）、管理员权限、屏幕截图。
  想要更强的保护只能每次手输密码，那就失去自动登录的意义了

## 分发给同学

1. 用下面的 **Nuitka + Inno Setup** 流程打安装包，把
   `installer\output\校园网自动认证系统_安装包_1.0.2.exe` 发给同学
2. **不要附带 `config.json`**（里面是你的学号和你机器加密的密码）
3. 同学双击安装包 → 一路下一步 → 启动后填自己的学号和密码 → 勾「开机自动登录」→ 保存并登录
4. 自启项会自动指向 **程序当前所在路径**；程序目录被移动后，下次启动会自动校正注册表项


## 认证流程（实测验证）

### 第 0 步：拿到「会话」（两种情况，必须先判断用哪条）

**A. 已认证过 / 网关认识本机** —— 直接调 `redirect.jsp` 就有会话：

```
GET /eportal/redirect.jsp
    → 302，Location 里带 sessionId / userIp / userMac / nasIp / customPageId
```

**B. 未认证且网关不认识本机** —— `redirect.jsp` 只会把你踢到占位地址：

```
GET /eportal/redirect.jsp
    → 302  http://123.123.123.123          ← 没有任何参数，走不通
```

这时必须走浏览器的真实路径：先访问任意普通网页，让网关拦截：

```
GET http://www.msftconnecttest.com/connecttest.txt
    → 200 + 一小段 JS：
      <script>top.self.location.href='https://sam.ahpu.edu.cn/eportal/index.jsp?
        wlanuserip=494fc4cd…&wlanacname=4338bd8d…&ssid=&nasip=3b5f2cf6…
        &snmpagentip=&mac=531af092…&t=wireless-v2&url=709db9dc…&apmac=
        &nasid=…&vid=…&port=…&nasportid=…'</script>

GET  <上面那段 /eportal/index.jsp 的完整地址>
    → 302  /portal/portal-main?sessionId=xxxx&userIp=…&userMac=…&nasIp=…
```

要点：

- 入口是 **`/eportal/index.jsp`**，不是 `redirect.jsp`
- 参数（`wlanuserip`、`nasip`、`mac`、`nasid`…）是 NAS 加密下发的十六进制串，**不需要解密，原样转发**
- 这些参数无法自己伪造：给 `redirect.jsp` 手动加 `userip/usermac` 会被忽略（实测），它只按源 IP 查

### 第 1~3 步：统一身份认证

```
GET  /cas-sso/login?flowSessionId=…&customPageId=…&nasIp=…&userIp=…&userMac=…
        → 下发 SESSION cookie；页面含两个隐藏字段：
          <p id="login-croypto">      本次 AES 密钥（每次刷新随机）
          <p id="login-page-flowkey"> execution
POST /cas-sso/login?<同上>   (application/x-www-form-urlencoded)
        username / type=UsernamePassword / _eventId=submit / geolocation=
        execution=<flowkey> / captcha_code=
        croypto=<密钥>
        password        = base64(AES-128-ECB-PKCS7(明文, 密钥))
        captcha_payload = base64(AES-128-ECB-PKCS7("{}", 密钥))
        → 302，Location 里带 ticket=ST-…（失败则回渲染登录页给 login-error-msg）
```

### 第 4~6 步：票据落地与授权

```
GET  <ticket 跳转>                        建立 ePortal 会话
判定：CAS 通过后先查在线状态（校园网有无感知认证）
      ├─ 已在线  → 认证完成（最常见）
      └─ 仍离线  → 继续下面两步
POST /eportal/network/serviceSelection {sessionId}          → 运营商列表
POST /eportal/network/serviceLogin     {sessionId, service} → authResult=success
POST /eportal/network/userOnline       {sessionId}          → online=true
```

要点：

- 密码不是明文传输，用登录页当次下发的 `croypto` 做 **AES-128-ECB + PKCS7** 加密
- `execution` 与 `SESSION` cookie 绑定，必须先 GET 登录页再 POST
- `serviceLogin` 的服务端 DTO 只有两个字段：`sessionId`、`service`
- **流程节点决定 `serviceLogin` 能不能调**（`POST /eportal/workFlow/getCurrentNode` 可查）：
  - 未认证时是 `serviceSelection` → 可以正常授权上线
  - 已在线时是 `finish` → 服务端返回 `workFlowNode is empty`，属正常现象，不是错误
- **未认证时 `redirect.jsp` 不可用**（只会 302 到 `123.123.123.123`），必须先走
  「普通网页 → 网关拦截 JS → `/eportal/index.jsp`」这条路拿会话
- **认证后不要立刻重新访问 `redirect.jsp`**：它可能新建流程会话，把刚认证好的流程顶掉
- 多网卡机器（有线 + WiFi + Hyper-V/VMware 虚拟网卡）默认路由可能不走校园网，
  程序会枚举本机 IPv4、逐张绑定网卡试到能取到 `sessionId` 为止
- 刚插网线/连 WiFi 有几秒到几十秒的网关初始化窗口，此时 `redirect.jsp` 会返回 302 但不带
  `sessionId`，程序会自动轮询等待（最长约 40 秒）
- 点「下线」后 `userOnline` 会立刻变 false，但网关真正释放会话还要 5~6 秒，
  排查时请等 10~15 秒再抓包（`autodiag` 已内置这段稳定期）

## 启动耗时（实测）

2026-10 优化后，从进程启动到登录完成（网络已就绪）：

| 版本 | 优化前 | 优化后 |
|---|---|---|
| 源码版 `--login` | 3.84 s | **0.95 s** |
| 打包 exe `--login` | 5.67 s | **1.03 s** |
| 安装版 exe（装到 `%LOCALAPPDATA%`） | 5.7 s | **0.87 s** |

协议本身很快（6 个请求合计 0.35 秒），耗时主要在等待，所以优化都围绕等待：

| 改动 | 效果 |
|---|---|
| 删掉 `_initial_login` 里的固定 `time.sleep(3)` | 直接省 3 秒 |
| 等待网关改为「多次短超时」：单次 2 秒、间隔 300ms、总上限 45 秒 | 失败一次只花 2 秒，不再一卡 20 秒 |
| 就绪判据改为「普通网页被拦截 **或** 能取到 sessionId」 | 未认证状态也能立刻判定就绪 |
| 轮询 1000ms → 300ms | 最多省 0.7 秒 |
| `pystray`/`Pillow` 延迟导入、图标延迟生成、托盘初始化后置 | exe 启动省约 1.3 秒 |

> 冷知识：原来 `wait_network(portal, 45)` 的 45 是**循环次数**，而单次失败请求会阻塞 20 秒，
> 最坏能等 15 分钟。现在参数改成了**墙钟秒数**，实测 `timeout=6` 在不可达网关上 8.35 秒返回。

可调参数在文件顶部：

```python
PROBE_TIMEOUT = 2.0    # 单次探测超时（秒）
PROBE_INTERVAL = 0.3   # 两次探测间隔（秒）
READY_TIMEOUT = 45     # 等待网卡/网关就绪的总时长上限（秒）
```


## 调试与排错

主程序只查状态：`python 校园网自动登录.py --status`

打包版没有控制台，`--status` 的输出看不见，用自检代替（结论写日志 + 弹窗）：

```bash
python 校园网自动登录.py --selftest
# 打包版：校园网自动认证系统.exe --selftest
```

自检会报告：版本、程序目录、本机地址是否在校园网网段、加密库是否可用、出口网卡、
门户会话、可选出口、在线状态。同学出问题时让他跑这个，截图就能定位。

> 日志里的学号、`ticket`、`sessionId`、MAC 已经自动打码（IP 保留，排查网络要用），
> 直接发给别人也不用先手动删。

协议调试脚本（复用主程序协议层）：

```bash
python ahpu_portal_probe.py status                     # 在线状态 + 实际出口网卡
python ahpu_portal_probe.py services                   # 可选运营商出口
python ahpu_portal_probe.py info                       # 在线详情（账号/IP/MAC/出口）
python ahpu_portal_probe.py castest 学号 密码           # 只验证统一身份认证，不影响在线状态
python ahpu_portal_probe.py login 学号 密码 出口 force  # 强制走完整登录流程
python ahpu_portal_probe.py relogin                    # 用 config.json 直接重新登录
python ahpu_portal_probe.py diag                       # 抓一套完整诊断（含原始响应体）
python ahpu_portal_probe.py autodiag                   # 盯梢：等你下线→等网关稳定→自动诊断→自动恢复上网
python ahpu_portal_probe.py autodiag 180 15            # 可自定义：最多等 180 秒、离线后再稳定 15 秒
```

> ⚠️ **`castest` / `login` 要明文密码作为命令行参数**，会留在 PowerShell 历史
> （`$env:APPDATA\Microsoft\Windows\PowerShell\PSReadLine\ConsoleHost_history.txt`）
> 和第三方命令行审计日志里。日常排错请用 `relogin`（读 `config.json` 里的 DPAPI 密文），
> 只有排查「密码到底对不对」时才用 `castest`，用完记得清理历史或改用 `Clear-History`。

常见报错对照：

| 日志 | 原因 | 处理 |
|---|---|---|
| `无法获取 sessionId（HTTP 302 → ...）` | 网关初始化中，或不在校园网 | 等 30 秒重试；确认只连校园网 |
| `认证信息无效。` | 密码错，或触发验证码 | 核对密码；换浏览器登录一次看是否需要验证码 |
| `workFlowNode is empty` | 设备已在线，流程已到 `finish` | 先下线再登录，或用 `force` |
| `所有网卡都取不到认证会话` | 默认路由走了热点/虚拟网卡 | 断开多余网络，或看日志里的「网络出口网卡」 |
| `认证类失败，已暂停自动重连` | 密码错 / 要验证码 / 出口无效，再试也不会成功 | 修正后点「立即登录」或托盘「断线自动重连」重新开关一次 |
| `配置的出口「X」不在可选列表（…）中` | 该账号当前没有这个出口 | 配置窗口里按提示重新选出口，不会自动换运营商 |
| `自动重连失败（network），连续第 N 次` | 网络类失败，正在退避重试 | 不用管；间隔按 1/2/4/8/15 分钟递增 |
| `TLS 证书校验失败（host）` | 门户换成了自签证书 | 只是提示，本次运行会临时降级；频繁出现请确认没被劫持 |
| 弹窗「已经在运行，请查看系统托盘」 | 又启动了一个实例 | 正常保护，用托盘里的那个即可 |
| 弹窗 `Could not create temporary directory!` | 单文件 exe 解包失败（`TEMP` 是 8.3 短路径） | 用下面「目录版」，或用带 `--runtime-tmpdir .` 打包的单文件版 |


## 开发与测试

核心逻辑（脱敏、失败分类、退避计算、Cookie 分域、配置读写）都有单元测试，
不需要真实校园网环境即可跑：

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q
```

```
21 passed in 0.3s
```

- `tests/conftest.py` 注入了 tkinter 的测试替身，所以 **无图形环境也能跑**（GitHub Actions 的 Windows runner 用的就是这套）
- 测试数据全部是虚构的学号 / MAC / IP，不含任何真实个人信息
- 改动认证流程后请先跑一遍，再重新打包

## 自行打包 exe

> **当前生产用的是「方案三：Nuitka」**（见下），它对杀毒误报最友好、体积也最小。
> 方案一/二（PyInstaller）保留作为备选，仓库里不保存它们的中间产物。

### 方案一：单文件（注意加 `--runtime-tmpdir .`）

```bash
pip install pyinstaller pystray pillow cryptography
pyinstaller -F -w -n 校园网自动认证系统 --runtime-tmpdir . ^
  --hidden-import cryptography --hidden-import pystray ^
  --hidden-import PIL.Image --hidden-import PIL.ImageDraw ^
  校园网自动登录.py
```

**`--runtime-tmpdir .` 很关键**：默认情况下单文件版会解包到 `%TEMP%`，
而部分机器的 `TEMP` 是 8.3 短路径（`C:\Users\USER~1\...`）或不可写，
会直接弹 `Could not create temporary directory!` 然后打不开。
指定 `.` 后改为解包到 exe 同目录（`_MEIxxxx` 文件夹，正常退出会自动删除，
异常退出留下的会在下次启动时自动清理一小时以前残留的）。

### 方案二：目录版（最稳，不依赖解包）

```bash
pyinstaller -D -w -n 校园网自动认证系统 ^
  --hidden-import cryptography --hidden-import pystray ^
  --hidden-import PIL.Image --hidden-import PIL.ImageDraw ^
  校园网自动登录.py
```

- 产物是 `dist\校园网自动认证系统\` 整个文件夹（约 73 MB / 一千多个文件）
- 优点：启动快、**完全不解包**、不受 `TEMP` 影响、不会被杀软误判为释放行为
- 分发时把整个文件夹压缩成 zip 发出去，对方解压后双击里面的 exe

### 两种版本的取舍

| | 单文件 | 目录版 |
|---|---|---|
| 分发 | 一个 exe，最省事 | 一个 zip |
| 启动速度 | 每次解包，慢 1~3 秒 | 快 |
| `TEMP` 异常时 | 需 `--runtime-tmpdir .` | 不受影响 |
| 杀软误报 | 偶发（解包行为） | 较少 |

- `-F` 单文件　`-D` 目录　`-w` 无控制台窗口　`-n` 输出名称　`-i 图标.ico` 自定义图标
- `--hidden-import` 是为了让密码加密（cryptography）和托盘（pystray/Pillow）被正确打进去
- 打包后程序自动适配：配置与日志均读写 **exe 同目录**，开机自启项也指向 exe 当前位置

### 方案三：Nuitka（推荐，杀毒误报最低）

Nuitka 把 Python 编译成原生 C 代码，**没有 PyInstaller 的自解压引导器**，
VirusTotal 上的误报通常从 5~8 条降到 0~2 条，体积也更小。

```bash
pip install nuitka
python -m nuitka --standalone --windows-console-mode=disable ^
  --enable-plugin=tk-inter ^
  --include-package=pystray --include-package=PIL --include-package=cryptography ^
  --windows-icon-from-ico=tools\app.ico ^
  --company-name="CampusNet AutoLogin" --product-name="校园网自动认证系统" ^
  --file-version=1.0.2.0 --product-version=1.0.2.0 ^
  --output-dir=nuitka_out --output-filename=校园网自动认证系统.exe ^
  校园网自动登录.py
```

要点：

- `--include-package=cryptography` **不能省**：本项目的 AES 是在函数里延迟 import 的，
  不显式包含会运行时 `ImportError`
- `--enable-plugin=tk-inter` 负责把 tcl/tk 数据文件带进去
- 编译需要 C 编译器：如 MSYS2 的 `ucrt64\bin\gcc.exe`（Nuitka 会提示
  “Non downloaded winlibs-gcc … ignored”，但仍然能用）
- 产物在 `nuitka_out\校园网自动登录.dist\`，用 `stage_nuitka\` 作为安装包载荷

### 三种打包方式对比

| | PyInstaller 单文件 | PyInstaller 目录版 | **Nuitka 目录版** |
|---|---|---|---|
| exe 体积 | 30.6 MB | 6.6 MB | **21.6 MB** |
| 安装包体积 | — | 23.6 MB | **16.8 MB** |
| VT 误报 | 多（5~8 条） | 中 | **少（通常 0~2 条）** |
| `TEMP` 异常 | 受影响（需 `--runtime-tmpdir`） | 不受影响 | 不受影响 |
| 启动速度 | 慢（每次解包） | 快 | 快 |


## 制作安装包（Inno Setup）

安装包基于**目录版**制作，脚本在 `installer\campusnet.iss`，中文界面靠 `installer\ChineseSimplified.isl`。

### 行为说明

| 项目 | 说明 |
|---|---|
| 安装位置 | `%LOCALAPPDATA%\Programs\CampusNetAutoLogin`（当前用户安装，**不需要管理员权限**） |
| 升级安装 | 自动检测旧版本安装目录并沿用它覆盖：先读卸载登记里的 `InstallLocation`（同 AppId 或显示名匹配「校园网 / CampusNet」），再试常见默认目录。`config.json` / `login.log` 保留，旧版 PyInstaller 的 `_internal` 残留会清掉 |
| 快捷方式 | 开始菜单 + 可选桌面快捷方式 |
| 附带数据 | **不含 config.json / login.log**，每个用户装完自己填学号密码 |
| 卸载 | 删除程序文件、开始菜单/桌面快捷方式、卸载登记项；同时清理 `config.json`、`login.log` 和 `HKCU\...\Run\CampusNetAutoLogin` 自启项 |

> 升级时会覆盖同名的程序文件；如果旧版本装在 `Program Files` 下而当前没有管理员权限，
> 安装包会跳过那个目录、回到默认位置（日志里会写明原因）。

### 重新编译

```bash
# 1) 先用 Nuitka 产出载荷（见「方案三」），把 nuitka_out\…dist 复制成
#    stage_nuitka\校园网自动认证系统\
# 2) 打安装包
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" ^
  "/DMyPayloadDir=..\stage_nuitka\校园网自动认证系统" installer\campusnet.iss
```

产物：`installer\output\校园网自动认证系统_安装包_1.0.2.exe`

- 不传 `/DMyPayloadDir` 时默认用 `..\stage_nuitka\校园网自动认证系统`
- `[Files]` 用的是通配，PyInstaller 的 `_internal` 结构和 Nuitka 的扁平结构都能打
- 升级版本时按「版本与发布」一节改四处；`AppId` 固定，覆盖安装不会装出两份

> 注意：如果安装包放在本项目的目录里直接双击运行无反应/闪退，把它复制到桌面或其他目录再运行。
> 本项目目录带有「禁止删除子项」的安全策略 ACE，会干扰 Inno 安装器初始化。
>
> 另一个原因是系统 `TEMP` 是 8.3 短路径（`echo %TEMP%` 里出现 `USERNA~1` 这种名字）：
> Inno 初始化会直接失败，退出码 1 且不给提示。把 `TEMP` / `TMP` 改成正常路径再运行即可。
> 从资源管理器双击一般不会遇到（那里的 `TEMP` 通常是长路径）。


## 代码签名与 SmartScreen 警告

双击 exe / 安装包时 Windows 可能弹两种警告，来源不同：

| 弹窗 | 触发原因 | 签名能解决吗 |
|---|---|---|
| **UAC：「未知发布者」** | 文件没有数字签名 | ✅ 有受信任证书即可消除 |
| **SmartScreen：「Windows 已保护你的电脑」** | 文件从网上下载（带 Mark-of-the-Web）+ 该程序还没有「应用信誉」 | ⚠️ 只有**受信任 CA 签发**的证书 + 一定信誉才能压住 |

也就是说：**自签名只能消除第一种**，第二种要想彻底没有，必须买正规代码签名证书。

### 三种证书对比

| 方案 | 花费 | 「未知发布者」 | SmartScreen | 适用场景 |
|---|---|---|---|---|
| 不签名 | 0 | 会弹 | 会弹 | 自己用 |
| **自签名** | 0 | 本机及装过证书的机器不弹 | 别人的机器仍会弹 | 小范围、可信同学 |
| **OV 代码签名证书** | 约 ¥1000~3000/年 | 不弹 | 需累积下载量后才逐步消失 | 正规分发 |
| **EV 代码签名证书** | 约 ¥3000~6000/年（硬件令牌） | 不弹 | **立即**获得信誉，基本不弹 | 想要「双击就开」的体验 |

> 学校/社团项目一般用自签名 + 说明就够了；真要发给几百人，再考虑买证书。

### 自签名（本项目已内置）

脚本：`tools\sign.ps1`，已生成证书并签好两个文件。

```powershell
# 第一次：生成自签名证书 + 装进本机信任根 + 签名
powershell -ExecutionPolicy Bypass -File tools\sign.ps1 -SelfSigned -Trust

# 以后只重新签名
powershell -ExecutionPolicy Bypass -File tools\sign.ps1
```

脚本做的事：

1. 生成代码签名证书（`CN=CampusNet AutoLogin`，有效期 3 年），导出公钥到 `tools\CampusNet-自签名证书.cer`
2. `-Trust` 时把它加入「当前用户 → 受信任的根证书颁发机构」
3. 用 SHA256 签名，并尝试加 DigiCert 时间戳（离线时自动改成不带时间戳，仍有效直到证书过期）
4. 用 `Get-AuthenticodeSignature` 复核，打印 `Valid` 才算成功

**签名顺序很重要**（否则安装包里装的是没签名的 exe）：

```
Nuitka 编译 → 签 stage_nuitka\…\校园网自动认证系统.exe → ISCC 打安装包 → 签安装包
```

重新打包后这样复核（两条都应为 `Valid`）：

```powershell
Get-AuthenticodeSignature 'stage_nuitka\校园网自动认证系统\校园网自动认证系统.exe'
Get-AuthenticodeSignature 'installer\output\校园网自动认证系统_安装包_1.0.2.exe'
```

### 让同学的电脑也信任（自签名才需要）

**先别急着装根证书**，大多数情况下面两个办法就够了：

- 双击时点「更多信息 → 仍要运行」（SmartScreen）
- 装完第一次启动若被拦，用 PowerShell 解除「来自网络」标记：
  `Unblock-File .\校园网自动认证系统.exe`

仍然天天弹窗、又完全信任这台机器的来源时，再考虑装根证书。把
`tools\CampusNet-自签名证书.cer` 一起发给对方，然后任选一种：

- 双击 `.cer` → 安装证书 → **当前用户** → 把所有证书放入下列存储 → **受信任的根证书颁发机构** → 完成
- 或用 PowerShell：

```powershell
Import-Certificate -FilePath .\CampusNet-自签名证书.cer -CertStoreLocation Cert:\CurrentUser\Root
```

装完再运行，就只会看到正常发布者名字，不再「未知发布者」。
装之前请核对指纹，避免装错别人的证书：

```powershell
(Get-PfxCertificate .\CampusNet-自签名证书.cer).Thumbprint
# 应为 4F5CAE2594431997B2A2C464791FE09C4C548CFE
```

> 安全提醒：这一步等于让对方信任「你的私钥签出来的任何东西」，只发给信任的同学，
> 万一哪天私钥泄露请立刻吊销/更换证书。

> **不要把这张 `.cer` 公开发到群里/论坛/网盘**：它是自签名**根证书**，谁把它装进
> 「受信任的根证书颁发机构」，谁就会无条件信任持有对应私钥的人签出的**任何**程序
> （不只是本项目）。只对明确要装本工具的同学单独发，装完可随时从
> `certmgr.msc → 受信任的根证书颁发机构 → 证书` 里删掉。
> 仓库里 `tools\CampusNet-自签名证书.cer` 只有公钥（`HasPrivateKey = False`），
> 私钥在本机证书库中，不进仓库；也**别让任何人把他的根证书装进你的电脑**。

### 买了正规证书怎么签

```powershell
# 方式一：证书已装在当前用户证书库（有 CodeSigning 用途）
powershell -ExecutionPolicy Bypass -File tools\sign.ps1 -Subject "CN=你的公司名"

# 方式二：直接给 pfx
powershell -ExecutionPolicy Bypass -File tools\sign.ps1 -Pfx D:\codesign.pfx -PfxPassword 密码
```

签完用 `Get-AuthenticodeSignature 文件.exe` 确认 `Status: Valid`。

### 没签名时的临时办法

同学拿到文件后可以这样绕开拦截（任选其一）：

- 右键 exe → 属性 → 底部勾「**解除锁定**」→ 确定
- 或用 PowerShell：`Unblock-File .\校园网自动认证系统_安装包_1.0.2.exe`
- SmartScreen 弹窗里点「更多信息」→「仍要运行」
- 通过 U 盘/局域网拷贝（没有 Mark-of-the-Web）通常**根本不会弹**

## 杀毒误报（PyInstaller 的常见问题）

把 exe 传上 VirusTotal 常会看到几条红色，例如：

```
Microsoft     Trojan:Win32/Wacatac.B!ml          ← !ml = 机器学习启发式
CrowdStrike   Win/malicious_confidence_90% (D)   ← 同样是 ML 打分
Bkav Pro      W32.Malware.23E62E0C
DeepInstinct  MALICIOUS
SecureAge     Malicious
```

**这不是代码里有病毒**，而是 PyInstaller 打包后的特征（压缩归档 + 自解压引导器 +
无元数据 + 无签名）在这类引擎的模型里得分高，属于业界出名的误报来源。

### 已做的降误报处理

1. 给 exe 加了**图标 + 完整版本信息**（产品名/公司/版本/版权）——无元数据的打包程序最容易被判可疑
2. 每次重新打包都会产生**新哈希**，不会继承旧文件的坏信誉
3. 三个产物全部签名（`Get-AuthenticodeSignature` → `Valid`）

### 想让它真正"变绿"（按性价比排序）

| 方案 | 效果 | 成本 |
|---|---|---|
| **提交误报**给各家厂商 | 多数条目 24~72 小时内移除，通常能降到 0~2 | 免费 |
| **换 Nuitka 打包**（编译成原生 C，PyInstaller 特征消失） | VT 命中通常 0~2，且不再反复被误判 | 需装 C 编译器，构建 5~10 分钟 |
| **买 EV 代码签名证书** | SmartScreen 立即信任；对 VT 有帮助但不保证清零 | 约 ¥3000+/年 |

提交误报入口：

- 微软：<https://www.microsoft.com/en-us/wdsi/filesubmission>（选 "Software developer" → 上传文件 → 说明 "false positive"）
- VirusTotal 详情页每个引擎右侧可点「Submit false positive」，或直接发邮件给各家（Bkav、DeepInstinct、SecureAge 都有 FP 邮箱）

> 注意：上传 VirusTotal 的样本会被公开共享，**别上传含自己账号密码的文件**。
> 本项目 exe 里不含 `config.json`，可以安全上传。

## 版本与发布

当前版本：**1.0.2**，变更见 [CHANGELOG.md](CHANGELOG.md)。

发版时要一起改这四处，否则界面标题、exe 属性和安装包版本会对不上：

| 位置 | 字段 |
|---|---|
| `校园网自动登录.py` | `APP_VERSION` |
| `installer\campusnet.iss` | `MyAppVersion` |
| `tools\version_info.txt` | `filevers` / `prodvers` / `FileVersion` / `ProductVersion` |
| `tools\sign.ps1` | `$Files` 里的安装包文件名 |

## 上传到 GitHub

`.gitignore` 排除了 `config.json`、`login.log`、`stage_nuitka\`、`installer\output\` 等
个人数据和打包产物。推之前先自检：

```bash
git ls-files                    # 只应看到源码、文档、脚本、图标、证书，共 22 个文件
git log --format="%ae" | sort -u # 只应看到 noreply 邮箱，不应出现私人邮箱
git status --short               # 应为空
```

第一次推送：

```bash
git remote add origin https://github.com/<你的用户名>/<仓库名>.git
git push -u origin main
git push origin v1.0.2          # 推标签（Release 要用）
```

exe 和安装包**不进仓库**，作为 [Release](https://docs.github.com/repositories/releasing-projects-on-github)
附件上传：在 GitHub 页面 `Releases → Draft a new release`，选 `v1.0.2` 标签，
把 `installer\output\校园网自动认证系统_安装包_1.0.2.exe` 和
`stage_nuitka\...\校园网自动认证系统.exe` 拖进附件区，正文直接复用 `CHANGELOG.md` 的 1.0.2 段落。

建议同时附上校验值，方便同学核对下载是否完整：

```bash
certutil -hashfile "installer\output\校园网自动认证系统_安装包_1.0.2.exe" SHA256
```

万一以后误提交了 `config.json`，执行 `git rm --cached config.json` 再提交，
并把它从历史里清掉（`git filter-repo`）——里面是你的学号和本机加密的密码。

## 常见问题


- **托盘图标不显示**：未安装 pystray，执行 `pip install pystray pillow`
- **提示缺少 cryptography**：执行 `pip install cryptography`（密码加密必需）
- **开机没自动登录**：托盘菜单确认「开机自动登录」已勾选；任务管理器→启动 中查看启动项是否被禁用
- **登录失败**：查看 `login.log`，对照上面的「常见报错对照」表
- **想彻底退出**：托盘右键 →「退出」

## 适用环境

- 安徽工程大学（本部 / 国际工程师学院校区）有线网、`AHPU`、`ahpu-stu` 无线网
- Windows 10 / 11（桌面版，支持系统托盘）
- Python 3.9+（CI 覆盖 3.9 / 3.12，实测 3.12）
- 适用认证体系：锐捷 ePortal 5.x + CAS 统一身份认证

## 免责声明

> **本项目仅供个人学习、网络协议研究与同校同学自用，与安徽工程大学及其网络管理部门无关，
> 未经授权、未获认可。**
>
> - 程序做的是「代替你手动点登录页」这件事，使用的仍是**你自己的账号和密码**，
>   不会绕过任何认证、不会共享账号、不会修改计费或出口策略
> - 请遵守学校《校园网用户守则》与相关管理规定；因使用本工具（含自动重连频率、
>   账号被风控锁定等）产生的一切后果由使用者自行承担
> - 统一身份认证有失败次数限制，程序已做失败分类与退避（认证类失败会暂停重连），
>   但**请不要把重试间隔改小**，也不要多台设备同时刷同一账号
> - 软件按「原样」提供，不附带任何明示或暗示的担保；作者不对数据丢失、
>   账号异常、网络中断等任何直接或间接损失负责
> - 请勿用于商业用途、二次售卖或任何盈利场景（见下方许可证）

## 许可证与贡献

- 许可证：**PolyForm Noncommercial 1.0.0**（全文见 [LICENSE](LICENSE)）——
  允许个人学习、教学、研究、非商业自用；**禁止任何商业使用**；转发请保留许可证与版权声明
- 这是**非 OSI 开源许可**，GitHub 侧边栏会显示为自定义许可证，属预期
- 发现问题欢迎提 Issue；涉及安全（密码/日志泄露一类）请走私密渠道，见 [SECURITY.md](SECURITY.md)
- 提交代码前请先跑 `python -m pytest -q`，并保持 `git ls-files` 里没有 `config.json`、`login.log` 之类个人数据

