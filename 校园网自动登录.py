# -*- coding: utf-8 -*-
"""
校园网自动登录工具（安徽工程大学 · 统一身份认证 + ePortal）
=============================================================
适用认证系统：锐捷 ePortal 5.x（sam.ahpu.edu.cn） + CAS 统一身份认证

功能：
  - 首次运行填写配置（认证地址/学号/密码/运营商），保存为 config.json
  - 认证地址支持粘贴浏览器里复制到的完整 ePortal 链接：点「解析链接」或直接粘贴，
    自动截成 origin（协议 + 主机 + 端口），丢弃 wlanuserip/sessionId 等一次性参数
  - 开机自启（注册表 Run 项，pythonw 静默运行）后自动读取配置登录
  - 常驻系统托盘：右键图标查看状态/立即登录/配置/自启/退出
  - 托盘图标颜色：绿=在线 灰=离线 红=登录失败（悬停显示详细状态）
  - 断线自动重连：每 60 秒检测，离线按退避策略重连（1/2/4/8/15 分钟）；
    密码错、验证码、出口配置无效属于"重试也没用"，会暂停自动重连等人工处理
  - 关闭配置窗口自动最小化到托盘，不退出程序

认证流程（全部实测验证）：
  1. GET  {portal}/eportal/redirect.jsp
          → 302，Location 里带 sessionId / userIp / userMac / nasIp / customPageId
  2. GET  {portal}/cas-sso/login?flowSessionId=…&customPageId=…&nasIp=…&userIp=…&userMac=…
          → 下发 SESSION cookie；页面里两个隐藏字段：
            <p id="login-croypto">      本次 AES 密钥（随机）
            <p id="login-page-flowkey"> execution
  3. POST {portal}/cas-sso/login?<同上>  (application/x-www-form-urlencoded)
          username / type=UsernamePassword / _eventId=submit / geolocation=
          execution=<flowkey> / captcha_code=
          croypto=<密钥>
          password        = base64(AES-128-ECB-PKCS7(明文, 密钥))
          captcha_payload = base64(AES-128-ECB-PKCS7("{}", 密钥))
          → 302，Location 里带 ticket=ST-…（失败则回渲染登录页并给 login-error-msg）
  4. GET  <ticket 跳转>                      建立 ePortal 会话
  5. POST {portal}/eportal/network/serviceSelection {sessionId}          → 运营商列表
  6. POST {portal}/eportal/network/serviceLogin     {sessionId, service} → authResult=success
  7. POST {portal}/eportal/network/userOnline       {sessionId}          → online=true

说明：
  - 账号是**学号**（统一身份认证），没有 @dx/@lt 之类的运营商后缀；
    "运营商"是在认证页选的服务名（中国电信/中国移动/中国联通/校园网）。
  - 校园网对师生免费，未办理运营商套餐的选"校园网"即可。

用法：
  python 校园网自动登录.py            # 配置窗口 + 托盘（已有配置则直接托盘+自动登录）
  python 校园网自动登录.py --login    # 仅托盘静默模式（开机自启使用）
  python 校园网自动登录.py --status   # 只查在线状态，打印到控制台
  python 校园网自动登录.py --selftest # 自检：写日志 + 弹窗（打包版没有控制台也能用）

依赖：pystray、Pillow、cryptography（pip install pystray pillow cryptography）
配置保存在同目录 config.json，日志写入同目录 login.log。

许可：GNU GPL-3.0-or-later（全文见 LICENSE 文件）。
Copyright (C) 2026 Mark20240805
本程序可自由使用、修改与再分发；再分发（含打包后的 exe）时必须以同一许可
提供源码。本程序不附带任何担保，详见 GPL-3.0 第 15、16 条。
"""

import json
import os
import queue
import re
import ssl
import sys
import http.client
import http.cookiejar
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import base64
import collections
import ctypes
import ctypes.wintypes
from datetime import datetime

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext

try:
    import winreg
except ImportError:  # 非 Windows 平台禁用自启功能
    winreg = None

# pystray / Pillow 改为延迟导入（见 tray_available()）：
# 它们只在需要托盘时才用得上，能省掉启动路径上的一部分导入时间。
_TRAY_MODS = {}
TRAY_AVAILABLE = None      # None=尚未探测


def tray_available():
    """探测并缓存 pystray/Pillow 是否可用（首次调用时才真正导入）"""
    global TRAY_AVAILABLE
    if TRAY_AVAILABLE is None:
        try:
            import pystray
            from PIL import Image, ImageDraw
            _TRAY_MODS.update(pystray=pystray, Image=Image, ImageDraw=ImageDraw)
            TRAY_AVAILABLE = True
        except ImportError:
            TRAY_AVAILABLE = False
    return TRAY_AVAILABLE


# PyInstaller 会设 sys.frozen；Nuitka 官方明确不设（改用模块属性 __compiled__）。
# 只认 sys.frozen 的话，Nuitka 打包版会被当成"源码运行"，自启命令里会写出一个
# 并不存在的 .py 参数（旧版 1.0.3 打包版就因此写出了错误的开机自启命令）。
IS_FROZEN = bool(getattr(sys, "frozen", False)) or "__compiled__" in globals()


def _packaged_exe_path():
    """打包版真实可执行文件路径

    Nuitka 打包后 sys.executable 可能仍指向打包时用的 python.exe（本项目实测：
    指向 E:\\CampusNetAutoLogin\\python.exe），直接用它会让 config.json / login.log
    和开机自启项都落到构建机的路径上。这里优先用 Win32 取当前进程的真实模块路径，
    再退回 sys.argv[0]、sys.executable。
    """
    if os.name == "nt":
        try:
            import ctypes as _ct
            buf = _ct.create_unicode_buffer(32768)
            n = _ct.windll.kernel32.GetModuleFileNameW(None, buf, len(buf))
            if n and os.path.exists(buf.value):
                return buf.value
        except Exception:
            pass
    try:
        argv0 = os.path.abspath(sys.argv[0]) if sys.argv and sys.argv[0] else ""
        if argv0 and os.path.exists(argv0):
            return argv0
    except Exception:
        pass
    return os.path.abspath(sys.executable)


if IS_FROZEN:  # 打包后（PyInstaller / Nuitka），资源放在 exe 同目录
    APP_EXE = _packaged_exe_path()
    SCRIPT_DIR = os.path.dirname(APP_EXE)
else:
    APP_EXE = ""
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(SCRIPT_DIR, "config.json")
LOG_FILE = os.path.join(SCRIPT_DIR, "login.log")
LOG_MAX_BYTES = 1024 * 1024   # 单份日志上限，超过就轮转
LOG_BACKUPS = 3               # 保留 login.log.1 ~ .3

AUTOSTART_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_NAME = "CampusNetAutoLogin"
MUTEX_NAME = "Local\\CampusNetAutoLogin_singleton"
_SINGLETON_HANDLE = None    # 互斥量句柄要一直持有，进程退出时由系统释放


def cleanup_stale_mei():
    """清理单文件版残留的解包目录

    打包时用了 `--runtime-tmpdir .`，解包目录会出现在 exe 旁边（_MEIxxxx）。
    正常退出会自动删除，异常退出（比如强杀）会留下，这里清掉一小时以前的。
    """
    if not IS_FROZEN:
        return
    import glob
    import shutil
    try:
        cur = os.path.abspath(getattr(sys, "_MEIPASS", "") or "")
        now = time.time()
        for d in glob.glob(os.path.join(SCRIPT_DIR, "_MEI*")):
            if not os.path.isdir(d) or os.path.abspath(d) == cur:
                continue
            if now - os.path.getmtime(d) < 3600:   # 一小时内不动，避免误删运行中的实例
                continue
            shutil.rmtree(d, ignore_errors=True)
    except Exception:
        pass

DEFAULT_PORTAL = "https://sam.ahpu.edu.cn"
APP_VERSION = "1.0.4"      # 发版时和 installer\campusnet.iss、tools\version_info.txt 一起改
# 兜底列表：正常从 serviceSelection 接口实时拉取
DEFAULT_SERVICES = ["校园网", "中国电信", "中国移动", "中国联通"]
# 新建配置的默认出口：没办运营商套餐的同学走校园网。
# 默认成某家运营商，可能在用户不知情的情况下走计费出口。
DEFAULT_SERVICE = "校园网"

MONITOR_INTERVAL = 60000  # 断线检测间隔（毫秒）

# ---- 重试策略 ----
# 网络类失败（不在校园网、网关还没起来）指数退避后重试，最长 15 分钟一次；
# 认证类失败（密码错、要验证码、出口配置无效）不再自动重试，
# 否则每分钟往统一身份认证提交一次失败密码，很容易把账号刷到锁定。
RETRY_BASE_DELAY = 60      # 秒，首次失败后等 60 秒
RETRY_MAX_DELAY = 900      # 秒，退避上限 15 分钟
LOCAL_IP_COOLDOWN = 180    # 秒，网卡探测全失败后的冷却时间
CHECK_TIMEOUT = 5          # 秒，在线状态查询的单次请求上限
CAS_DAILY_LIMIT = 20       # 每天最多向统一身份认证提交密码的次数，超了转手动
                           # （服务端业务错误会退避重试并每次重提密码，不设上限
                           #   会在一天内把账号刷到风控/锁定）

# do_login / cas_login 的失败分类，决定调用方是退避重试还是暂停自动重连
KIND_OK = "ok"
KIND_NETWORK = "network"
KIND_MANUAL = "manual"
KIND_OTHER = "other"

# ---- 启动等待参数 ----
# 单次探测的超时：宁可多试几次，也不要一次卡 20 秒
PROBE_TIMEOUT = 2.0        # 秒，单次请求上限
PROBE_INTERVAL = 0.3       # 秒，两次探测之间的间隔
READY_TIMEOUT = 45         # 秒，等待网卡/网关就绪的总时长上限

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# TLS：默认校验证书。门户证书实测是 DigiCert 签发的 *.ahpu.edu.cn，校验能过。
# 密码是用登录页下发的密钥加密的，而"下发密钥"走的正是这条连接——
# 关掉校验等于把密文和密钥一起交给中间人，所以只在下面一种情况降级：
#   目标是 IP 字面量（网关 10.x，不可能有合法证书）。
# 域名主机的证书错误一律硬失败并记日志——同网段 MITM 用自签证书即可诱导降级，
# 从而截获密钥与密码密文，所以域名侧绝不自动降级。
_SSL_CTX = ssl.create_default_context()
_SSL_CTX_INSECURE = ssl.create_default_context()
_SSL_CTX_INSECURE.check_hostname = False
_SSL_CTX_INSECURE.verify_mode = ssl.CERT_NONE

_TLS_INSECURE_HOSTS = set()      # 已确认"不校验证书"的 IP 字面量（网关 10.x），本次运行内复用
_TLS_FALLBACK_LOCK = threading.Lock()

TITLE_FONT = ("Microsoft YaHei", 15, "bold")
LABEL_FONT = ("Microsoft YaHei", 10)
LOG_FONT = ("Microsoft YaHei", 9)


# ---------------------------------------------------------------- 基础工具

# 写日志前的脱敏规则：学号、CAS 票据、会话号、MAC 不该以明文留在磁盘上。
# userIp 保留——排查网络问题要靠它定位网段。
_REDACT_RULES = [
    (re.compile(r"(学号[：:\s]{0,3})(\d{4})\d{4,}"), r"\1\2******"),
    (re.compile(r"[（(](\d{4})\d{6,}[）)]"), r"（\1******）"),
    (re.compile(r"(ticket=ST-)[^\s&\"']+"), r"\1***"),
    # 尾部的 [0-9a-zA-F*]* 同时吃掉十六进制和已打上的星号，保证重复调用幂等。
    # 会话号 / MAC 的形态很杂，按"键名 + 值"逐条覆盖，见下。
    (re.compile(r"(sessionId=)([0-9a-zA-Z]{4})[0-9a-zA-Z*]*"), r"\1\2****"),
    (re.compile(r"(userMac=)([0-9a-fA-F:]{6})[0-9a-fA-F:*]*"), r"\1\2******"),
    # 网关 NAS 参数用的是 mac= 而不是 userMac=
    (re.compile(r"(mac=)([0-9a-fA-F]{6})[0-9a-fA-F:*]*"), r"\1\2******"),
    # JSON 形式 {"sessionId":"..."} / {"userMac":"..."} / {"mac":"..."}
    (re.compile(r'(sessionId["\s:]+)([0-9a-zA-Z]{4})[0-9a-zA-Z*]*'), r"\1\2****"),
    (re.compile(r'(userMac["\s:]+)([0-9a-fA-F:]{6})[0-9a-fA-F:*]*'), r"\1\2******"),
    (re.compile(r'(mac["\s:]+)([0-9a-fA-F]{6})[0-9a-fA-F:*]*'), r"\1\2******"),
    # 冒号分隔 MAC（如 aa:bb:cc:dd:ee:ff），不带键名，兜底
    (re.compile(r"\b([0-9a-fA-F]{2}:){5}([0-9a-fA-F]{2})\b"), r"**:**:**:**:**:**"),
    # 无键名、无冒号的连续 12 位十六进制（如「终端MAC（a1b2c3d4e5f6）」），
    # 字母+数字混合且长度≥12，纯数字的学号已被前面的规则处理，不会误伤
    (re.compile(r"(?<![0-9a-fA-F])([0-9a-fA-F]*[a-fA-F][0-9a-fA-F]*)(?![0-9a-fA-F])"),
     lambda m: "******" if len(m.group(1)) >= 12 else m.group(0)),
    # password= 明文兜底（理论上不会出现，但万一明文落盘就兜住）
    (re.compile(r'(password["\s:=]+)[^\s&"\',}]+'), r"\1***"),
]


