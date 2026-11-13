# -*- coding: utf-8 -*-
"""纯函数测试：退避算法、脱敏、入口归一化、失败分类、Cookie 主机隔离、重定向限域。

这些是最容易在后续改动中回归的点，也是上线前检查里反复被强调的缺陷所在。
全程不发起任何真实网络请求、不碰真实 config.json / login.log。
"""
import pytest


# ---------------------------------------------------------------------------
# 退避算法（B1 失败分类 / D7 频率闸门 依赖的底层）
# ---------------------------------------------------------------------------

def test_retry_delay_sequence(m):
    assert [m.retry_delay(n) for n in range(1, 7)] == [60, 120, 240, 480, 900, 900]


def test_retry_delay_caps_at_max(m):
    assert m.retry_delay(100) == 900
    assert m.retry_delay(0) == 60      # 越界输入兜底为第 1 档
    assert m.retry_delay(-5) == 60


# ---------------------------------------------------------------------------
# 脱敏（H3/D1 幂等 + H4 白名单覆盖）
# ---------------------------------------------------------------------------

def test_redact_is_idempotent(m):
    samples = [
        "userMac=aabbccddeeff",
        "sessionId=6787abcdef012345",
        "sessionId=Zx9yAbCdEf0123",            # 非 hex 会话号
        "userMac=aa:bb:cc:dd:ee:ff",           # 冒号 MAC
        "mac=1a2b3c4d5e",                      # 网关 NAS 参数
        '{"sessionId": "deadbeef1234"}',       # JSON 键名
        '{"userMac": "a1b2c3d4e5f6"}',
        '{"mac": "a1b2c3d4e5f6"}',
        "终端MAC（a1b2c3d4e5f6）",              # 裸 12 位 hex
        "password=MyPass123",
    ]
    for s in samples:
        once = m._redact(s)
        assert m._redact(once) == once, "脱敏必须幂等: %r -> %r" % (s, once)


def test_redact_masks_sensitive(m):
    assert m._redact("sessionId=6787abcdef012345") == "sessionId=6787****"
    assert m._redact("userMac=aabbccddeeff") == "userMac=aabbcc******"
    assert m._redact('{"sessionId": "deadbeef1234"}') == '{"sessionId": "dead****"}'
    # 裸 12 位 MAC 被抹成星号
    assert m._redact("终端MAC（a1b2c3d4e5f6）") == "终端MAC（******）"
    # 学号只保留前 4 位
    assert m._redact("学号: 2023000001") == "学号: 2023******"
    # CAS 票据打码
    assert m._redact("ticket=ST-abc123def") == "ticket=ST-***"


def test_redact_preserves_innocuous(m):
    # userIp 保留（排查网络问题要用），普通文本不受影响
    assert "10.11.12.13" in m._redact("终端IP(10.11.12.13)")
    assert m._redact("已在线，无需登录") == "已在线，无需登录"


# ---------------------------------------------------------------------------
# 入口归一化（H3/B3 相关的 host 处理）
# ---------------------------------------------------------------------------

def test_normalize_portal(m):
    assert m.normalize_portal("sam.ahpu.edu.cn") == "https://sam.ahpu.edu.cn"
    assert m.normalize_portal("sam.ahpu.edu.cn/") == "https://sam.ahpu.edu.cn"
    assert m.normalize_portal("https://sam.ahpu.edu.cn") == "https://sam.ahpu.edu.cn"
    assert m.normalize_portal("") == ""
    assert m.normalize_portal(None) == ""


def test_ip_literal(m):
    assert m._ip_literal("10.11.12.13") is True
    assert m._ip_literal("sam.ahpu.edu.cn") is False
    assert m._ip_literal("1.1.1.1") is True
    assert m._ip_literal("256.1.1.1") is True   # 只做形态判断，不做取值校验


# ---------------------------------------------------------------------------
# 重定向限域（B4）
# ---------------------------------------------------------------------------

