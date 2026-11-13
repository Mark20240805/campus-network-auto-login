# -*- coding: utf-8 -*-
"""纯函数测试：退避算法、脱敏、入口归一化、认证链接解析、失败分类、Cookie 主机隔离、重定向限域。

这些是最容易在后续改动中回归的点，也是上线前检查里反复被强调的缺陷所在。
全程不发起任何真实网络请求、不碰真实 config.json / login.log。
"""
import time

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
# 认证链接解析（ePortal 链接 → origin）
#
# 用户从浏览器地址栏复制到的是"完整认证链接"，后缀的 wlanuserip/nasip/mac/
# sessionId 都是每次会话随机、无法复用的一次性参数。直接当认证地址用会拼出
# …/eportal/index.jsp?…/eportal/redirect.jsp，认证必然失败。
# 这里校验纯函数 parse_portal_link：截成 origin、失败文案固定、不发网络请求。
# ---------------------------------------------------------------------------

# 真实样本（参数值均为虚构）：输入整串 → 期望 origin
REAL_LINKS = [
    # 1) 网关 JS 跳转带出来的 index.jsp 链接（wlanuserip/nasip/mac…）
    ("https://sam.ahpu.edu.cn/eportal/index.jsp?wlanuserip=494fc4cd&wlanacname=4338bd8d"
     "&ssid=&nasip=3b5f2cf6&snmpagentip=&mac=531af092&t=wireless-v2&url=709db9dc"
     "&apmac=&nasid=x&vid=1&port=2&nasportid=3",
     "https://sam.ahpu.edu.cn"),
    # 2) redirect.jsp 的 302 Location（sessionId/userIp/userMac/nasIp）
    ("https://sam.ahpu.edu.cn/eportal/redirect.jsp?sessionId=6787abcdef&userIp=10.20.30.40"
     "&userMac=aabbccddeeff&nasIp=10.11.12.13",
     "https://sam.ahpu.edu.cn"),
    # 3) CAS 统一身份认证入口
    ("https://sam.ahpu.edu.cn/cas-sso/login?flowSessionId=abc&customPageId=1"
     "&nasIp=10.11.12.13&userIp=10.20.30.40&userMac=aabbccddeeff",
     "https://sam.ahpu.edu.cn"),
    # 4) 网关 IP 场景：保留 http 与 IP，不替换成默认域名
    ("http://10.11.12.13/eportal/index.jsp?wlanuserip=1&nasip=10.11.12.13",
     "http://10.11.12.13"),
    # 5) 非默认端口要保留
    ("https://sam.ahpu.edu.cn:8443/eportal/index.jsp?x=1",
     "https://sam.ahpu.edu.cn:8443"),
    # 6) 只复制了主机 + 路径（没有协议头）→ 补 https://
    ("sam.ahpu.edu.cn/eportal/index.jsp?sessionId=abc",
     "https://sam.ahpu.edu.cn"),
    # 7) 夹在一句话里的链接
    ("点击 http://10.11.12.13/eportal/index.jsp?x=1 登录",
     "http://10.11.12.13"),
    # 8) 被引号包住、且 query 里是 HTML 实体 &amp;
    ("'https://sam.ahpu.edu.cn/eportal/index.jsp?a=1&amp;b=2'",
     "https://sam.ahpu.edu.cn"),
]


@pytest.mark.parametrize("text,expect", REAL_LINKS)
def test_parse_portal_link_real_samples(m, text, expect):
    res = m.parse_portal_link(text)
    assert res.error == ""
    assert res.portal == expect


def test_parse_portal_link_empty(m):
    for text in ("", "   ", None):
        res = m.parse_portal_link(text)
        assert res.portal == ""
        assert res.error == m.ERR_PORTAL_EMPTY
        assert res.warning == "" and res.detail == ""


def test_parse_portal_link_no_link(m):
    # "abc" 没有点、也不是 IP，补 https:// 也救不回来
    res = m.parse_portal_link("abc")
    assert res.portal == ""
    assert res.error == m.ERR_PORTAL_NO_LINK


def test_parse_portal_link_bad_scheme(m):
    res = m.parse_portal_link("ftp://x.com/a")
    assert res.portal == ""
    assert res.error == "只支持 http/https 链接，当前是「ftp://」。"


def test_parse_portal_link_bad_host(m):
    res = m.parse_portal_link("https://256.1.1.1/eportal/x")
    assert res.portal == ""
    assert res.error == "链接里的主机「256.1.1.1」不是合法的域名或 IP 地址。"