def _redact(text):
    """把文本里的学号、票据、会话号、MAC 打码（幂等，可重复调用）"""
    s = str(text)
    for pattern, repl in _REDACT_RULES:
        s = pattern.sub(repl, s)
    return s


def _rotate_log():
    """login.log 超过 LOG_MAX_BYTES 就滚动，最多保留 LOG_BACKUPS 份"""
    try:
        if os.path.getsize(LOG_FILE) <= LOG_MAX_BYTES:
            return
    except OSError:
        return
    for i in range(LOG_BACKUPS - 1, 0, -1):
        src = "%s.%d" % (LOG_FILE, i)
        if os.path.exists(src):
            try:
                os.replace(src, "%s.%d" % (LOG_FILE, i + 1))
            except OSError:
                pass
    try:
        os.replace(LOG_FILE, LOG_FILE + ".1")
    except OSError:
        pass


def clear_logs():
    """删掉当前日志和所有轮转备份"""
    for path in [LOG_FILE] + ["%s.%d" % (LOG_FILE, i)
                             for i in range(1, LOG_BACKUPS + 1)]:
        try:
            os.remove(path)
        except OSError:
            pass


def scrub_log():
    """把历史日志按当前规则重写一遍（升级后清掉旧版本留下的明文）"""
    try:
        if not os.path.exists(LOG_FILE):
            return
        with open(LOG_FILE, encoding="utf-8", errors="replace") as f:
            old = f.readlines()
        new = [_redact(line.rstrip("\n")) + "\n" for line in old]
        if new == old:
            return
        tmp = LOG_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.writelines(new)
        os.replace(tmp, LOG_FILE)
        log("历史日志已按新规则脱敏")
    except Exception:
        pass


def log(message):
    """写日志文件（敏感字段自动打码）；控制台运行时同时打印"""
    _rotate_log()
    line = "[%s] %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        _redact(message))
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    try:
        print(line)
    except Exception:
        pass


def normalize_portal(url):
    """把用户填写的认证地址归一成 origin（协议 + 主机 + 端口）

    认证地址只该是 origin：后续所有请求都是 `portal + "/eportal/redirect.jsp"`
    这样拼出来的。用户从浏览器复制来的却是整串带参数的完整链接，直接拼接会变成
    `…/eportal/index.jsp?wlanuserip=…/eportal/redirect.jsp`，认证必然失败。

    这里用 parse_portal_link 截掉路径与参数；解析不出来时退回"补 https:// + 去尾斜杠"
    的老逻辑（保持宽松，不改变原有返回值语义，老配置照样能用）。
    解析规则见下方「认证链接解析」一节（定义在后，运行时才调用）。
    """
    raw = (url or "").strip()
    if not raw:
        return ""
    portal = parse_portal_link(raw).portal
    if portal:
        return portal
    raw = raw.rstrip("/")
    if raw and "://" not in raw:
        raw = "https://" + raw
    return raw


def retry_delay(fail_streak):
    """第 n 次失败后要等多少秒（指数退避，封顶 RETRY_MAX_DELAY）"""
    n = max(1, min(int(fail_streak), 10))
    return min(RETRY_BASE_DELAY * (2 ** (n - 1)), RETRY_MAX_DELAY)


def _ip_literal(host):
    """是不是 IPv4 字面量（网关 IP 不可能有合法证书，直接不校验）"""
    parts = (host or "").split(".")
    return len(parts) == 4 and all(p.isdigit() for p in parts)


# ---------------------------------------------------------------- 认证链接解析
#
# 用户从浏览器复制到的是「完整认证链接」，形如
#   https://sam.ahpu.edu.cn/eportal/index.jsp?wlanuserip=…&nasip=…&mac=…&t=wireless-v2
# 后缀那些参数是 NAS 每次会话随机下发的一次性参数，无法复用；直接当认证地址用会拼出
#   …/eportal/index.jsp?…&t=wireless-v2/eportal/redirect.jsp
# 于是必然失败，而且报错信息看不出原因。这里把它截成 origin（协议 + 主机 + 端口）。
#
# 约束：
#   - 全是纯函数，不发任何网络请求（用户粘链接时往往还没连上校园网）；
#   - 不在日志/弹窗里回吐链接原文（含 sessionId / mac / wlanuserip / userIp），
#     只输出 origin 和"丢弃了几个参数"这类计数。

ParseResult = collections.namedtuple("ParseResult", "portal error warning detail")

ERR_PORTAL_EMPTY = "请先粘贴完整的网络认证链接（浏览器地址栏里 eportal 那一串）。"
ERR_PORTAL_NO_LINK = ("没有识别到有效的认证链接。"
                      "请粘贴以 http:// 或 https:// 开头的完整认证链接。")
ERR_PORTAL_SCHEME = "只支持 http/https 链接，当前是「%s」。"
ERR_PORTAL_HOST = "链接里的主机「%s」不是合法的域名或 IP 地址。"
ERR_PORTAL_PORT = "链接里的端口「%s」无效（应为 1–65535）。"
ERR_PORTAL_IPV6 = "链接里的 IPv6 地址缺少方括号，请写成 https://[地址]/… 的形式。"
WARN_PORTAL_NOT_EPORTAL = "未检测到锐捷 ePortal 链接特征，请确认这是校园网认证链接。"

SCAN_LIMIT = 4096                 # 只扫描前 4096 字符，避免粘贴超大文本卡住界面
_ZERO_WIDTH_CHARS = ("\u200b", "\u200c", "\u200d", "\ufeff")
# 复制时常被一起带上的成对引号/尖括号/中文书名号，首尾一并去掉
_LINK_EDGE_CHARS = "\"'<>“”‘’《》【】()"
# 粘在链接尾部、但不属于链接的标点（中文句子里的"点击…登录"这类）
_LINK_TAIL_CHARS = ").,;，。；、）】>\"'”’》"
# 判断"像不像锐捷 ePortal 链接"的特征词，只用于软提示
_EPORTAL_HINTS = ("eportal", "portal", "cas-sso", "wlanuserip", "nasip",
                  "userip", "sessionid")
_RE_HTTP_LINK = re.compile(r"https?://[^\s'\"<>]+", re.I)
_RE_ANY_SCHEME = re.compile(r"([A-Za-z][A-Za-z0-9+.\-]*)://")
_RE_NETLOC = re.compile(r"^(?:[^@/?#]*@)?(\[[^\]]*\]|[^:?#/]*)(?::([^?#/]*))?$")
_RE_DOMAIN_LABEL = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9\-]*[A-Za-z0-9])?$")


def _clean_link_text(text):
    """PR-1 预处理：去零宽字符，全角字符 NFKC 归一，解 HTML 实体，剥首尾成对引号

    从 PDF / Word / 微信里复制出来的链接常是全角：`ＨＴＴＰＳ：／／sam.ahpu.edu.cn／
    eportal／x？a=1`。NFKC 会把全角字母、数字、标点一起压成半角，比逐个替换可靠；
    正文字符（汉字、日文假名）不受影响，正常 ASCII 链接的归一结果就是它自己。
    """
    s = str(text or "")
    for ch in _ZERO_WIDTH_CHARS:
        if ch in s:
            s = s.replace(ch, "")
    s = unicodedata.normalize("NFKC", s)
    # NFKC 已覆盖下面两个全角符号，显式替换保留着当兜底（幂等，无副作用）
    s = s.replace("\uff1a", ":").replace("\uff0f", "/")   # 全角 ： ／
    s = s.replace("&amp;", "&").replace("&AMP;", "&")
    s = s.strip()[:SCAN_LIMIT]
    # 首尾可能套了多层引号/书名号，循环剥到不再变化为止
    prev = None
    while prev != s:
        prev = s
        s = s.strip().strip(_LINK_EDGE_CHARS).strip()
    return s


def _valid_ipv4(host):
    """逐段校验的合法 IPv4（区别于只判形态的 _ip_literal）"""
    parts = (host or "").split(".")
    if len(parts) != 4:
        return False
    for p in parts:
        if not p.isdigit() or len(p) > 3 or (len(p) > 1 and p[0] == "0"):
            return False
        if int(p) > 255:
            return False
    return True


def _valid_host(host):
    """PR-5 主机校验：合法 IPv4 / IPv6 / 域名（额外放行 localhost）"""
    host = (host or "").lower().rstrip(".")
    if not host:
        return False
    if host == "localhost":
        return True
    if ":" in host:                       # IPv6 字面量（urlsplit 已去掉方括号）
        import ipaddress
        try:
            ipaddress.IPv6Address(host)
            return True
        except ValueError:
            return False
    if _valid_ipv4(host):
        return True
    labels = host.split(".")
    if len(labels) < 2:
        return False
    if len(labels) == 4 and all(p.isdigit() for p in labels):
        return False                      # 形如 IPv4 但取值非法（如 256.1.1.1）
    if labels[-1].isdigit():
        return False                      # 顶级域不能是纯数字
    for label in labels:
        if not label or len(label) > 63 or not _RE_DOMAIN_LABEL.match(label):
            return False
    return True


def _split_netloc(netloc):
    """把 netloc 拆成 (host 原文, port 原文)

    保留方括号（IPv6 重建时要用），port 原文用于报错文案——urlsplit().port 只会
    抛 ValueError，拿不到用户到底写了什么。
    """
    matched = _RE_NETLOC.match(netloc or "")
    if not matched:
        return (netloc or ""), ""
    return matched.group(1) or "", matched.group(2) or ""