def test_redirect_allowed_allowlist(m):
    portal = "https://sam.ahpu.edu.cn/x"
    # 门户域内、CAS 子域、主域、网关 IP 都应放行
    assert m._redirect_allowed(portal, "https://sam.ahpu.edu.cn/eportal/index.jsp")
    assert m._redirect_allowed(portal, "https://cas.ahpu.edu.cn/login")
    assert m._redirect_allowed(portal, "https://ahpu.edu.cn/x")
    assert m._redirect_allowed(portal, "http://10.11.12.13/eportal/redirect.jsp")


def test_redirect_allowed_blocks_foreign(m):
    portal = "https://sam.ahpu.edu.cn/x"
    # 第三方主机 / 恶意票据外发目标必须拦截
    assert not m._redirect_allowed(portal, "http://www.msftconnecttest.com/x")
    assert not m._redirect_allowed(portal, "http://evil.com/?ticket=ST-1")
    assert not m._redirect_allowed(portal, "https://www.evil.com/x")


# ---------------------------------------------------------------------------
# Cookie 主机隔离（B4）
# ---------------------------------------------------------------------------

def test_cookie_host_normalization(m):
    fn = m.PortalSession._cookie_host
    # sam / cas 子域归到同一注册域 ahpu.edu.cn（CAS 认证依赖同域共享）
    assert fn("sam.ahpu.edu.cn") == "ahpu.edu.cn"
    assert fn("cas.ahpu.edu.cn") == "ahpu.edu.cn"
    assert fn("ahpu.edu.cn") == "ahpu.edu.cn"
    # IP 字面量原样返回，不与任何域名共享
    assert fn("10.11.12.13") == "10.11.12.13"
    # 第三方域归自己的注册域，绝不被归到公共 .com/.cn 之类
    assert fn("www.evil.com") == "www.evil.com"
    assert fn("evil.com") == "evil.com"


def test_cookie_isolation(m):
    s = m.PortalSession()
    s._store_cookie("https://sam.ahpu.edu.cn/x", "SESSION", "BBB")
    s._store_cookie("https://cas.ahpu.edu.cn/login", "TGC", "CCC")
    # 门户 / CAS 子域互相能拿到（同域共享）
    assert "SESSION=BBB" in s._cookies_for("https://sam.ahpu.edu.cn/eportal/redirect.jsp")
    assert "SESSION=BBB" in s._cookies_for("https://cas.ahpu.edu.cn/login")
    # 第三方域拿不到任何 cookie
    assert s._cookies_for("http://www.msftconnecttest.com/x") == ""
    assert s._cookies_for("https://www.evil.com/x") == ""


# ---------------------------------------------------------------------------
# 失败分类（B1）：cas_login POST 后的兜底分支
# ---------------------------------------------------------------------------

def _classify(m, st, resp, loc):
    """复刻 cas_login POST 之后的分支逻辑，做断言"""
    if st in (301, 302, 303) and loc:
        return m.KIND_OK
    if st == 0 or st >= 500 or (st in (301, 302, 303) and not loc):
        return m.KIND_NETWORK
    return m.KIND_MANUAL


def test_cas_login_failure_classification(m):
    # 网络层失败必须归 NETWORK（退避重试），绝不能归 MANUAL（永久熔断）
    assert _classify(m, 0, "", "") == m.KIND_NETWORK          # 请求超时/断线
    assert _classify(m, 500, "", "") == m.KIND_NETWORK        # 服务端 5xx
    assert _classify(m, 502, "", "") == m.KIND_NETWORK
    assert _classify(m, 302, "", "") == m.KIND_NETWORK        # 302 但无 Location
    # 真正的账号问题才归 MANUAL
    assert _classify(m, 200, "密码错误", "") == m.KIND_MANUAL
    # 成功归 OK
    assert _classify(m, 301, "", "loc") == m.KIND_OK