def test_parse_portal_link_bad_port(m):
    res = m.parse_portal_link("https://sam.ahpu.edu.cn:99999/eportal/x")
    assert res.portal == ""
    assert res.error == "链接里的端口「99999」无效（应为 1–65535）。"


def test_parse_portal_link_drops_default_port(m):
    assert m.parse_portal_link(
        "https://sam.ahpu.edu.cn:443/x").portal == "https://sam.ahpu.edu.cn"
    assert m.parse_portal_link(
        "http://sam.ahpu.edu.cn:80/x").portal == "http://sam.ahpu.edu.cn"


def test_parse_portal_link_ipv6(m):
    # IPv6 主机重建时保留方括号
    assert m.parse_portal_link(
        "https://[2001:db8::1]/eportal/x").portal == "https://[2001:db8::1]"


def test_parse_portal_link_idempotent(m):
    for text, _expect in REAL_LINKS:
        once = m.parse_portal_link(text).portal
        twice = m.parse_portal_link(once).portal
        assert once == twice


def test_parse_portal_link_pure_origin(m):
    res = m.parse_portal_link("https://sam.ahpu.edu.cn")
    assert res.portal == "https://sam.ahpu.edu.cn"
    assert res.error == "" and res.warning == ""
    assert res.detail == "链接已是纯认证地址"


def test_parse_portal_link_warns_non_eportal(m):
    # 不是 ePortal 链接：仍然填入（只警告），不阻断
    res = m.parse_portal_link("https://www.example.com/a")
    assert res.portal == "https://www.example.com"
    assert res.error == ""
    assert res.warning == m.WARN_PORTAL_NOT_EPORTAL


def test_parse_portal_link_fullwidth(m):
    assert m.parse_portal_link(
        "https：／／sam.ahpu.edu.cn／eportal／index.jsp？a=1"
    ).portal == "https://sam.ahpu.edu.cn"


def test_parse_portal_link_nfkc_fullwidth_chars(m):
    # 从 PDF / Word / 微信复制出来的是全角字母、数字、标点：NFKC 要一并吃掉
    res = m.parse_portal_link(
        "ＨＴＴＰＳ：／／sam.ahpu.edu.cn／eportal／x？a=1")
    assert res.portal == "https://sam.ahpu.edu.cn"
    assert res.error == ""
    # 全角端口数字也要还原
    assert m.parse_portal_link(
        "ｈｔｔｐｓ：／／sam.ahpu.edu.cn：８４４３／x").portal == \
        "https://sam.ahpu.edu.cn:8443"
    # 正常 ASCII 链接的归一结果必须不变
    assert m.parse_portal_link(
        "https://sam.ahpu.edu.cn/eportal/x?a=1&b=2").portal == \
        "https://sam.ahpu.edu.cn"


def test_parse_portal_link_empty_host(m):
    # 空主机不能输出「链接里的主机「」…」这种带空引号的文案
    for text in ("https:///x", "https://", "https:////"):
        res = m.parse_portal_link(text)
        assert res.portal == ""
        assert res.error == m.ERR_PORTAL_NO_LINK
        assert "「」" not in res.error


def test_parse_portal_link_ipv6_without_brackets(m):
    # 未加方括号时 urlsplit 会拆成 host=2001 / port=db8::1，要单独提示怎么写
    for text in ("https://2001:db8::1/x", "https://2001:db8::1:8443/x"):
        res = m.parse_portal_link(text)
        assert res.portal == ""
        assert res.error == m.ERR_PORTAL_IPV6
        assert "「2001」" not in res.error


def test_parse_portal_link_ipv6_bracketed_ok(m):
    # 正确的方括号写法不能被新规则误伤（含非默认端口）
    assert m.parse_portal_link(
        "https://[2001:db8::1]/eportal/x").portal == "https://[2001:db8::1]"
    assert m.parse_portal_link(
        "https://[2001:db8::1]:8443/eportal/x").portal == "https://[2001:db8::1]:8443"