def _parse_origin(candidate):
    """把候选链接截成 origin（PR-3/PR-4/PR-5）

    返回 (portal, kind, arg)：kind 为 "ok" / "scheme" / "ipv6" / "empty" /
    "host" / "port"；成功时 portal 是 origin、arg 为空串；失败时 portal 为空串、
    arg 是原始片段（协议前缀 / 主机名 / 端口号），由上层拼成固定的失败文案。
    """
    try:
        parts = urllib.parse.urlsplit(candidate)
    except ValueError:
        return "", "host", _split_netloc((candidate or "").split("://")[-1])[0]
    scheme = (parts.scheme or "").lower()
    if scheme not in ("http", "https"):
        return "", "scheme", scheme + "://"
    port_raw = _split_netloc(parts.netloc)[1]
    host = (parts.hostname or "").lower().rstrip(".")
    # 端口原文里出现冒号有两种可能，别一棍子打成"IPv6 语法错"：
    #   a) 裸 IPv6（https://2001:db8::1/x）会被 urlsplit 拆成 host=2001 /
    #      port=db8::1，此时 host 本身不合法 → 提示补方括号；
    #   b) 端口笔误（https://sam.ahpu.edu.cn:8:0/x）host 是合法域名/IP，
    #      只是端口里多打了冒号 → 报端口无效，指向才对。
    if ":" in port_raw:
        if _valid_host(host):
            return "", "port", port_raw
        return "", "ipv6", ""
    if not host:                          # "https://" / "https:///x" 这类空主机
        return "", "empty", ""
    if not _valid_host(host):
        return "", "host", host
    port = 0
    if port_raw:
        try:
            port = parts.port
        except ValueError:
            return "", "port", port_raw
        if not port or not (1 <= port <= 65535):
            return "", "port", port_raw
    netloc = "[%s]" % host if ":" in host else host
    if (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
        port = 0                          # PR-4：默认端口不写进 origin
    if port:
        netloc += ":%d" % port
    return "%s://%s" % (scheme, netloc), "ok", ""


def _is_default_portal(portal):
    """origin 的主机是不是默认门户（本校）主机

    `https://sam.ahpu.edu.cn/#/login` 这种本校门户的裸地址/前端路由里本来就不会出现
    eportal 之类的特征词，不该被当成"不是校园网认证链接"来提示。
    """
    host = (urllib.parse.urlsplit(portal or "").hostname or "").lower()
    default_host = (urllib.parse.urlsplit(DEFAULT_PORTAL).hostname or "").lower()
    return bool(host) and host == default_host


def _success_result(portal, text, candidate, added_scheme):
    """构造解析成功的结果：detail 说明丢了什么，warning 做 ePortal 特征软提示"""
    path = ""
    n_params = 0
    fragment = ""
    try:
        parts = urllib.parse.urlsplit(candidate)
        path = (parts.path or "").strip()
        if path in ("", "/"):
            path = ""
        query = parts.query or ""
        if query:
            n_params = len([seg for seg in query.split("&") if seg.strip()])
        fragment = (parts.fragment or "").strip()
    except ValueError:
        path, n_params, fragment = "", 0, ""

    steps = []
    if added_scheme:
        steps.append("已补全 https://")
    if path and n_params:
        steps.append("已忽略路径与 %d 个参数" % n_params)
    elif n_params:
        steps.append("已忽略 %d 个参数" % n_params)
    elif path:
        steps.append("已忽略路径")
    if fragment:
        steps.append("已忽略 # 锚点")      # 前端路由锚点（如 #/login）同样要丢
    detail = "，".join(steps) if steps else "链接已是纯认证地址"

    warning = ""
    # 只有真的丢弃了东西才做特征检查：已是纯 origin 的输入不该再加提示（PR-8）。
    # 本校门户主机（如 https://sam.ahpu.edu.cn/#/login）直接豁免，避免自相矛盾。
    if (path or n_params or fragment) and not _is_default_portal(portal) and not any(
            k in (text or "").lower() for k in _EPORTAL_HINTS):
        warning = WARN_PORTAL_NOT_EPORTAL
    return ParseResult(portal, "", warning, detail)


def parse_portal_link(text):
    """把用户粘贴的完整认证链接解析为有效认证地址（origin）。

    portal:  成功时的认证地址（失败为 ""）
    error:   失败原因（成功为 ""）
    warning: 软提示（可为 ""，例如"不是 ePortal 链接"）
    detail:  成功时的说明（例如"已忽略路径与 6 个参数"）

    纯函数：不读配置、不发网络请求、不返回链接原文（原文里的 sessionId /
    mac / wlanuserip 不能进日志）。
    """
    s = _clean_link_text(text)
    if not s:
        return ParseResult("", ERR_PORTAL_EMPTY, "", "")

    # PR-2：优先找第一个 http(s) 链接；host 非法的候选跳过、继续试下一个
    first_err = ("", "")
    pos = 0
    while True:
        found = _RE_HTTP_LINK.search(s, pos)
        if not found:
            break
        candidate = found.group(0).rstrip(_LINK_TAIL_CHARS)
        portal, kind, arg = _parse_origin(candidate)
        if kind == "ok":
            return _success_result(portal, s, candidate, False)
        if not first_err[0]:
            first_err = (kind, arg)
        pos = found.end()
    if first_err[0] == "host":
        return ParseResult("", ERR_PORTAL_HOST % (first_err[1] or ""), "", "")
    if first_err[0] == "port":
        return ParseResult("", ERR_PORTAL_PORT % (first_err[1] or ""), "", "")
    if first_err[0] == "ipv6":
        return ParseResult("", ERR_PORTAL_IPV6, "", "")
    if first_err[0] == "scheme":
        return ParseResult("", ERR_PORTAL_SCHEME % first_err[1], "", "")
    if first_err[0] == "empty":
        # "https://" / "https:///x"：没有主机，归到"没识别到有效链接"
        return ParseResult("", ERR_PORTAL_NO_LINK, "", "")

    # 没有 http(s) 链接：先看是不是别的协议，给"只支持 http/https"的明确提示。
    # 先做 "://" 的廉价短路：整串没有 :// 时这条正则没必要跑（超长无空格文本下
    # 回溯会到几十毫秒）。
    if "://" in s:
        other = _RE_ANY_SCHEME.search(s)
        if other and other.group(1).lower() not in ("http", "https"):
            return ParseResult("", ERR_PORTAL_SCHEME % (other.group(1).lower() + "://"),
                               "", "")

    # PR-2 兜底：用户只复制了 sam.ahpu.edu.cn/eportal/index.jsp?…（没有协议头）。
    # 仅当整串不含空白、且首段确实像个主机时才补 https://。主机的判定口径与
    # _valid_host 保持一致：含点 / IP 字面量 / localhost。
    if not any(ch.isspace() for ch in s):
        head = re.split(r"[/?#]", s, 1)[0]
        host_head = _split_netloc(head)[0].strip("[]").rstrip(".").lower()
        if "." in host_head or _ip_literal(host_head) or host_head == "localhost":
            candidate = "https://" + s
            portal, kind, arg = _parse_origin(candidate)
            if kind == "ok":
                return _success_result(portal, s, candidate, True)
            if kind == "port":
                return ParseResult("", ERR_PORTAL_PORT % (arg or ""), "", "")
            if kind == "ipv6":
                return ParseResult("", ERR_PORTAL_IPV6, "", "")
            # host 非法（含"主机没有点"）：与"找不到链接"归为一类
    return ParseResult("", ERR_PORTAL_NO_LINK, "", "")


# 允许跟随重定向 / 外发 Cookie 的主机白名单：门户域名及其子域 + 网关 IP 字面量。
# 认证全程都发生在 sam.ahpu.edu.cn（CAS 也在 *.ahpu.edu.cn 域内），
# 其余目标一律不放行，避免 SESSION cookie / ST 票据被带到陌生主机。
_ALLOWED_REDIRECT_SUFFIXES = ("ahpu.edu.cn",)


def _redirect_allowed(from_url, to_url):
    """判断重定向目标是否可跟随：
    1) 目标是 IP 字面量（网关 10.x 等）→ 允许；
    2) 目标 host 是 *.ahpu.edu.cn（含主域）→ 允许；
    3) 目标与来源同 host（子域内跳转）→ 允许；
    其余一律拒绝。
    """
    to_host = (urllib.parse.urlsplit(to_url).hostname or "").lower()
    if not to_host:
        return True
    if _ip_literal(to_host):
        return True
    if to_host == "ahpu.edu.cn" or to_host.endswith(".ahpu.edu.cn"):
        return True
    from_host = (urllib.parse.urlsplit(from_url).hostname or "").lower()
    return from_host == to_host


def _is_cert_error(exc):
    """判断异常是不是 TLS 证书校验失败（不同路径抛的异常类型不一样）"""
    if isinstance(exc, ssl.SSLCertVerificationError):
        return True
    text = str(exc)
    return "CERTIFICATE_VERIFY_FAILED" in text or "certificate verify failed" in text


def _aes_ecb_b64(key_b64, text):
    """统一身份认证用：base64(AES-128-ECB-PKCS7(text, key))，key 为 base64 字符串"""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    key = base64.b64decode(key_b64)
    data = text.encode("utf-8")
    n = 16 - len(data) % 16
    data += bytes([n]) * n
    enc = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    return base64.b64encode(enc.update(data) + enc.finalize()).decode()


_PREFERRED_LOCAL_IP = None   # 自动探测出的"能通认证门户"的本机网卡地址
_LOCAL_IP_NEXT_PROBE = 0.0   # 全网卡都失败后，下次允许再逐张探测的时间点（墙钟）


def local_ipv4_list():
    """枚举本机可用 IPv4（排除回环、169.254 自动地址）"""
    import socket
    ips = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip.startswith("127.") or ip.startswith("169.254.") or ip in ips:
                continue
            ips.append(ip)
    except Exception:
        pass
    return ips


class PortalSession:
    """带 cookie 的 HTTP 会话（CAS 强依赖 SESSION cookie）

    多网卡环境（有线 + 无线 + 虚拟网卡）下，系统默认路由可能不走校园网，
    导致拿不到带 sessionId 的重定向。这里支持 source_address 绑定，
    主动指定从哪张网卡发出请求。
    """

    def __init__(self, portal=DEFAULT_PORTAL, local_ip=None, timeout=20):
        self.portal = normalize_portal(portal) or DEFAULT_PORTAL
        self.local_ip = local_ip if local_ip is not None else _PREFERRED_LOCAL_IP
        # Cookie 按 host 隔离：{host: {name: value}}。
        # 之前是全局 dict，任何请求都会带上全部 Cookie，CAS 的 SESSION 会被
        # 明文探测地址（msftconnecttest.com 等）外发出去。
        self.cookies = {}
        self.timeout = timeout

    @staticmethod
    def _cookie_host(host):
        """把 host 归一化成 cookie 桶的键：域名取"注册域"（主域 + 有效后缀），
        让 sam/cas 等子域共享 cookie；IP 字面量原样返回（不同 IP 不共享）。

        例：sam.ahpu.edu.cn / cas.ahpu.edu.cn → ahpu.edu.cn（共享）
            www.evil.com → evil.com
        """
        host = (host or "").lower()
        if _ip_literal(host):
            return host
        parts = host.split(".")
        # 双字符一级后缀（.cn/.com/.net 等）常见前面带二级组织后缀：
        # ahpu.edu.cn 的注册域是 ahpu.edu.cn，而非 edu.cn。
        # 简单稳妥的做法：保留最后 3 段；不足 3 段则全保留。
        if len(parts) >= 3:
            return ".".join(parts[-3:])
        return host

    def _cookies_for(self, url):
        """取该请求该发送的 Cookie：host 精确匹配或同主域共享；跨域一律不外发"""
        host = (urllib.parse.urlsplit(url).hostname or "").lower()
        if not host:
            return ""
        key = self._cookie_host(host)
        bucket = self.cookies.get(key) or self.cookies.get(host) or {}
        if not bucket:
            return ""
        return "; ".join("%s=%s" % kv for kv in bucket.items())

    def _store_cookie(self, url, name, value):
        """把 Set-Cookie 存进它所属 host 的桶里"""
        host = (urllib.parse.urlsplit(url).hostname or "").lower()
        if not host:
            return
        key = self._cookie_host(host)
        self.cookies.setdefault(key, {})[name] = value

    def _conn(self, url, timeout=None):
        timeout = timeout or self.timeout
        parts = urllib.parse.urlsplit(url)
        scheme = parts.scheme or "https"
        port = parts.port or (443 if scheme == "https" else 80)
        src = (self.local_ip, 0) if self.local_ip else None
        if scheme == "https":
            host = parts.hostname
            ctx = (_SSL_CTX_INSECURE
                   if (_ip_literal(host) or host in _TLS_INSECURE_HOSTS)
                   else _SSL_CTX)
            return http.client.HTTPSConnection(host, port, timeout=timeout,
                                               context=ctx, source_address=src)
        return http.client.HTTPConnection(parts.hostname, port, timeout=timeout,
                                          source_address=src)

    def request(self, url, data=None, headers=None, follow=True, method=None, _depth=0):
        parts = urllib.parse.urlsplit(url)
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        h = {"User-Agent": UA,
             "Accept": "*/*",
             "Accept-Language": "zh-CN,zh;q=0.9",
             "Accept-Encoding": "identity",
             "Host": parts.netloc}
        cookie = self._cookies_for(url)
        if cookie:
            h["Cookie"] = cookie
        if headers:
            h.update(headers)
        try:
            conn = self._conn(url)
            conn.request(method or ("POST" if data is not None else "GET"),
                         path, body=data, headers=h)
            r = conn.getresponse()
            body = r.read()
            status = r.status
            hdrs = {}
            for k, v in r.getheaders():
                if k.lower() == "set-cookie":
                    name, _, rest = v.partition("=")
                    self._store_cookie(url, name.strip(), rest.split(";")[0])
                else:
                    hdrs[k] = v
            conn.close()
        except Exception as e:
            host = urllib.parse.urlsplit(url).hostname
            # B3：只有 IP 字面量（网关 10.x，不可能有合法证书）才允许免校验。
            # 域名主机的证书错误一律硬失败——否则同网段 MITM 用自签证书即可诱导
            # 降级，从而截获登录页下发的密钥和密码密文。
            if host and _is_cert_error(e) and _ip_literal(host):
                with _TLS_FALLBACK_LOCK:
                    first = host not in _TLS_INSECURE_HOSTS
                    _TLS_INSECURE_HOSTS.add(host)
                if first:
                    log("IP 网关证书无法校验（%s），本次运行对其不校验证书: %s"
                        % (host, e))
                return self.request(url, data=data, headers=headers, follow=follow,
                                    method=method, _depth=_depth)
            if host and _is_cert_error(e):
                log("域名 %s 的 TLS 证书校验失败，已拒绝连接（可能被劫持）: %s"
                    % (host, e))
            return 0, {}, str(e)

        loc = hdrs.get("Location") or hdrs.get("location") or ""
        if follow and status in (301, 302, 303, 307, 308) and loc and _depth < 6:
            nxt = urllib.parse.urljoin(url, loc)
            # B4：重定向只允许跳到门户域（*.ahpu.edu.cn）或网关 IP 字面量，
            # 其余目标（如恶意 Location 指向第三方主机）直接不跟随，
            # 避免把 CAS 的 SESSION cookie / ST 票据送到陌生主机。
            if _redirect_allowed(url, nxt):
                nxt_data = None if status in (301, 302, 303) else data
                return self.request(nxt, data=nxt_data, headers=headers, follow=True,
                                    _depth=_depth + 1)
            log("重定向目标不在允许域内，已拒绝跟随: %s" % nxt)
        return status, hdrs, body.decode("utf-8", "replace")

    def get(self, path, follow=True):
        return self.request(self.portal + path, follow=follow)

    def trace(self, url, max_hops=8):
        """逐跳追踪重定向，返回 [(url, status, location), ...]

        网关拦截普通 HTTP 请求时下发的跳转地址里带着 userip/usermac/nasip，
        浏览器就是顺着它走的；裸调 redirect.jsp 有时拿不到这些参数，
        所以这里把整条链原样走一遍并记录下来。
        """
        hops = []
        cur = url
        for _ in range(max_hops):
            st, hd, _ = self.request(cur, follow=False)
            loc = hd.get("Location") or hd.get("location") or ""
            hops.append((cur, st, loc))
            if st in (301, 302, 303, 307, 308) and loc:
                cur = urllib.parse.urljoin(cur, loc)
                continue
            break
        return hops

    def post_json(self, path, obj):
        body = json.dumps(obj, separators=(",", ":")).encode()
        return self.request(self.portal + path, data=body, method="POST", headers={
            "Content-Type": "application/json;charset=UTF-8",
            "Accept": "application/json, text/plain, */*",
            "Referer": self.portal + "/portal/portal-main",
            "isPortal": "true",
        })


def ensure_local_ip(portal=DEFAULT_PORTAL, verbose=True):
    """自动找出能从校园网取到 sessionId 的本机地址，缓存到 _PREFERRED_LOCAL_IP

    返回选中的 IP（None 表示系统默认路由可用）。
    探测一律用短超时：不在校园网时，不能把 20 秒的默认超时乘上网卡数量
    （实测两张网卡要 40 秒，而巡检间隔只有 60 秒）。全部失败后冷却一段时间，
    避免每次状态查询都重跑一遍。
    """
    global _PREFERRED_LOCAL_IP, _LOCAL_IP_NEXT_PROBE

    def ok(ip):
        s = PortalSession(portal, local_ip=ip, timeout=PROBE_TIMEOUT)
        st, hd, _ = s.request(s.portal + "/eportal/redirect.jsp", follow=False)
        loc = hd.get("Location") or ""
        return "sessionId=" in loc

    # 已经有结论且仍然可用 → 直接复用
    if _PREFERRED_LOCAL_IP:
        return _PREFERRED_LOCAL_IP
    if time.time() < _LOCAL_IP_NEXT_PROBE:
        return None

    if ok(None):          # 默认路由就行
        return None

    for ip in local_ipv4_list():
        if ok(ip):
            _PREFERRED_LOCAL_IP = ip
            _LOCAL_IP_NEXT_PROBE = 0.0
            if verbose:
                log("默认路由取不到会话，改走本机地址 %s" % ip)
            return ip
    _LOCAL_IP_NEXT_PROBE = time.time() + LOCAL_IP_COOLDOWN
    if verbose:
        log("所有网卡都取不到认证会话（可能不在校园网内）")
    return None


# ---------------------------------------------------------------- 认证协议

def get_redirect_info(sess, retries=2, interval=1.5, secondaries=False):
    """第 1 步：从 redirect.jsp 的 302 Location 取本次会话参数

    刚插网线/刚连 WiFi 时网关有初始化窗口，此时会返回 302 但不带 sessionId，
    所以这里按 retries × interval 轮询等待。secondaries=True 时同时尝试备用入口。
    """
    last = {}
    targets = [sess.portal + "/eportal/redirect.jsp"]
    if secondaries:
        targets += ["http://sam.ahpu.edu.cn/eportal/redirect.jsp",
                    "http://10.11.12.13/eportal/redirect.jsp"]
    for i in range(retries):
        for url in targets:
            st, hd, _ = sess.request(url, follow=False)
            loc = hd.get("Location") or hd.get("location") or ""
            if loc:
                qs = urllib.parse.parse_qs(urllib.parse.urlsplit(loc).query)
                info = {k: v[0] for k, v in qs.items()}
                if info.get("sessionId"):
                    return info
                last = info
                last["_debug"] = "HTTP %s → %s" % (st, loc[:100])
            else:
                last["_debug"] = "HTTP %s @ %s" % (st, url)
        if i < retries - 1:
            time.sleep(interval)
    return last


def get_session_id(sess, retries=2, secondaries=False):
    return get_redirect_info(sess, retries=retries,
                             secondaries=secondaries).get("sessionId")


def has_campus_ip():
    """本机是否已拿到校园网网段地址（10.x.x.x）"""
    return any(ip.startswith("10.") for ip in local_ipv4_list())


# 普通 HTTP 探测地址：网关靠拦截这类请求把"本机 + 原始访问网址"登记进会话表，
# 登记过之后 redirect.jsp 才能给出 userIp/userMac/nasIp 和 sessionId。
# 浏览器打开任意网站能弹出认证页，就是这条链路。
CAPTIVE_PROBES = [
    "http://www.msftconnecttest.com/connecttest.txt",
    "http://edge-http.microsoft.com/captiveportal/generate_204",
    "http://captive.apple.com/hotspot-detect.html",
    "http://1.1.1.1/",
]


def trigger_captive(sess):
    """主动发几个普通 HTTP 请求，让网关登记本机（等价于"浏览器打开任意网站"）

    返回被拦截时的跳转地址（可能为空串）。这个地址里通常带 userip/usermac/nasip，
    直接顺着它走就能拿到带 sessionId 的门户地址。
    """
    for url in CAPTIVE_PROBES:
        st, hd, body = sess.request(url, follow=False)
        loc = hd.get("Location") or hd.get("location") or ""
        if st in (301, 302, 303, 307, 308) and (
                "portal" in loc or "ahpu" in loc or "10.11.12.13" in loc):
            log("网关已拦截并跳转: %s → %s" % (url.split("/")[2], loc[:110]))
            return loc
        if st == 200 and ("portal" in body[:3000] or "认证" in body[:3000]):
            log("网关返回了认证页面（%s）" % url.split("/")[2])
            return loc or ""
    return ""


def _params_from_url(url):
    qs = urllib.parse.parse_qs(urllib.parse.urlsplit(url or "").query)
    return {k: v[0] for k, v in qs.items()}


# 网关拦截时返回的是这种小页面：
#   <script>top.self.location.href='https://sam.ahpu.edu.cn/eportal/index.jsp?
#       wlanuserip=…&wlanacname=…&ssid=&nasip=…&mac=…&t=wireless-v2&url=…&nasid=…'</script>
# 参数是 NAS 加密下发的，我们不用解密，原样转发即可。
_RE_JS_REDIRECT = re.compile(
    r"""(?:top\.self\.)?location(?:\.href)?\s*=\s*['"]([^'"]+)['"]""")


def portal_entry_from_gateway(sess, limit=None):
    """被网关拦截时，从响应里取出真正的门户入口地址（/eportal/index.jsp?...）

    limit: 只探测前几个地址（启动等待时用 1~2 个，避免单轮耗时过长）
    """
    for url in (CAPTIVE_PROBES[:limit] if limit else CAPTIVE_PROBES):
        st, hd, body = sess.request(url, follow=False)
        loc = hd.get("Location") or hd.get("location") or ""
        if loc and ("eportal" in loc or "/portal" in loc):
            return urllib.parse.urljoin(url, loc)
        m = _RE_JS_REDIRECT.search(body or "")
        if m:
            tgt = m.group(1).replace("&amp;", "&")
            if "eportal" in tgt or "/portal" in tgt:
                return tgt
        # 兜底：响应体里任何 eportal/index.jsp 地址
        m = re.search(r"""['"](https?://[^'"\s]*/eportal/index\.jsp[^'"\s]*)['"]""",
                      body or "")
        if m:
            return m.group(1).replace("&amp;", "&")
    return ""


def get_redirect_info_via_captive(sess):
    """走浏览器那条路：普通网页 → 网关拦截 → 门户入口 → 会话

    1. 请求普通网页，从网关返回的 JS 里取 `/eportal/index.jsp?...` 入口
    2. 原样访问它（参数是 NAS 加密的，不需要解密），记录整条跳转链
    3. 链上任意一跳带 sessionId 就直接用
    4. 走完入口后网关通常已登记本机，再取一次 redirect.jsp 兜底
    """
    entry = portal_entry_from_gateway(sess)
    if entry:
        log("网关下发的门户入口: %s" % entry[:140])
        hops = sess.trace(entry)
        for hop_url, _st, hop_loc in hops:
            for cand in (hop_url, hop_loc):
                info = _params_from_url(cand)
                if info.get("sessionId"):
                    log("从门户入口链取到会话: %s" % str(hop_loc)[:110])
                    return info
        info = get_redirect_info(sess, retries=3, interval=1)
        if info.get("sessionId"):
            return info
    return get_redirect_info(sess, retries=1)


def wait_campus_ip(timeout=READY_TIMEOUT):
    """等待网卡拿到校园网地址（开机自启时 DHCP 可能还没完成）

    300ms 轮询 + 墙钟截止时间（不是循环次数），拿不到就尽快返回。
    """
    deadline = time.time() + timeout
    n = 0
    while time.time() < deadline:
        if has_campus_ip():
            if n:
                log("网卡已获取校园网地址（等待 %.1f 秒）" % (time.time() - (deadline - timeout)))
            return True
        if n % 10 == 0:
            log("等待网卡获取校园网地址... 当前: %s"
                % (",".join(local_ipv4_list()) or "无"))
        n += 1
        time.sleep(PROBE_INTERVAL)
    return False


def describe_no_session(info):
    """把"取不到 sessionId"的原因翻译成人话"""
    dbg = info.get("_debug", "无响应")
    hints = []
    if "123.123.123.123" in dbg:
        hints.append("网关未识别本机（多半是网卡还没拿到校园网 IP）")
    ips = local_ipv4_list()
    if not any(ip.startswith("10.") for ip in ips):
        hints.append("当前本机地址 %s 不在校园网网段" % (",".join(ips) or "无"))
    tail = ("；" + "；".join(hints)) if hints else ""
    return "无法获取 sessionId（%s）%s" % (dbg, tail)


def new_session(portal=DEFAULT_PORTAL):
    """建会话前先确保选对了网卡"""
    ensure_local_ip(portal, verbose=False)
    return PortalSession(portal)


def status_session(portal=DEFAULT_PORTAL):
    """状态查询专用会话：短超时，离线时不会一次卡 20 秒"""
    sess = new_session(portal)
    sess.timeout = CHECK_TIMEOUT
    return sess


def check_online(sess=None):
    """在线状态查询，返回 (是否在线, 用户名)。

    online 三态：
      True  = 确认在线
      False = 确认离线（网关明确回"未上线"）
      None  = 无法确认（取不到会话 / 请求异常），此时不该被当作"离线"去触发重连
    """
    sess = sess or status_session()
    try:
        sid = get_session_id(sess)
        if not sid:
            return None, ""
        st, hd, text = sess.post_json("/eportal/network/userOnline", {"sessionId": sid})
        data = _loads(text).get("data") or {}
        if not data.get("online"):
            return False, ""
        # userOnline 不带用户名，再查一次在线详情（仅在已在线时）
        try:
            st, hd, text2 = sess.get(
                "/eportal/adaptor/getOnlineUserInfo?sessionId=" + sid)
            info = (_loads(text2).get("data") or {}).get("portalOnlineUserInfo") or {}
            return True, (info.get("userName") or info.get("userId") or "")
        except Exception:
            return True, ""
    except Exception as e:
        log("状态查询失败: %s" % e)
        return None, ""


def _loads(text):
    try:
        return json.loads(text or "{}")
    except Exception:
        return {}


def get_services(sess, sid):
    """第 5 步：可选运营商列表"""
    st, hd, text = sess.post_json("/eportal/network/serviceSelection", {"sessionId": sid})
    data = _loads(text).get("data")
    if isinstance(data, list):
        return [x.get("value") for x in data if isinstance(x, dict) and x.get("value")]
    return []


def merge_services(live, current=None):
    """界面下拉框用的出口列表：始终保留「校园网」和当前配置

    门户的 serviceSelection 只返回这个账号能切换的运营商，未办套餐时常用的
    「校园网」不一定在里面（实测只回中国电信/移动/联通），所以下拉框要把
    内置项并进去，不能直接用接口返回的列表覆盖。
    """
    out = []
    for name in [DEFAULT_SERVICE, current] + list(live or []):
        if name and name not in out:
            out.append(name)
    return out


def current_node(sess, sid):
    """查询会话当前的认证流程节点（诊断 / 确保流程就位）"""
    st, hd, text = sess.post_json("/eportal/workFlow/getCurrentNode", {"sessionId": sid})
    return _loads(text).get("data") or {}


def cas_login(sess, user, password, info=None):
    """第 2~4 步：统一身份认证，返回 (是否成功, ticket/错误信息, 失败类型)

    失败类型用来决定要不要自动重试：KIND_NETWORK 是网关/网络问题，退避后可以再试；
    KIND_MANUAL 是账号、密码或验证码问题，继续自动重试只会把 SSO 账号刷到锁定。
    """
    global _PREFERRED_LOCAL_IP, _LOCAL_IP_NEXT_PROBE

    if info is None:
        info = get_redirect_info(sess, retries=2, interval=1)
    if not info.get("sessionId"):
        # 可能是网卡选错了（网线/WiFi 切换、插拔），或需要走网关拦截入口
        _PREFERRED_LOCAL_IP = None
        _LOCAL_IP_NEXT_PROBE = 0.0        # 认证失败时强制重新选一次网卡
        ip = ensure_local_ip(sess.portal)
        if ip:
            sess.local_ip = ip
        info = get_redirect_info_via_captive(sess) or info
    if not info.get("sessionId"):
        return False, describe_no_session(info), KIND_NETWORK

    url = (sess.portal + "/cas-sso/login?flowSessionId=%s&customPageId=%s"
           "&preview=false&appType=normal&language=zh-CN&timer=%d"
           "&nasIp=%s&userIp=%s&userMac=%s&accept-language=zh-CN") % (
        info.get("sessionId", ""), info.get("customPageId", ""),
        int(time.time() * 1000), info.get("nasIp", ""),
        info.get("userIp", ""), info.get("userMac", ""))

    st, hd, html = sess.request(url)
    if st != 200:
        return False, "登录页打开失败 HTTP %s" % st, KIND_NETWORK

    m_key = re.search(r'<p id="login-croypto">([^<]*)</p>', html)
    m_flow = re.search(r'<p id="login-page-flowkey">([^<]*)</p>', html)
    if not m_key or not m_flow:
        return False, "登录页结构变化：未找到 croypto / flowkey", KIND_OTHER
    croypto, flowkey = m_key.group(1), m_flow.group(1)

    form = {
        "username": user,
        "type": "UsernamePassword",
        "_eventId": "submit",
        "geolocation": "",
        "execution": flowkey,
        "captcha_code": "",
        "croypto": croypto,
        "password": _aes_ecb_b64(croypto, password),
        "captcha_payload": _aes_ecb_b64(croypto, "{}"),
    }
    st, hd, resp = sess.request(
        url, data=urllib.parse.urlencode(form).encode(), method="POST", follow=False,
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "Origin": sess.portal, "Referer": url})

    loc = hd.get("Location") or hd.get("location") or ""
    if st in (301, 302, 303) and loc:
        qs = urllib.parse.parse_qs(urllib.parse.urlsplit(loc).query)
        ticket = (qs.get("ticket") or [""])[0]
        sess.last_cas_location = loc
        sess.request(loc if loc.startswith("http") else sess.portal + loc)
        return True, ticket or loc, KIND_OK

    # 网络层失败（超时/断线返回 st==0，5xx 服务端故障，302 却无 Location）：
    # 都是"暂时连不上"，应按网络错误退避重试，绝不能判成账号问题而永久熔断。
    if st == 0 or st >= 500 or (st in (301, 302, 303) and not loc):
        return False, "HTTP %s" % st, KIND_NETWORK

    m = re.search(r'id="login-error-msg"[^>]*>\s*<span>([^<]*)</span>', resp)
    err = m.group(1).strip() if m else "HTTP %s" % st
    if "验证码" in err:
        # 要验证码时程序无能为力，验证码只在浏览器那个页面里有效
        err += "（门户要求验证码，请先用浏览器登录一次）"
    return False, err, KIND_MANUAL


