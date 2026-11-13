# -*- coding: utf-8 -*-
"""认证链接解析的边界与不变量补充测试。

补充 test_core.py 未覆盖、但交互中真实会遇到的输入（userinfo、IPv6+端口、
一段文字里多个链接、超长粘贴），以及两条不该被破坏的不变量：
  1. 解析是纯函数，绝不发网络请求；
  2. 返回值里不出现链接原文（sessionId / mac / wlanuserip 不能经日志落盘）。
全程不发网络请求、不碰真实 config.json / login.log。
"""
import socket
import time
import urllib.request

import pytest


GATEWAY = ("https://sam.ahpu.edu.cn/eportal/index.jsp?wlanuserip=494fc4cd"
           "&wlanacname=4338bd8d&ssid=&nasip=3b5f2cf6&snmpagentip=&mac=531af092"
           "&t=wireless-v2&url=709db9dc&apmac=&nasid=x&vid=1&port=2"
           "&nasportid=3&userip=10.20.30.40")


# ---------------------------------------------------------------------------
# 主机 / 端口边界
# ---------------------------------------------------------------------------

def test_parse_portal_link_drops_userinfo(m):
    """带 user:pass@ 的链接只能取主机，不能把凭据塞进认证地址"""
    res = m.parse_portal_link("https://user:pass@sam.ahpu.edu.cn/eportal/x?y=1")
    assert res.portal == "https://sam.ahpu.edu.cn"
    assert "user" not in res.portal and "pass" not in res.portal


def test_parse_portal_link_ipv6_with_port(m):
    """IPv6 的方括号必须保留，且非默认端口不能被吞掉"""
    assert m.parse_portal_link(
        "https://[2001:db8::1]:8443/eportal/x").portal == "https://[2001:db8::1]:8443"
    assert m.parse_portal_link(
        "http://[::1]:8080/eportal/x").portal == "http://[::1]:8080"


def test_parse_portal_link_keeps_gateway_ip_and_http(m):
    """网关 IP 场景必须保留 http 与 IP 字面量，绝不替换成默认域名"""
    res = m.parse_portal_link("http://10.11.12.13/eportal/index.jsp?wlanuserip=1")
    assert res.portal == "http://10.11.12.13"
    assert res.portal != m.DEFAULT_PORTAL


def test_parse_portal_link_strips_trailing_dot(m):
    """FQDN 末尾的根点不属于 origin"""
    assert m.parse_portal_link(
        "https://sam.ahpu.edu.cn./eportal/x?a=1").portal == "https://sam.ahpu.edu.cn"


# ---------------------------------------------------------------------------
# 一段文字里多个链接：取第一个"合法"的
# ---------------------------------------------------------------------------

def test_parse_portal_link_takes_first_valid(m):
    """前面那个 http 链接 host 非法时要跳过，继续找下一个"""
    res = m.parse_portal_link(
        "http://bad 以及 https://sam.ahpu.edu.cn/eportal/x?y=1")
    assert res.portal == "https://sam.ahpu.edu.cn"


def test_parse_portal_link_skips_other_schemes(m):
    """别的协议出现在前面不影响后面 http(s) 链接被选中"""
    res = m.parse_portal_link(
        "ftp://x.com/a 然后 https://sam.ahpu.edu.cn/eportal/x?y=1")
    assert res.portal == "https://sam.ahpu.edu.cn"
    assert res.error == ""


# ---------------------------------------------------------------------------
# 超长粘贴：不能卡住界面，且要能截断处理
# ---------------------------------------------------------------------------

def test_parse_portal_link_long_text_stays_fast(m):
    """6000 字的粘贴要毫秒级返回（只扫前 SCAN_LIMIT 个字符）"""
    text = GATEWAY + "前缀" * 3000          # 链接在最前面
    started = time.time()
    res = m.parse_portal_link(text)
    elapsed = time.time() - started
    assert res.portal == "https://sam.ahpu.edu.cn"
    assert elapsed < 1.0, "解析超长文本耗时 %.3fs，会卡住界面" % elapsed


def test_parse_portal_link_truncates_huge_garbage(m):
    """10 万字符垃圾输入也要快速失败，不能抛异常"""
    started = time.time()
    res = m.parse_portal_link("x" * 100000)
    elapsed = time.time() - started
    assert res.portal == "" and res.error == m.ERR_PORTAL_NO_LINK
    assert elapsed < 1.0, "解析 10 万字符耗时 %.3fs" % elapsed