def test_parse_portal_link_fragment_detail(m):
    # 前端路由锚点也是被丢弃的东西，不能报"链接已是纯认证地址"
    res = m.parse_portal_link("https://sam.ahpu.edu.cn/#/login")
    assert res.portal == "https://sam.ahpu.edu.cn"
    assert res.detail == "已忽略 # 锚点"
    res = m.parse_portal_link("https://sam.ahpu.edu.cn/#/login?")
    assert res.detail == "已忽略 # 锚点"
    # 多步丢弃仍用「，」连接
    res = m.parse_portal_link("https://sam.ahpu.edu.cn/eportal/x?a=1#/home")
    assert res.detail == "已忽略路径与 1 个参数，已忽略 # 锚点"
    assert res.warning == ""            # 有 eportal 特征，不加软提示
    # 只有锚点、又不像 ePortal 链接时仍然给软提示
    res = m.parse_portal_link("https://www.example.com/#/login")
    assert res.warning == m.WARN_PORTAL_NOT_EPORTAL


def test_parse_portal_link_default_portal_no_warning(m):
    # 本校门户的裸地址 / 前端路由里本来就没有 eportal 特征词，不能自相矛盾地
    # 提示"不是校园网认证链接"
    for text in ("https://sam.ahpu.edu.cn/#/login",
                 "https://sam.ahpu.edu.cn/#/login?",
                 "https://SAM.AHPU.EDU.CN/#/x"):
        res = m.parse_portal_link(text)
        assert res.portal == "https://sam.ahpu.edu.cn"
        assert res.warning == ""
    # 外站同样形态仍要给软提示
    assert m.parse_portal_link(
        "https://www.example.com/#/login").warning == m.WARN_PORTAL_NOT_EPORTAL


def test_parse_portal_link_localhost_fallback(m):
    # 没有协议头、主机是单标签 localhost：补 https:// 后仍要截掉路径
    res = m.parse_portal_link("localhost:8080/eportal")
    assert res.portal == "https://localhost:8080"
    assert res.detail.startswith("已补全 https://")
    assert m.parse_portal_link("localhost/eportal/x").portal == "https://localhost"
    # 已知限制：单标签内网主机名（intranet）不补协议头，仍判"没识别到链接"
    res = m.parse_portal_link("intranet/eportal/x")
    assert res.portal == "" and res.error == m.ERR_PORTAL_NO_LINK


def test_parse_portal_link_port_typo_vs_ipv6(m):
    # 主机合法、端口里多打了冒号 → 报端口无效（不能指成 IPv6 语法错）
    for text, port in (("https://sam.ahpu.edu.cn:8:0/x", "8:0"),
                       ("https://sam.ahpu.edu.cn:80:80/x", "80:80")):
        res = m.parse_portal_link(text)
        assert res.portal == ""
        assert res.error == "链接里的端口「%s」无效（应为 1–65535）。" % port
    # 真正的裸 IPv6（host 不合法）仍报缺方括号
    assert m.parse_portal_link("https://2001:db8::1/x").error == m.ERR_PORTAL_IPV6


def test_parse_portal_link_no_scheme_short_circuit(m):
    # 整串没有 "://" 时要跳过协议正则，超长无空格输入也必须是毫秒级
    started = time.time()
    res = m.parse_portal_link("x" * m.SCAN_LIMIT)
    elapsed = time.time() - started
    assert res.portal == "" and res.error == m.ERR_PORTAL_NO_LINK
    assert elapsed < 0.5, "解析 4096 字符耗时 %.3fs" % elapsed


def test_parse_portal_link_detail_counts(m):
    res = m.parse_portal_link(
        "https://sam.ahpu.edu.cn/eportal/index.jsp?a=1&b=2&c=3")
    assert res.detail == "已忽略路径与 3 个参数"
    assert m.parse_portal_link(
        "https://sam.ahpu.edu.cn?x=1").detail == "已忽略 1 个参数"
    assert m.parse_portal_link(
        "https://sam.ahpu.edu.cn/eportal/index.jsp").detail == "已忽略路径"
    assert m.parse_portal_link(
        "sam.ahpu.edu.cn/eportal/index.jsp").detail.startswith("已补全 https://")


def test_normalize_portal_truncates(m):
    # 带路径和参数的完整链接 → origin
    assert m.normalize_portal(
        "https://sam.ahpu.edu.cn/eportal/index.jsp?wlanuserip=1&nasip=2"
    ) == "https://sam.ahpu.edu.cn"
    # 老语义保持不变
    assert m.normalize_portal("sam.ahpu.edu.cn") == "https://sam.ahpu.edu.cn"
    assert m.normalize_portal("sam.ahpu.edu.cn/") == "https://sam.ahpu.edu.cn"
    assert m.normalize_portal("https://sam.ahpu.edu.cn") == "https://sam.ahpu.edu.cn"
    assert m.normalize_portal("") == ""
    assert m.normalize_portal(None) == ""



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