def service_login(sess, sid, service):
    """第 6 步：把本机授权到指定出口"""
    st, hd, text = sess.post_json("/eportal/network/serviceLogin",
                                 {"sessionId": sid, "service": service})
    return _loads(text)


def do_login(cfg, progress=None, notify=None, force=False):
    """执行登录流程。progress(msg) 输出进度；notify(title, text) 结果通知。
    force=True 时即使已在线也走完整认证（调试用）。
    返回 (是否成功, 说明文字, 失败类型)。失败类型见模块顶部的 KIND_*：
    KIND_MANUAL 表示账号/配置问题，调用方应暂停自动重连。"""
    if progress is None:
        progress = lambda msg: None
    portal = normalize_portal(cfg.get("portalHost")) or DEFAULT_PORTAL
    user = (cfg.get("userId") or "").strip()
    password = cfg.get("password") or ""
    service = cfg.get("service") or ""

    try:
        sess = new_session(portal)
        sess.timeout = 8          # 正常请求实测都在 0.2 秒内，8 秒足够，同时兜住异常卡顿
        if sess.local_ip:
            progress("网络出口网卡: %s" % sess.local_ip)

        online, uid = check_online(sess)
        if online and not force:
            msg = "已在线" + ("（%s）" % uid if uid else "") + "，无需登录"
            progress(msg)
            if notify:
                notify("校园网", "已在线，无需登录")
            return True, msg, KIND_OK

        if not has_campus_ip():
            progress("等待网卡获取校园网地址...")
            wait_campus_ip(READY_TIMEOUT)
        progress("等待认证网关就绪...")
        wait_network(portal, READY_TIMEOUT)
        # 网关就绪后再取会话；未认证时 redirect.jsp 只会回 123.123.123.123，
        # 那就走浏览器那条路：普通网页 → 网关 JS 跳转 → /eportal/index.jsp
        info = get_redirect_info(sess, retries=1, interval=0)
        for attempt in range(3):
            if info.get("sessionId"):
                break
            progress("网关未识别本机，改走浏览器路径（普通网页 → 网关跳转）...")
            via = get_redirect_info_via_captive(sess)
            if via.get("sessionId"):
                info = via
                break
            trigger_captive(sess)
            info = get_redirect_info(sess, retries=1, interval=PROBE_INTERVAL,
                                    secondaries=True)
            if not info.get("sessionId"):
                time.sleep(PROBE_INTERVAL)
        sid_before = info.get("sessionId") or ""
        progress("开始统一身份认证，学号: %s（sessionId=%s）" % (user, sid_before or "-"))
        ok, detail, reason = cas_login(sess, user, password, info=info)
        if not ok:
            progress("认证失败: %s" % detail)
            if notify:
                notify("登录失败", str(detail))
            return False, "统一身份认证失败：%s" % detail, reason
        progress("统一身份认证通过，ticket=%s" % (detail or "-"))
        progress("CAS 跳转目标: %s" % (getattr(sess, "last_cas_location", "") or "-"))

        # 校园网存在无感知认证：CAS 通过后可能已直接上线，先确认一次
        online, uid = check_online(sess)
        if online:
            msg = "登录成功（统一身份认证已生效%s）" % (
                "，账号 %s" % uid if uid else "")
            progress(msg)
            if notify:
                notify("登录成功", "学号: %s" % (uid or user))
            return True, msg, KIND_OK

        # 注意：redirect.jsp 每访问一次可能新建一个流程会话，
        # 所以认证后**先**用认证前的 sessionId（流程就是它建的），失败再回退重取。
        services = get_services(sess, sid_before) if sid_before else []
        if services:
            progress("可选出口: %s" % "、".join(services))
        if not service:
            # 老配置可能没有 service 字段：优先校园网，避免默认落到计费出口
            service = (DEFAULT_SERVICE if DEFAULT_SERVICE in services
                       else (services[0] if services else DEFAULT_SERVICE))
            progress("配置里没有出口，自动使用: %s" % service)
        # 配置的出口不在列表里也照样试（服务端有可能仍然接受），但绝不静默换成
        # 列表里的第一家运营商：选错出口可能产生额外计费。
        service_mismatch = ""
        if services and service not in services:
            service_mismatch = ("\n配置的出口「%s」不在可选列表（%s）中，请在配置窗口重新选择"
                                % (service, "、".join(services)))
            progress(service_mismatch.strip())

        last_err = ""
        tried = []

        def attempt(sid):
            """用给定会话尝试授权上线，返回 (成功?, 错误信息)"""
            node = current_node(sess, sid)
            path = node.get("currentNodePath") or ""
            progress("sessionId=%s 流程节点: %s" % (
                sid, (node.get("currentNodeId") or "空") + ("/" + path if path else "")))
            res = service_login(sess, sid, service)
            data = res.get("data") or {}
            if res.get("code") == 200 and data.get("authResult") == "success":
                online, _uid = check_online(sess)
                if online:
                    return True, ""
                return False, "授权成功但状态校验仍离线"
            err = (res.get("message") or data.get("authMessage")
                   or json.dumps(res, ensure_ascii=False)[:200])
            if "workFlowNode" in err and path == "finish":
                err += "（设备已在线，认证流程已到 finish 终点；请先下线再登录）"
            return False, err

        ok2, err = attempt(sid_before) if sid_before else (False, "无 sessionId")
        if ok2:
            msg = "登录成功（出口: %s）" % service
            progress(msg)
            if notify:
                notify("登录成功", "学号: %s  出口: %s" % (user, service))
            return True, msg, KIND_OK
        last_err = err
        tried.append(sid_before)
        progress("认证前会话授权失败: %s" % err)

        # 回退：CAS 后会话可能刷新，重新取一次再试
        sid_after = get_session_id(sess)
        info2 = get_redirect_info(sess)
        progress("CAS 后客户端状态: userOnline=%s sessionId=%s cookie=%s" % (
            info2.get("userOnline") or "?", info2.get("sessionId") or "-",
            ",".join(sorted(n for b in sess.cookies.values() for n in b))
            or "无"))
        if sid_after and sid_after not in tried:
            ok3, err3 = attempt(sid_after)
            if ok3:
                msg = "登录成功（出口: %s）" % service
                progress(msg)
                if notify:
                    notify("登录成功", "学号: %s  出口: %s" % (user, service))
                return True, msg, KIND_OK
            last_err = err3
            progress("新会话授权失败: %s" % err3)

        msg = "授权上线失败：%s" % (last_err or "未知错误")
        if service_mismatch:
            msg += service_mismatch
        if notify:
            notify("登录失败", str(last_err or "未知错误"))
        return False, msg, (KIND_MANUAL if service_mismatch else KIND_OTHER)

    except ImportError:
        msg = "缺少依赖 cryptography，请执行 pip install cryptography"
        progress(msg)
        if notify:
            notify("登录失败", msg)
        return False, msg, KIND_MANUAL
    except Exception as e:
        progress("登录异常: %s" % e)
        if notify:
            notify("登录失败", str(e))
        return False, "登录异常：%s" % e, KIND_OTHER