# ---------------------------------------------------------------------------
# 不变量 1：纯函数，绝不发网络请求
# ---------------------------------------------------------------------------

def test_parse_portal_link_makes_no_network_call(m, monkeypatch):
    """粘贴链接时往往还没连上校园网，解析绝不允许碰网络"""
    def boom(*args, **kwargs):
        raise AssertionError("parse_portal_link 发起了网络请求")

    monkeypatch.setattr(socket, "socket", boom)
    monkeypatch.setattr(socket, "create_connection", boom)
    monkeypatch.setattr(urllib.request, "urlopen", boom)

    samples = (GATEWAY, "sam.ahpu.edu.cn/eportal/x?a=1",
               "http://10.11.12.13/eportal/x", "ftp://x.com/a", "", None)
    for text in samples:
        m.parse_portal_link(text)          # 不抛异常即通过


# ---------------------------------------------------------------------------
# 不变量 2：返回值不带链接原文
# ---------------------------------------------------------------------------

def test_parse_portal_link_never_returns_raw_text(m):
    """sessionId / mac / wlanuserip / userIp 等一次性参数不能出现在任何字段里"""
    secrets = ("494fc4cd", "531af092", "709db9dc", "3b5f2cf6",
               "wireless-v2", "wlanuserip", "userip", "sessionid")
    for text in (GATEWAY,
                 "https://sam.ahpu.edu.cn/eportal/redirect.jsp?sessionId=6787abcdef"
                 "&userIp=10.20.30.40&userMac=aabbccddeeff"):
        res = m.parse_portal_link(text)
        blob = " ".join([res.portal, res.error, res.warning, res.detail]).lower()
        leaked = [s for s in secrets if s in blob]
        assert not leaked, "解析结果泄漏了 %r" % (leaked,)


def test_parse_portal_link_error_only_echoes_host_or_port(m):
    """失败文案只回显主机/端口片段，整条链接不能进文案"""
    res = m.parse_portal_link("https://256.1.1.1/eportal/x?sessionId=6787abcdef")
    assert "256.1.1.1" in res.error
    assert "6787abcdef" not in res.error and "eportal/x" not in res.error

    res = m.parse_portal_link("https://sam.ahpu.edu.cn:99999/x?mac=531af092")
    assert "99999" in res.error
    assert "531af092" not in res.error


# ---------------------------------------------------------------------------
# normalize_portal：截断 + 老语义兜底
# ---------------------------------------------------------------------------

def test_normalize_portal_truncates_any_scheme(m):
    assert m.normalize_portal(GATEWAY) == "https://sam.ahpu.edu.cn"
    assert m.normalize_portal(
        "https://sam.ahpu.edu.cn:8443/eportal/x?y=1") == "https://sam.ahpu.edu.cn:8443"
    assert m.normalize_portal(
        "http://10.11.12.13/eportal/index.jsp?x=1") == "http://10.11.12.13"
    assert m.normalize_portal("   https://sam.ahpu.edu.cn/   ") == \
        "https://sam.ahpu.edu.cn"


def test_normalize_portal_legacy_fallback(m):
    """解析失败时退回"补 https:// + 去尾斜杠"，老配置与老返回值不能变"""
    assert m.normalize_portal("") == ""
    assert m.normalize_portal(None) == ""
    assert m.normalize_portal("   ") == ""
    assert m.normalize_portal("sam.ahpu.edu.cn") == "https://sam.ahpu.edu.cn"
    assert m.normalize_portal("sam.ahpu.edu.cn/") == "https://sam.ahpu.edu.cn"
    assert m.normalize_portal("ftp://x.com") == "ftp://x.com"


@pytest.mark.parametrize("origin", [
    "https://sam.ahpu.edu.cn",
    "http://10.11.12.13",
    "https://sam.ahpu.edu.cn:8443",
    "https://[2001:db8::1]",
])
def test_redact_does_not_mangle_origin(m, origin):
    """日志/弹窗里的 origin 不能被脱敏规则误伤"""
    once = m._redact(origin)
    assert once == origin
    assert m._redact(once) == once          # 幂等
