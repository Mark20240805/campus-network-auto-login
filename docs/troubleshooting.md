> [← 返回 README](../README.md) · 相关：[协议原理](protocol.md) · [配置与隐私](config-and-privacy.md)

# 调试与排错 · 常见问题

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

## 常见问题


- **托盘图标不显示**：未安装 pystray，执行 `pip install pystray pillow`
- **认证地址怎么填**：只填「协议 + 主机 + 端口」，例如 `https://sam.ahpu.edu.cn`；
  浏览器复制来的完整链接（带 `wlanuserip`/`nasip`/`sessionId` 一长串参数）整段粘进去，
  点「解析链接」即可自动截成 origin，不要手工保留路径和参数。
  手写的明显非法地址（`http://`、`ftp://…`、主机或端口不合法）在保存时会被拦下并提示原因
- **提示缺少 cryptography**：执行 `pip install cryptography`（密码加密必需）
- **开机没自动登录**：托盘菜单确认「开机自动登录」已勾选；任务管理器→启动 中查看启动项是否被禁用
- **登录失败**：查看 `login.log`，对照上面的「常见报错对照」表
- **想彻底退出**：托盘右键 →「退出」