def wait_network(portal, timeout=READY_TIMEOUT):
    """等待认证网关就绪，返回是否成功

    多次短超时探测：每轮最多 2 秒，轮间隔 300ms，总时长不超过 timeout 秒。
    这样开机时网关哪怕要十几秒才起来，也不会被一次 20 秒的超时拖住。
    """
    wait_campus_ip(timeout)
    sess = PortalSession(portal, timeout=PROBE_TIMEOUT)
    start = time.time()
    n = 0
    while time.time() - start < timeout:
        n += 1
        if get_redirect_info(sess, retries=1).get("sessionId"):
            log("认证网关已就绪（第 %d 次探测，用时 %.1f 秒）"
                % (n, time.time() - start))
            return True
        if portal_entry_from_gateway(sess, limit=1):
            log("认证网关已就绪（拦截入口可用，第 %d 次探测，用时 %.1f 秒）"
                % (n, time.time() - start))
            return True
        if n % 10 == 0:
            log("等待认证网关... 已探测 %d 次（%.1f 秒）" % (n, time.time() - start))
        time.sleep(PROBE_INTERVAL)
    log("等待认证网关超时（%.1f 秒）" % (time.time() - start))
    return False


# ---------------------------------------------------------------- 配置读写

# 密码用 Windows DPAPI 加密存储：密文只能被"当前用户 + 当前机器"解开。
# 这样把程序目录发给别人时，config.json 里的密码对他是无效的，必须自己填。

class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", ctypes.wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi(data, protect):
    """调用 CryptProtectData / CryptUnprotectData"""
    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = _DATA_BLOB()
    fn = ctypes.windll.crypt32.CryptProtectData if protect else \
        ctypes.windll.crypt32.CryptUnprotectData
    ok = fn(ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out))
    if not ok:
        raise OSError("DPAPI 调用失败")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


_DECRYPT_WARNED = set()      # 同一字段的"解不开"只提示一次，避免巡检时刷屏


def _encrypt_secret(plain):
    """明文 → base64(DPAPI 密文)；失败返回 None（调用方回退明文并记日志）"""
    try:
        return base64.b64encode(_dpapi(plain.encode("utf-8"), True)).decode()
    except Exception as e:
        log("敏感字段加密失败（将回退明文存储）: %s" % e)
        return None


def _decrypt_secret(token, label):
    """base64(DPAPI 密文) → 明文；解不开（换了电脑/用户）返回空串"""
    try:
        return _dpapi(base64.b64decode(token), False).decode("utf-8")
    except Exception:
        if label not in _DECRYPT_WARNED:
            _DECRYPT_WARNED.add(label)
            log("%s无法解密（配置可能来自其他电脑/用户），请重新输入" % label)
        return ""


def load_config():
    try:
        with open(CONFIG_FILE, encoding="utf-8-sig") as f:
            cfg = json.load(f)
    except Exception:
        return {}
    if not isinstance(cfg, dict):
        return {}
    # 兼容两种情况：DPAPI 密文 / 历史明文（下次保存时自动迁移成密文）
    if cfg.get("passwordEnc"):
        cfg["password"] = _decrypt_secret(cfg.pop("passwordEnc"), "密码")
    if cfg.get("userIdEnc"):
        cfg["userId"] = _decrypt_secret(cfg.pop("userIdEnc"), "学号")
    return cfg


def save_config(cfg):
    """保存配置：临时文件 + os.replace 原子写；学号/密码用 DPAPI 密文

    直接覆盖写，中途断电/被杀就留下半截 JSON，下次启动等于没配置。
    新值为空时（DPAPI 解不开、换过电脑）必须保留旧密文，
    否则只是切一下"断线自动重连"就会把账号/密码永久删掉。
    """
    data = dict(cfg)
    pwd = data.pop("password", "")
    uid = (data.pop("userId", "") or "").strip()
    old = {}
    try:
        with open(CONFIG_FILE, encoding="utf-8-sig") as f:
            old = json.load(f) or {}
    except Exception:
        old = {}
    if not isinstance(old, dict):
        old = {}

    if pwd:
        enc = _encrypt_secret(pwd)
        if enc:
            data["passwordEnc"] = enc
        else:
            data["password"] = pwd      # DPAPI 不可用时的兜底
    elif old.get("passwordEnc"):
        data["passwordEnc"] = old["passwordEnc"]

    if uid:
        enc = _encrypt_secret(uid)
        if enc:
            data["userIdEnc"] = enc
        else:
            data["userId"] = uid
    elif old.get("userIdEnc"):
        data["userIdEnc"] = old["userIdEnc"]
    elif old.get("userId"):
        data["userId"] = old["userId"]   # 老配置还没有密文，先沿用它

    tmp = CONFIG_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass
    os.replace(tmp, CONFIG_FILE)
    try:                                # 仅当前用户可读（Windows 上 chmod 作用有限）
        os.chmod(CONFIG_FILE, 0o600)
    except Exception:
        pass


