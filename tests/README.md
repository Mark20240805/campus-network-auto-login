# 测试说明

本项目的最小回归测试集，覆盖上线前检查中反复强调、最容易回归的核心点。

## 运行

```bash
# 需要 pytest（建议用系统自带 tkinter 的 Python 3.9+）
pip install -r requirements-dev.txt
py -3.12 -m pytest          # 或 python -m pytest
```

## 覆盖范围

| 文件 | 覆盖内容 |
|------|---------|
| `test_core.py` | 退避算法、日志脱敏（幂等 + 白名单覆盖）、入口归一化、认证链接解析（ePortal 完整链接 → origin，8 个真实样本 + 失败文案 + 幂等 + 全角 NFKC 归一 + 空主机 + 未加方括号 IPv6 + `#` 锚点计入 detail + 本校门户豁免软提示 + `localhost` 兜底 + 端口笔误与 IPv6 的区分 + `"://"` 短路）、`_ip_literal`、重定向限域（B4）、Cookie 主机隔离（B4）、失败分类（B1） |
| `test_config_log.py` | 配置保存（空值保留旧密文）、明文迁移、原子写、日志轮转（保留 3 份）、`scrub_log` 幂等收敛 |
| `test_portal_parse_edge.py` | 解析边界：userinfo（`user:pass@`）剥离、IPv6 + 非默认端口、网关 IP 保留 http、FQDN 末尾点、一段文字里多个链接（跳过非法候选）、超长粘贴耗时上限；两条不变量——解析绝不发网络请求、返回值不含 `sessionId`/`mac`/`wlanuserip` 原文、`normalize_portal` 老语义兜底、`_redact` 不误伤 origin |
| `test_portal_parse_ui.py` | UI 接线（Fake 对象，不实例化 `CampusApp`）：「解析链接」按钮的六条链路（有链接 / 读剪贴板兜底 / 双空提示 / 失败不改输入框 / 已是 origin 不重复写 / 软警告仍填入）、粘贴自动解析的触发条件、`_collect_config` 保存时截断回写、保存前拦截写了 `://` 却解析不出 origin 的地址（`http://`/`ftp://`/主机/端口非法）；不含 `://` 的单标签主机名（如内网 `portal`）仍放行；粘贴后自动解析对全角链接同样生效 |
| `test_dns_fallback.py` | DNS 缓存读写/过期/原子写失败重试、白名单、连接层回退三分支、仅 `gaierror` 才回退、投毒防护（证书失败清缓存）、体检三态、PowerShell 脚本构造、`repair_dns` 全分支、提权进程退出码/超时终止 |
| `test_reliability.py` | 在线状态三态（2xx + 合法 JSON + 显式布尔才下结论）、授权网络失败归 `KIND_NETWORK`、巡检代际丢弃过期消息、修复 DNS 后忙时补登、DNS 未测不误判恢复 |

## 设计要点

- **不碰真实数据**：测试全部使用**虚构**的学号 / MAC / IP（如 `2023000001`、
  `a1b2c3d4e5f6`、`10.11.12.13`），与任何真实账号无关；`conftest.py` 的 `tmp_paths`
  fixture 把 `CONFIG_FILE` / `LOG_FILE` 指向临时目录，DPAPI 加密被 monkeypatch 成确定值，
  绝不读写你机器上的 `config.json` / `login.log`，也不发起任何真实登录请求。
- **兼容无 Tk 环境**：`conftest.py` 只在 tkinter 缺失时才注入桩模块，让精简版
  Python 也能 import 主模块跑纯函数测试。

## 已知盲区（不在本测试集内）

真实门户认证、网关拦截链路、多网卡选路、托盘 UI、开机自启、安装包升级——
这些依赖真实校园网网关与桌面环境，无法在单测里验证，需真机回归。

另外「修复 DNS」的 **UAC 提权环节**只用注入桩覆盖了全部分支（退出码、超时、取消授权），
没有在真机上实际改过网卡配置；真机验证时请点一次托盘「修复 DNS」确认弹窗与恢复结果。
DNS 被劫持但解析「成功」的场景原理上不会触发 IP 回退，靠投毒防护保证缓存不帮倒忙，
根治需手动修复 DNS。
