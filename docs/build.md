> [← 返回 README](../README.md) · 相关：[排错](troubleshooting.md) · [开发与发布](development.md)

# 构建与分发：打包 · 安装包 · 代码签名 · 杀毒误报

不想自己构建、只想给同学一个安装包时，按本文顺序操作即可；签名与 SmartScreen 的原理与取舍也一并记录在文内。

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
  --file-version=1.0.4.0 --product-version=1.0.4.0 ^
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

产物：`installer\output\校园网自动认证系统_安装包_1.0.4.exe`

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
Get-AuthenticodeSignature 'installer\output\校园网自动认证系统_安装包_1.0.4.exe'
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

### 自签名证书的固有限制（请知悉）

本项目用**自签名证书**签名，这类证书有几点天生的限制，与本项目做得好不好无关：

- **没有第三方背书**：自签名只能证明「同一个私钥签出来的」，不能像 DigiCert/Sectigo
  那样证明「发布者身份经过了审核」。发布者名字（`CampusNet AutoLogin`）是自填的
- **SmartScreen 信誉不会积累**：正规签名要靠下载量积累信誉，自签名永远弹
  「未知发布者」，这是 Windows 的预期行为，不代表文件有毒，也不代表没毒
- **无吊销基础设施**：没有 CRL/OCSP，私钥一旦泄露没有全网自动失效机制
- **签名不等于安全**：签名验证的是「文件自发布后没被篡改」，不保证文件本身无恶意

所以：**不想看弹窗、也不想装任何证书的同学，最稳妥的做法是自己审查源码后自行编译**
（见上文[自行打包 exe](#自行打包-exe) 一节，三种打包方式任选）。自己编译的产物与
你审查过的源码一一对应，不依赖对任何人的信任；发布版与源码的对应关系可以用
Release 附带的 `SHA256SUMS.txt` 核对。

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
- 或用 PowerShell：`Unblock-File .\校园网自动认证系统_安装包_1.0.4.exe`
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