def migrate_config():
    """把旧配置里的明文学号/密码就地加密（启动时跑一次）"""
    try:
        with open(CONFIG_FILE, encoding="utf-8-sig") as f:
            raw = json.load(f)
    except Exception:
        return False
    if not isinstance(raw, dict):
        return False
    if not (raw.get("userId") or raw.get("password")):
        return False                    # 已经是密文（或没配置）
    cfg = load_config()
    if not (cfg.get("userId") and cfg.get("password")):
        return False                    # 解不开就原样保留，别动用户的配置
    try:
        save_config(cfg)
        log("配置已升级：学号与密码改为 DPAPI 密文存储")
        return True
    except Exception as e:
        log("配置迁移失败: %s" % e)
        return False


# ---------------------------------------------------------------- 开机自启

def pythonw_path():
    """优先使用 pythonw.exe（无控制台窗口）"""
    exe = os.path.abspath(sys.executable)
    cand = os.path.join(os.path.dirname(exe), "pythonw.exe")
    return cand if os.path.exists(cand) else exe


def autostart_cmd():
    """自启命令行：始终指向"程序自己现在所在的位置"

    - 打包成 exe 后 → exe 所在路径（移动到别的目录也以当前路径为准）
    - 源码运行     → pythonw.exe + 当前 .py 路径
    """
    if IS_FROZEN:
        return '"%s" --login' % APP_EXE
    return '"%s" "%s" --login' % (pythonw_path(), os.path.abspath(__file__))


def autostart_registered_value():
    """读注册表里已写入的命令，没有则返回 None"""
    if not winreg:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY) as k:
            return winreg.QueryValueEx(k, AUTOSTART_NAME)[0]
    except OSError:
        return None


def autostart_enabled():
    return autostart_registered_value() is not None


def autostart_targets(value):
    """从自启命令里取出"必须存在"的文件路径

    带引号的按引号切；没引号的按空格切，并丢掉 --login 这类参数。
    """
    if not value:
        return []
    parts = re.findall(r'"([^"]+)"', value)
    if not parts:
        parts = value.split()
    return [p for p in parts if not p.startswith("-")]


def autostart_policy_disabled():
    """Windows「启动应用」里是否被禁用（StartupApproved 首字节为奇数）

    任务管理器 / 电脑管家把启动项关掉时，Run 键仍然存在，只在这里打标记，
    所以"注册表有值"不等于"开机真的会跑"。
    """
    if not winreg:
        return False
    key = (r"Software\Microsoft\Windows\CurrentVersion\Explorer"
           r"\StartupApproved\Run")
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            data, _ = winreg.QueryValueEx(k, AUTOSTART_NAME)
    except OSError:
        return False
    return bool(data) and data[0] % 2 == 1


def clear_autostart_policy_disabled():
    """把「启动应用」里的禁用标记清回启用（02 00 ...）

    只把 Run 键写对还不够：任务管理器/电脑管家那边仍标着"已禁用"的话，
    开机照样不会跑。写回 02 是 Windows 自己的"启用"编码。
    """
    if not winreg:
        return False
    key = (r"Software\Microsoft\Windows\CurrentVersion\Explorer"
           r"\StartupApproved\Run")
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0,
                            winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, AUTOSTART_NAME, 0, winreg.REG_BINARY,
                              b"\x02" + b"\x00" * 11)
        return True
    except OSError:
        return False


def autostart_problems(value=None):
    """自启项体检：返回问题列表，空列表表示正常"""
    value = autostart_registered_value() if value is None else value
    if value is None:
        return ["注册表里没有自启项"]
    problems = []
    if value != autostart_cmd():
        problems.append("命令指向别处: %s" % value)
    missing = [p for p in autostart_targets(value) if not os.path.exists(p)]
    if missing:
        problems.append("目标文件不存在: %s" % "、".join(missing))
    if autostart_policy_disabled():
        problems.append("被 Windows「启动应用」禁用")
    return problems


def verify_autostart_delayed(delay=3.0):
    """写入几秒后复查一次：部分安全软件会延迟把自启项改回去"""
    expected = autostart_cmd()

    def run():
        time.sleep(delay)
        try:
            current = autostart_registered_value()
        except Exception:
            return
        if current != expected:
            log("开机自启项几秒后被改回（%s），可能被电脑管家/安全软件拦截"
                % (current or "空"))

    try:
        threading.Thread(target=run, daemon=True).start()
    except Exception:
        pass


def refresh_autostart_if_enabled(cfg=None):
    """启动时自愈：配置里开了自启，就把注册表项按当前路径重写一遍

    解决"程序目录被移动/重命名后自启失效"的问题。
    """
    cfg = cfg if cfg is not None else load_config()
    if not cfg.get("autoStart"):
        return False
    problems = autostart_problems()
    if not problems:
        return False
    if set_autostart(True):
        clear_autostart_policy_disabled()
        log("已按当前程序位置更新开机自启项")
        return True
    log("开机自启自愈失败：%s" % "；".join(problems))
    return False


def set_autostart(enable):
    """写入/删除注册表 HKCU Run 启动项，返回是否成功

    写完立刻回读：电脑管家类软件会"保护"启动项，把刚写入的值静默改回去，
    所以 SetValueEx 不报错并不代表真的生效；成功后再挂一个延迟复查。
    """
    if not winreg:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY,
                            0, winreg.KEY_SET_VALUE) as k:
            if enable:
                winreg.SetValueEx(k, AUTOSTART_NAME, 0, winreg.REG_SZ, autostart_cmd())
            else:
                try:
                    winreg.DeleteValue(k, AUTOSTART_NAME)
                except OSError:
                    pass
        current = autostart_registered_value()
        if enable and current != autostart_cmd():
            log("开机自启写入后被改回（%s），可能被电脑管家/安全软件拦截"
                % (current or "空"))
            return False
        if not enable and current is not None:
            log("开机自启项删除后被还原（%s），可能被电脑管家/安全软件拦截"
                % current)
            return False
        if enable:
            verify_autostart_delayed()
        return True
    except OSError as e:
        log("设置开机自启失败: %s" % e)
        return False


# ---------------------------------------------------------------- 单实例

def acquire_single_instance():
    """命名互斥量：同一 Windows 登录会话内只允许一个实例

    返回 (是否首个实例, 句柄)。句柄保存到全局，进程活着就一直持有。
    非 Windows 或调用异常时直接放行——不能因为加锁把程序锁死。
    """
    try:
        k32 = ctypes.windll.kernel32
        k32.CreateMutexW.restype = ctypes.c_void_p
        k32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
        k32.CloseHandle.argtypes = [ctypes.c_void_p]
        k32.CloseHandle.restype = ctypes.c_int
        handle = k32.CreateMutexW(None, 0, MUTEX_NAME)
        if not handle:
            return True, None
        if k32.GetLastError() == 183:        # ERROR_ALREADY_EXISTS
            k32.CloseHandle(handle)
            return False, None
        return True, handle
    except Exception:
        return True, None


# ---------------------------------------------------------------- 托盘图标

def _make_icon_image(rgb):
    """绘制圆形 WiFi 图标（64x64）；Pillow 在第一次调用时才导入"""
    tray_available()
    Image, ImageDraw = _TRAY_MODS["Image"], _TRAY_MODS["ImageDraw"]
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([2, 2, 62, 62], fill=rgb + (255,))
    white = (255, 255, 255, 255)
    d.arc([14, 16, 50, 52], 205, 335, fill=white, width=6)
    d.arc([22, 24, 42, 44], 205, 335, fill=white, width=6)
    d.ellipse([29, 31, 35, 37], fill=white)
    return img


_ICONS = {}


def icons():
    """三色托盘图标（延迟生成：不进托盘就不画）"""
    if not _ICONS:
        _ICONS["green"] = _make_icon_image((46, 204, 113))    # 在线
        _ICONS["grey"] = _make_icon_image((149, 165, 166))    # 离线/未知
        _ICONS["red"] = _make_icon_image((231, 76, 60))       # 登录失败
    return _ICONS



# ---------------------------------------------------------------- 图形界面（含托盘）

class CampusApp(tk.Tk):

    def __init__(self, headless=False):
        super().__init__()
        self.headless = headless
        self.title("校园网自动登录 v%s（安徽工程大学）" % APP_VERSION)
        self.resizable(False, True)
        self.queue = queue.Queue()
        self.worker = None
        self.icon = None
        self.last_status = None  # (online, uid)
        self.fail_streak = 0       # 连续失败次数，用于指数退避
        self.next_retry_at = 0.0   # 下次允许自动重连的时间点（墙钟）
        self.need_manual = False   # 认证类失败：暂停自动重连，等用户手动处理
        self.monitor_busy = False  # 巡检线程是否在跑，慢网络下避免线程叠加
        self._cas_attempt_date = ""   # 频率闸门：当前累计 CAS 提交次数的日期
        self._cas_attempts = 0        # 频率闸门：当天已提交 CAS 密码的次数

        cfg = load_config()
        self.auto_relogin = bool(cfg.get("autoRelogin", True))

        self._build()
        self._load_settings()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.withdraw()  # 先隐藏，再按需显示，避免闪烁

        # 托盘初始化推迟到这里（Pillow/pystray 此时才导入）
        tray_ok = tray_available()
        if tray_ok:
            self._start_tray()
        else:
            log("未安装 pystray，系统托盘不可用（pip install pystray pillow）")

        has_cfg = bool(cfg.get("userId") and cfg.get("password"))
        if not has_cfg:
            # 首次运行：显示配置窗口
            self._show_window()
            self._append("首次使用：请填写学号与统一身份认证密码后点击「保存并登录」")
        else:
            if not tray_ok and not self.headless:
                self._show_window()
            self._initial_login()

        self.after(150, self._poll)
        self.after(MONITOR_INTERVAL, self._monitor_tick)
        self.after(800, self._refresh_services_async)

    # ---------- 界面构建 ----------

    def _build(self):
        outer = ttk.Frame(self, padding=16)
        outer.pack(fill="both", expand=True)

        ttk.Label(outer, text="校园网自动登录", font=TITLE_FONT).pack()
        ttk.Label(outer, text="安徽工程大学 · 统一身份认证 + ePortal",
                  foreground="#777", font=LABEL_FONT).pack(pady=(2, 12))

        form = ttk.Frame(outer)
        form.pack(fill="x")
        form.columnconfigure(1, weight=1)

        self.portal_var = tk.StringVar()
        self.user_var = tk.StringVar()
        self.pass_var = tk.StringVar()
        self.service_var = tk.StringVar()

        self.service_box = ttk.Combobox(form, textvariable=self.service_var,
                                        values=DEFAULT_SERVICES,
                                        state="readonly", width=30)
        # 认证地址行：输入框 + 「解析链接」按钮放在同一个 grid 单元格里。
        # 用户常常把浏览器地址栏里那一整串（带 wlanuserip/nasip/mac/sessionId 等
        # 一次性参数）直接粘进来，按一下按钮就能截成 https://sam.ahpu.edu.cn。
        # 输入框宽度 32 → 24：让出按钮的位置后整行宽度与下面几行大致齐平。
        portal_row = ttk.Frame(form)
        self.portal_entry = ttk.Entry(portal_row, textvariable=self.portal_var, width=24)
        self.portal_entry.pack(side="left", fill="x", expand=True)
        self.portal_entry.bind("<<Paste>>", self._on_portal_paste)
        self.parse_btn = ttk.Button(portal_row, text="解析链接", width=8,
                                    command=self.on_parse_portal)
        self.parse_btn.pack(side="right", padx=(6, 0))

        rows = [
            ("认证地址：", portal_row),
            ("学　号：", ttk.Entry(form, textvariable=self.user_var, width=32)),
            ("密　码：", ttk.Entry(form, textvariable=self.pass_var, width=32, show="*")),
            ("运营商：", self.service_box),
        ]
        for r, (label, widget) in enumerate(rows):
            ttk.Label(form, text=label, font=LABEL_FONT).grid(
                row=r, column=0, sticky="e", padx=(0, 8), pady=4)
            widget.grid(row=r, column=1, sticky="we", pady=4)

        tip = ("账号为学号，无需运营商后缀；未办运营商套餐请选「校园网」。"
               "密码与微校园/今日校园一致。")
        ttk.Label(outer, text=tip, foreground="#888", font=("Microsoft YaHei", 8),
                  wraplength=330, justify="left").pack(anchor="w", pady=(6, 0))

        opts = ttk.Frame(outer)
        opts.pack(fill="x", pady=(8, 2))
        self.autostart_var = tk.BooleanVar(value=autostart_enabled())
        self.notify_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opts, text="开机自动登录", variable=self.autostart_var).pack(side="left")
        ttk.Checkbutton(opts, text="显示登录通知", variable=self.notify_var).pack(side="left", padx=15)

        btns = ttk.Frame(outer)
        btns.pack(fill="x", pady=10)
        self.login_btn = ttk.Button(btns, text="保存并登录", command=self.on_save_login)
        self.login_btn.pack(side="left", expand=True, fill="x", padx=(0, 4))
        ttk.Button(btns, text="仅保存", command=self.on_save).pack(
            side="left", expand=True, fill="x", padx=2)
        self.check_btn = ttk.Button(btns, text="检查状态", command=self.on_check)
        self.check_btn.pack(side="left", expand=True, fill="x", padx=(4, 0))

        ttk.Label(outer, text="运行日志", font=LABEL_FONT).pack(anchor="w")
        self.log_text = scrolledtext.ScrolledText(
            outer, height=10, width=54, state="disabled", font=LOG_FONT)
        self.log_text.pack(fill="both", expand=True)

    # ---------- 托盘 ----------

    def _start_tray(self):
        pystray = _TRAY_MODS["pystray"]
        menu = pystray.Menu(
            pystray.MenuItem("校园网自动登录", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("配置窗口", self._tray_action("show"), default=True),
            pystray.MenuItem("立即登录", self._tray_action("login")),
            pystray.MenuItem("检查状态", self._tray_action("check")),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("开机自动登录", self._tray_action("toggle_autostart"),
                             checked=lambda item: autostart_enabled()),
            pystray.MenuItem("断线自动重连", self._tray_action("toggle_autorelogin"),
                             checked=lambda item: self.auto_relogin),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("打开日志文件", self._tray_action("open_log")),
            pystray.MenuItem("清空日志", self._tray_action("clear_log")),
            pystray.MenuItem("退出", self._tray_action("quit")),
        )
        self.icon = pystray.Icon("CampusNetLogin", icons()["grey"],
                                 "校园网自动登录 - 初始化", menu)
        threading.Thread(target=self.icon.run, daemon=True).start()

    def _tray_action(self, name):
        def handler(icon, item):
            self.queue.put(("action", name))
        return handler

    def _refresh_tray_menu(self):
        try:
            if self.icon:
                self.icon.update_menu()
        except Exception:
            pass

    def _set_tray_status(self, online, uid="", fail=False):
        self.last_status = (online, uid)
        if not self.icon:
            return
        try:
            ic = icons()
            if fail or (self.need_manual and online is False):
                self.icon.icon = ic["red"]
                self.icon.title = ("校园网自动登录 - 登录失败" +
                                   ("，等待手动处理" if self.need_manual else ""))
            elif online is True:
                self.icon.icon = ic["green"]
                self.icon.title = _redact("校园网自动登录 - 已在线" +
                                          (" (%s)" % uid if uid else ""))
            elif online is None:
                self.icon.icon = ic["grey"]
                self.icon.title = "校园网自动登录 - 状态未知（可能不在校园网）"
            else:
                self.icon.icon = ic["grey"]
                self.icon.title = "校园网自动登录 - 离线"
        except Exception:
            pass

    def _handle_tray_action(self, name):
        if name == "show":
            self._show_window()
        elif name == "login":
            self._start_login(from_tray=True)
        elif name == "check":
            self.on_check()
        elif name == "toggle_autostart":
            enable = not autostart_enabled()
            ok = set_autostart(enable)
            if enable and ok:
                clear_autostart_policy_disabled()
            elif enable:
                self._notify("开机自启未生效",
                             "自启项写入后被改回，可能被电脑管家类软件拦截，"
                             "请在「启动项管理」里放行后重试")
            self.autostart_var.set(enable)
            cfg = load_config()
            cfg["autoStart"] = enable
            try:
                save_config(cfg)
            except Exception as e:
                log("保存配置失败: %s" % e)
                messagebox.showerror("保存失败", "无法写入配置：\n%s" % e, parent=self)
            log("开机自启: %s" % ("开启" if enable else "关闭"))
            self._refresh_tray_menu()
        elif name == "toggle_autorelogin":
            self.auto_relogin = not self.auto_relogin
            cfg = load_config()
            cfg["autoRelogin"] = self.auto_relogin
            try:
                save_config(cfg)
            except Exception as e:
                log("保存配置失败: %s" % e)
                messagebox.showerror("保存失败", "无法写入配置：\n%s" % e, parent=self)
            if self.auto_relogin:      # 重新开启时清掉退避/熔断，立刻给一次机会
                self.fail_streak = 0
                self.next_retry_at = 0.0
                self.need_manual = False
            log("断线自动重连: %s" % ("开启" if self.auto_relogin else "关闭"))
            self._refresh_tray_menu()
        elif name == "open_log":
            try:
                os.startfile(LOG_FILE)
            except Exception as e:
                log("打开日志失败: %s" % e)
        elif name == "clear_log":
            if messagebox.askyesno("清空日志", "删除 login.log 及历史日志？",
                                   parent=self):
                clear_logs()
                log("日志已清空")
                self._append("日志已清空")
        elif name == "quit":
            self.quit_app()

    def _show_window(self):
        self.deiconify()
        self.update_idletasks()
        w, h = self.winfo_width(), self.winfo_height()
        self.geometry("+%d+%d" % ((self.winfo_screenwidth() - w) // 2,
                                  (self.winfo_screenheight() - h) // 3))
        self.lift()

    def on_close(self):
        """关闭窗口：有托盘则最小化到托盘，否则退出"""
        if self.icon:
            self.withdraw()
        else:
            self.quit_app()

    def quit_app(self):
        try:
            if self.icon:
                self.icon.stop()
        except Exception:
            pass
        self.destroy()

    # ---------- 配置 ----------

    def _load_settings(self):
        cfg = load_config()
        self.portal_var.set(normalize_portal(cfg.get("portalHost")) or DEFAULT_PORTAL)
        self.user_var.set(cfg.get("userId", ""))
        self.pass_var.set(cfg.get("password", ""))
        service = cfg.get("service", "")
        if service:
            self.service_var.set(service)
        else:
            self.service_var.set(DEFAULT_SERVICE)
        self.notify_var.set(bool(cfg.get("showNotify", True)))
        self._append("提示：填写学号与密码后点击「保存并登录」；开机自启状态：%s" % (
            "已开启" if autostart_enabled() else "未开启"))

    # ---------- 认证地址解析 ----------

    def _clipboard_text(self):
        """读剪贴板文本；没有剪贴板/被别的程序占住时返回空串，绝不抛异常"""
        try:
            value = self.clipboard_get()
        except Exception:
            return ""
        return str(value or "").strip()

    def on_parse_portal(self):
        """「解析链接」按钮：把输入框里的完整认证链接截成认证地址"""
        self._parse_portal(manual=True)

    def _parse_portal(self, manual=True):
        """解析认证地址：优先输入框，其次剪贴板（用户常常只是复制了链接没粘进来）

        manual=True  —— 点按钮触发：什么都没有时明确提示，失败时弹警告框；
        manual=False —— 粘贴后自动触发：失败时静默（保留原文，用户可点按钮看原因）。

        隐私：日志只写解析出的 origin 与"丢弃了几个参数"，绝不回吐链接原文
        （原文里带 sessionId / mac / wlanuserip）；全程不发网络请求。
        """
        text = (self.portal_var.get() or "").strip()
        if not text:
            text = self._clipboard_text()
        if not text:
            if manual:
                messagebox.showwarning(
                    "无法解析链接",
                    "请先粘贴完整的网络认证链接，再点击「解析链接」。", parent=self)
            return None

        result = parse_portal_link(text)
        if not result.portal:
            if manual:
                messagebox.showwarning("无法解析链接", result.error, parent=self)
            return None

        current = (self.portal_var.get() or "").strip()
        if result.portal != current:
            self.portal_var.set(result.portal)
            try:                                   # 光标与视图都挪到末尾
                self.portal_entry.icursor("end")
                self.portal_entry.xview("end")
            except Exception:
                pass
            self._append("认证地址已解析为 %s（%s）" % (result.portal, result.detail))
        else:
            self._append("认证地址已是纯认证地址：%s" % result.portal)
        if result.warning:
            self._append("提示：%s" % result.warning)
        return result.portal

    def _on_portal_paste(self, event=None):
        """认证地址框的 <<Paste>> 处理：粘进来的是"完整链接"就自动解析一次

        返回 None 让默认粘贴照常执行；真正的内容检查推迟到 after_idle，
        那时输入框里已经是粘贴后的文本。
        """
        self.after_idle(self._auto_parse_portal_after_paste)
        return None

    def _auto_parse_portal_after_paste(self):
        """粘贴后自动解析（静默失败）：出现 ? 或 /eportal 等路径才动手，
        避免用户只是想改个端口/主机时被无谓地改写。

        标记匹配前先过 _clean_link_text（NFKC 归一半角）：从 PDF / Word / 微信复制
        出来的是 `ＨＴＴＰＳ：／／…／ｅｐｏｒｔａｌ／ｘ？…`，全角标记直接匹配是匹配不到的。
        """
        text = _clean_link_text(self.portal_var.get() or "").lower()
        if not any(mark in text for mark in ("?", "/eportal", "/cas-sso", "/portal/")):
            return
        self._parse_portal(manual=False)

    def _collect_config(self):
        raw_portal = (self.portal_var.get() or "").strip()
        # 明显非法（写了 :// 却解析不出 origin：http:// 、ftp:// 、主机/端口非法）
        # 就在保存前拦住，让用户看到具体原因——否则 "http://" 会被下面的宽松兜底
        # 变成 "https://http:" 写进 config.json，下次启动只会报一个莫名其妙的失败。
        # 不含 :// 的输入（如内网单标签主机名 portal）不做校验，保持宽松。
        if "://" in raw_portal:
            parsed = parse_portal_link(raw_portal)
            if not parsed.portal:
                raise ValueError(parsed.error)
        portal = normalize_portal(raw_portal)
        # 兜底：用户可能没点「解析链接」就直接保存/登录。把截断后的 origin 回写
        # 输入框，保证 config.json 里永远是 origin，而不是带 wlanuserip 的完整链接。
        if portal and portal != raw_portal:
            self.portal_var.set(portal)
            self._append("认证地址已自动去除路径与参数：%s" % portal)
        user = self.user_var.get().strip()
        password = self.pass_var.get()
        if not portal or not user or not password:
            raise ValueError("请填写认证地址、学号和密码！")
        if "@" in user:  # 历史遗留的运营商后缀，去掉
            user = user.split("@")[0]
        return {
            "portalHost": portal,
            "userId": user,
            "password": password,
            "service": self.service_var.get() or DEFAULT_SERVICE,
            "autoStart": self.autostart_var.get(),
            "showNotify": self.notify_var.get(),
            "autoRelogin": self.auto_relogin,
        }

    def _refresh_services_async(self):
        """后台拉取可选运营商，填充下拉框（失败则保留默认列表）"""
        portal = normalize_portal(self.portal_var.get()) or DEFAULT_PORTAL

        def run():
            try:
                sess = new_session(portal)
                sid = get_session_id(sess)
                if sid:
                    names = get_services(sess, sid)
                    if names:
                        self.queue.put(("services", names))
            except Exception:
                pass

        threading.Thread(target=run, daemon=True).start()

    # ---------- 登录与检测 ----------

    def _initial_login(self):
        """启动时自动登录（使用已保存的配置）"""
        cfg = load_config()
        if not (cfg.get("userId") and cfg.get("password")):
            if self.headless:
                self._notify("校园网自动登录", "未找到配置，请打开配置窗口填写")
            return
        log("读取已保存配置，自动登录（学号: %s）" % cfg.get("userId"))
        # 不在这里同步等网关：do_login 自己会用短超时探测就绪状态。
        # 在主线程等会把托盘菜单和窗口一起冻住（最坏 90 秒）。
        self._run_login_thread(cfg, notify=bool(cfg.get("showNotify", True)))

    def _run_login_thread(self, cfg, notify=False, auto=False):
        if self.worker and self.worker.is_alive():
            return False
        if not auto:                # 手动登录：清掉退避/熔断，让用户随时能重试
            self.fail_streak = 0
            self.next_retry_at = 0.0
            self.need_manual = False
        self._set_busy(True)

        def progress(msg):
            log(msg)
            self.queue.put(("log", msg))

        def run():
            ok, _detail, reason = do_login(cfg, progress=progress,
                                           notify=self._notify if notify else None)
            sess = status_session(cfg.get("portalHost") or DEFAULT_PORTAL)
            online, uid = check_online(sess)
            self.queue.put(("status", online, uid, not ok))
            self.queue.put(("done", ok, reason, auto))

        self.worker = threading.Thread(target=run, daemon=True)
        self.worker.start()
        return True

    def _after_login(self, ok, reason, auto):
        """按登录结果更新重试策略；自动重试失败时不弹窗打断用户"""
        if ok is None:                  # 只是手动"检查状态"，不参与重试策略
            return
        if ok:
            self.fail_streak = 0
            self.next_retry_at = 0.0
            self.need_manual = False
            if not auto and self.state() != "withdrawn":
                messagebox.showinfo("校园网", "登录成功！", parent=self)
            return
        if reason == KIND_MANUAL:
            # 账号/密码/验证码/出口配置：再自动重试也没用，只会刷失败次数
            self.need_manual = True
            self.next_retry_at = 0.0
            log("认证类失败，已暂停自动重连；核对学号/密码后点「立即登录」")
            # 自动重连失败也要给用户一条可自救的提示，别让"改密码"变成无解谜
            tip = ("登录失败：账号、密码或出口配置有问题，已暂停自动重连。\n"
                   "若近期修改过校园网/统一身份认证密码，请打开配置窗口重新输入后保存。\n"
                   "详见运行日志。")
            if self.icon:
                self._notify("校园网登录失败", tip)
            if not auto and self.state() != "withdrawn":
                messagebox.showerror("校园网", tip, parent=self)
            return
        # KIND_OTHER（服务端业务错误，如"运营商未响应"）：会退避后重跑完整 CAS
        # 并每次重提密码。不设上限就会在一天内把账号刷到风控/锁定，所以这里
        # 按天累计提交次数，超阈值即转"等待手动处理"。
        if auto:
            today = time.strftime("%Y-%m-%d")
            if self._cas_attempt_date != today:
                self._cas_attempt_date = today
                self._cas_attempts = 0
            self._cas_attempts += 1
            if self._cas_attempts >= CAS_DAILY_LIMIT:
                self.need_manual = True
                self.next_retry_at = 0.0
                log("今日自动认证已达 %d 次上限，暂停自动重连，避免把账号刷到风控；"
                    "请手动点「立即登录」或明天再试" % self._cas_attempts)
                if self.icon:
                    self._notify("校园网", "今日自动登录尝试已达上限，已暂停；"
                                          "请手动登录或明天再试")
                return
        self.fail_streak += 1
        delay = retry_delay(self.fail_streak)
        self.next_retry_at = time.time() + delay
        log("%s失败（%s），连续第 %d 次，%d 秒后再试"
            % ("自动重连" if auto else "登录", reason or "未知",
               self.fail_streak, delay))
        if not auto and self.state() != "withdrawn":
            messagebox.showerror("校园网", "登录失败，详见运行日志。", parent=self)

    def _notify(self, title, message):
        """登录结果通知：有托盘就发系统气泡，绝不在工作线程里新建 Tk"""
        title, message = _redact(title), _redact(message)
        if self.icon:
            try:
                self.icon.notify(str(message), str(title))
                return
            except Exception:
                pass
        self.queue.put(("toast", title, message))

    def _toast(self, title, message, duration=6000):
        """没有托盘时的兜底气泡：主线程 Toplevel，不阻塞、不新建 Tk root"""
        try:
            top = tk.Toplevel(self)
            top.overrideredirect(True)
            top.attributes("-topmost", True)
            frm = tk.Frame(top, bg="#2d2d2d", padx=16, pady=12)
            tk.Label(frm, text=title, bg="#2d2d2d", fg="#ffffff",
                     font=("Microsoft YaHei", 11, "bold")).pack(anchor="w")
            tk.Label(frm, text=message, bg="#2d2d2d", fg="#dddddd",
                     font=("Microsoft YaHei", 9), wraplength=260,
                     justify="left").pack(anchor="w")
            frm.pack()
            top.update_idletasks()
            w, h = top.winfo_width(), top.winfo_height()
            sw, sh = top.winfo_screenwidth(), top.winfo_screenheight()
            top.geometry("+%d+%d" % (sw - w - 20, sh - h - 70))
            top.bind("<Button-1>", lambda e: top.destroy())
            top.after(duration, top.destroy)
        except Exception:
            pass

    def _start_login(self, from_tray=False):
        if from_tray:
            cfg = load_config()
            if not (cfg.get("userId") and cfg.get("password")):
                self._notify("校园网自动登录", "未找到配置，请先配置账号")
                self._show_window()
                return
            hidden = self.state() == "withdrawn"
            self._run_login_thread(cfg, notify=hidden and bool(cfg.get("showNotify", True)))
        else:
            try:
                cfg = self._collect_config()
            except ValueError as e:
                messagebox.showwarning("提示", str(e), parent=self)
                return
            self._run_login_thread(cfg, notify=False)

    # ---------- 事件处理 ----------

    def on_save(self, silent=False):
        try:
            cfg = self._collect_config()
        except ValueError as e:
            messagebox.showwarning("提示", str(e), parent=self)
            return False
        try:
            save_config(cfg)
        except Exception as e:
            log("配置保存失败: %s" % e)
            messagebox.showerror("保存失败",
                                 "无法写入 %s：\n%s" % (CONFIG_FILE, e), parent=self)
            return False
        self.fail_streak = 0        # 配置改过，给自动重连一次新机会
        self.next_retry_at = 0.0
        self.need_manual = False
        if cfg["autoStart"]:
            if set_autostart(True):
                clear_autostart_policy_disabled()
                state = "已开启"
            else:
                state = "开启失败（可能被电脑管家类软件拦截）"
                self._notify("开机自启未生效",
                             "自启项写入后被改回，请在电脑管家「启动项管理」里放行")
        else:
            set_autostart(False)
            state = "已关闭"
        log("配置已保存，学号: %s，出口: %s" % (cfg["userId"], cfg["service"]))
        self._append("配置已保存（学号: %s，出口: %s，开机自启: %s）" % (
            cfg["userId"], cfg["service"], state))
        self._refresh_tray_menu()
        if not silent:
            messagebox.showinfo("已保存", "配置已保存。", parent=self)
        return True

    def on_save_login(self):
        if self.on_save(silent=True):
            cfg = load_config()
            self._run_login_thread(cfg, notify=False)

    def on_check(self):
        if self.worker and self.worker.is_alive():
            return
        portal = normalize_portal(self.portal_var.get()) or DEFAULT_PORTAL
        self._set_busy(True)

        def run():
            online, uid = check_online(status_session(portal))
            self.queue.put(("status", online, uid, False))
            self.queue.put(("done", None))

        self.worker = threading.Thread(target=run, daemon=True)
        self.worker.start()

    def _monitor_tick(self):
        """托盘常驻：周期性检测在线状态；离线时按退避策略决定要不要自动重连"""
        try:
            if not self.monitor_busy and not (self.worker and self.worker.is_alive()):
                cfg = load_config()
                portal = normalize_portal(cfg.get("portalHost"))
                if not portal and self.state() != "withdrawn":
                    portal = normalize_portal(self.portal_var.get())
                if portal:
                    self.monitor_busy = True

                    def run():
                        try:
                            online, uid = check_online(status_session(portal))
                            self.queue.put(("monitor", online, uid))
                        finally:
                            self.monitor_busy = False

                    threading.Thread(target=run, daemon=True).start()
        except Exception:
            pass
        self.after(MONITOR_INTERVAL, self._monitor_tick)

    # ---------- 后台消息轮询 ----------

    def _poll(self):
        try:
            while True:
                item = self.queue.get_nowait()
                try:
                    self._dispatch(item)
                except Exception as e:
                    # 单条消息处理异常不能拖垮整个轮询循环，否则托盘状态
                    # 永久冻结、自动重连彻底失效。记一条日志继续处理下一条。
                    log("处理后台消息失败: %s" % e)
        except queue.Empty:
            pass
        finally:
            # 重调度放在 finally：即使上面抛了没被兜住的异常，也要保证 150ms
            # 后继续轮询，避免轮询线程"死掉"。
            try:
                self.after(150, self._poll)
            except Exception:
                pass

    def _dispatch(self, item):
        kind = item[0]
        if kind == "log":
            self._append(item[1])
        elif kind == "status":
            self._set_tray_status(item[1], item[2], fail=item[3])
        elif kind == "services":
            cur = self.service_var.get()
            # 接口只返回账号能切换的运营商，不能直接覆盖下拉框，
            # 否则「校园网」会被挤掉
            self.service_box.configure(values=merge_services(item[1], cur))
            if cur and cur not in item[1]:
                # 不静默替换用户选的出口，只提示
                log("配置的出口「%s」不在门户返回的列表（%s）中，请确认"
                    % (cur, "、".join(item[1])))
            elif not cur:
                self.service_var.set(DEFAULT_SERVICE)
            log("可选出口: %s" % "、".join(item[1]))
        elif kind == "monitor":
            online, uid = item[1], item[2]
            self._set_tray_status(online, uid)
            if online is True:
                # 已经在线说明账号没问题，解除熔断
                self.fail_streak = 0
                self.next_retry_at = 0.0
                self.need_manual = False
            elif online is False and (self.auto_relogin and not self.need_manual
                  and time.time() >= self.next_retry_at):
                # 只有"确认离线"才触发重连；查询失败(None)时不动状态机，
                # 避免网关/DNS 抖动被误判为离线而反复跑完整 CAS 认证
                if not (self.worker and self.worker.is_alive()):
                    cfg = load_config()
                    log("检测到离线，自动重连（第 %d 次）" % (self.fail_streak + 1))
                    self._run_login_thread(
                        cfg, notify=(self.fail_streak == 0), auto=True)
        elif kind == "action":
            self._handle_tray_action(item[1])
        elif kind == "toast":
            self._toast(item[1], item[2])
        elif kind == "done":
            self._set_busy(False)
            self._after_login(item[1],
                              item[2] if len(item) > 2 else None,
                              item[3] if len(item) > 3 else False)

    def _set_busy(self, busy):
        state = "disabled" if busy else "normal"
        self.login_btn.configure(state=state)
        self.check_btn.configure(state=state)

    def _append(self, text):
        text = _redact(text)
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")


# ---------------------------------------------------------------- 自检

def selftest_report():
    """生成一份给人看的自检报告

    打包后的 exe 没有控制台，`--status` 的 print 看不见；这里把结论同时写进
    login.log 并弹窗，同学只要双击就能把结果截图发过来。
    """
    cfg = load_config()
    portal = normalize_portal(cfg.get("portalHost")) or DEFAULT_PORTAL
    lines = ["版本: %s" % APP_VERSION,
             "程序目录: %s" % SCRIPT_DIR,
             "认证门户: %s" % portal]
    if cfg.get("userId") and cfg.get("password"):
        lines.append("配置: 学号 %s，出口 %s"
                     % (cfg.get("userId"), cfg.get("service") or DEFAULT_SERVICE))
    else:
        lines.append("配置: 未完成（缺学号或密码），请先在配置窗口填写")

    lines.append("运行方式: %s" % ("打包 exe" if IS_FROZEN else "源码运行"))
    value = autostart_registered_value()
    if value is None:
        auto_detail = "未设置"
    else:
        problems = autostart_problems(value)
        auto_detail = "正常" if not problems else "；".join(problems)
    lines.append("开机自启: 配置%s，%s"
                 % ("开启" if cfg.get("autoStart") else "关闭", auto_detail))

    ips = local_ipv4_list()
    lines.append("本机地址: %s%s"
                 % (", ".join(ips) or "无",
                    "" if has_campus_ip() else "（不在校园网网段）"))
    try:
        import cryptography        # noqa: F401  只检查能不能 import
        lines.append("加密库 cryptography: 可用")
    except ImportError:
        lines.append("加密库 cryptography: 缺失，登录会失败（pip install cryptography）")

    sess = status_session(portal)
    lines.append("出口网卡: %s" % (sess.local_ip or "系统默认路由"))
    info = get_redirect_info(sess, retries=1)
    sid = info.get("sessionId")
    if sid:
        lines.append("门户会话: 已取得 sessionId=%s" % sid)
        services = get_services(sess, sid)
        if services:
            lines.append("可选出口: %s" % "、".join(merge_services(services)))
    else:
        lines.append("门户会话: %s" % describe_no_session(info))
    online, uid = check_online(sess)
    lines.append("在线状态: %s%s" % ("在线" if online else "离线",
                                   "（%s）" % uid if uid else ""))
    return lines


def run_selftest():
    lines = selftest_report()
    for line in lines:
        log("自检 | " + line)          # log() 内部会脱敏
    text = _redact("\n".join(lines))
    try:
        print(text)                 # pythonw 下 stdout 是 None，忽略即可
    except Exception:
        pass
    try:
        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo("校园网自动登录 · 自检", text)
        root.destroy()
    except Exception:
        try:
            ctypes.windll.user32.MessageBoxW(
                None, text, "校园网自动登录 · 自检", 0x40)
        except Exception:
            pass


# ---------------------------------------------------------------- 入口

def main():
    if "--selftest" in sys.argv[1:]:
        run_selftest()
        return
    if "--status" in sys.argv[1:]:
        cfg = load_config()
        online, uid = check_online(
            status_session(cfg.get("portalHost") or DEFAULT_PORTAL))
        msg = "在线 %s" % (uid or "") if online else "离线"
        log("状态查询: " + msg)
        try:
            print(msg)              # 打包版没有 stdout，写日志兜底
        except Exception:
            pass
        return
    cleanup_stale_mei()
    global _SINGLETON_HANDLE
    first, _SINGLETON_HANDLE = acquire_single_instance()
    if not first:
        if "--login" not in sys.argv[1:]:   # 自启重复触发时静默退出
            log("已有实例在运行，本次启动退出")
            try:
                ctypes.windll.user32.MessageBoxW(
                    None, "校园网自动登录已经在运行，请查看系统托盘。",
                    "校园网自动登录", 0x40)
            except Exception:
                pass
        return
    try:                            # 程序目录被移动后，自启项自动跟随
        refresh_autostart_if_enabled()
    except Exception:
        pass
    try:                            # 旧配置里的明文学号/密码就地加密
        migrate_config()
    except Exception:
        pass
    try:                            # 旧日志按新规则重写一遍
        scrub_log()
    except Exception:
        pass
    headless = "--login" in sys.argv[1:]
    app = CampusApp(headless=headless)
    app.mainloop()


if __name__ == "__main__":
    main()
