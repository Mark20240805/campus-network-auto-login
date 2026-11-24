# -*- coding: utf-8 -*-
"""DNS 缓存回退与一键修复的纯逻辑测试。

背景：第三方软件把网卡 DNS 从"自动获取"改成手动后，认证门户域名解析不了，
登录链路整体卡死。1.1.0 加了两层兜底：
  方案2 _attach_dns_fallback：解析失败时改连缓存 IP（Host/SNI/证书仍按域名）；
  方案3 check_dns_health / repair_dns：体检告警 + 提权把网卡 DNS 恢复自动获取。

这里只测纯逻辑：缓存读写/过期、回退白名单、体检三态、PowerShell 脚本构造、
repair_dns 的分支。真实网卡、真实 PowerShell、真实 UAC 都不在测试里碰。
"""
import time

import pytest


@pytest.fixture
def dns_sandbox(m, tmp_path, monkeypatch):
    """把 DNS 缓存重定向到临时目录并清空内存态（monkeypatch 测试后自动还原）"""
    monkeypatch.setattr(m, "_DNS_CACHE_FILE",
                        str(tmp_path / "dns_cache.json"))
    monkeypatch.setattr(m, "_DNS_CACHE", None)
    monkeypatch.setattr(m, "_DNS_CACHE_MTIME", {})
    monkeypatch.setattr(m, "_DNS_CACHE_DIRTY", False)
    monkeypatch.setattr(m, "_DNS_FALLBACK_LOGGED", set())
    return tmp_path


# ---------------------------------------------------------------------------
# 缓存读写（方案2的持久层）
# ---------------------------------------------------------------------------

def test_dns_cache_roundtrip_and_persist(m, dns_sandbox):
    m._dns_cache_put("SAM.AHPU.EDU.CN", "10.11.12.5")     # 大写主机也要能命中
    assert m._dns_cache_get("sam.ahpu.edu.cn") == "10.11.12.5"
    # 内存清空后从磁盘文件重新加载（跨启动生效）
    m._DNS_CACHE = None
    assert m._dns_cache_get("sam.ahpu.edu.cn") == "10.11.12.5"


def test_dns_cache_ignores_ip_literal_and_empty(m, dns_sandbox):
    m._dns_cache_put("10.11.12.13", "1.2.3.4")
    m._dns_cache_put("", "1.2.3.4")
    m._dns_cache_put("sam.ahpu.edu.cn", "")
    assert m._dns_cache_load() == {}


def test_dns_cache_expired(m, dns_sandbox):
    m._dns_cache_put("sam.ahpu.edu.cn", "10.11.12.5")
    m._DNS_CACHE["sam.ahpu.edu.cn"]["ts"] = time.time() - 40 * 86400
    assert m._dns_cache_get("sam.ahpu.edu.cn") is None


def test_dns_cache_write_only_on_change(m, dns_sandbox):
    m._dns_cache_put("sam.ahpu.edu.cn", "10.11.12.5")
    first = dns_sandbox.joinpath("dns_cache.json").read_text(encoding="utf-8")
    m._dns_cache_put("sam.ahpu.edu.cn", "10.11.12.5")     # 同 IP 不落盘
    assert dns_sandbox.joinpath("dns_cache.json").read_text(
        encoding="utf-8") == first
    m._dns_cache_put("sam.ahpu.edu.cn", "10.11.12.6")     # IP 变化才落盘
    assert m._dns_cache_get("sam.ahpu.edu.cn") == "10.11.12.6"


def test_dns_cache_file_corrupt_treated_as_empty(m, dns_sandbox):
    dns_sandbox.joinpath("dns_cache.json").write_text("{not json", encoding="utf-8")
    assert m._dns_cache_get("sam.ahpu.edu.cn") is None


def test_dns_cache_drop(m, dns_sandbox):
    m._dns_cache_put("sam.ahpu.edu.cn", "10.11.12.5")
    m._dns_cache_drop("sam.ahpu.edu.cn")
    assert m._dns_cache_get("sam.ahpu.edu.cn") is None
    # 落盘文件里也清掉了（重启后不复活）
    m._DNS_CACHE = None
    assert m._dns_cache_get("sam.ahpu.edu.cn") is None


def test_dns_cache_drop_ignores_ip_and_missing(m, dns_sandbox):
    m._dns_cache_put("sam.ahpu.edu.cn", "10.11.12.5")
    m._dns_cache_drop("10.11.12.13")          # IP 字面量不该动缓存
    m._dns_cache_drop("cas.ahpu.edu.cn")      # 不存在：不报错、不重写文件
    assert m._dns_cache_get("sam.ahpu.edu.cn") == "10.11.12.5"


def test_dns_cache_put_refreshes_file_after_24h(m, dns_sandbox):
    m._dns_cache_put("sam.ahpu.edu.cn", "10.11.12.5")
    # 模拟"这条缓存是 25 小时前落盘的"
    m._DNS_CACHE_MTIME["sam.ahpu.edu.cn"] = time.time() - 25 * 3600
    before = dns_sandbox.joinpath("dns_cache.json").read_text(encoding="utf-8")
    m._dns_cache_put("sam.ahpu.edu.cn", "10.11.12.5")     # 同 IP 但超过 24h → 重写
    after = dns_sandbox.joinpath("dns_cache.json").read_text(encoding="utf-8")
    assert before != after
    before = after
    m._dns_cache_put("sam.ahpu.edu.cn", "10.11.12.5")     # 24h 内同 IP → 不再写盘
    assert dns_sandbox.joinpath("dns_cache.json").read_text(
        encoding="utf-8") == before


def test_request_clears_dns_cache_on_cert_error(m, dns_sandbox):
    """投毒防护：域名证书校验失败时必须清掉它的缓存条目。

    场景：DNS 被劫持但"解析成功"→ 错误 IP 被记进缓存 → TLS 验证失败。
    不清缓存的话，之后 DNS 真挂时回退会一直撞这个假 IP。
    """
    import ssl

    class BoomConn:
        def request(self, *a, **k):
            raise ssl.SSLCertVerificationError(1, "CERTIFICATE_VERIFY_FAILED")

        def close(self):
            pass

    m._dns_cache_put("sam.ahpu.edu.cn", "10.11.12.5")
    sess = m.PortalSession("https://sam.ahpu.edu.cn")
    sess._conn = lambda url, timeout=None: BoomConn()
    st, _hd, _body = sess.request("https://sam.ahpu.edu.cn/eportal/redirect.jsp",
                                  follow=False)
    assert st == 0
    assert m._dns_cache_get("sam.ahpu.edu.cn") is None


# ---------------------------------------------------------------------------
# 回退白名单（口径与重定向限域一致）
# ---------------------------------------------------------------------------

def test_dns_fallback_allowed(m):
    portal = "https://sam.ahpu.edu.cn"
    assert m._dns_fallback_allowed("sam.ahpu.edu.cn", portal)
    assert m._dns_fallback_allowed("cas.ahpu.edu.cn", portal)
    assert m._dns_fallback_allowed("AHPU.EDU.CN", portal)      # 大小写不敏感
    # 探测地址等无关域名、IP 字面量、空主机一律不放行
    assert not m._dns_fallback_allowed("www.msftconnecttest.com", portal)
    assert not m._dns_fallback_allowed("10.11.12.13", portal)
    assert not m._dns_fallback_allowed("", portal)
    # ahpu.edu.cn 是可信域名族（与重定向白名单同口径），无论门户配置成什么
    assert m._dns_fallback_allowed("sam.ahpu.edu.cn",
                                   "https://portal.other.edu.cn")
    # 用户改配了别校门户：门户主机本身跟随配置放行
    assert m._dns_fallback_allowed("portal.other.edu.cn",
                                   "https://portal.other.edu.cn")
    assert not m._dns_fallback_allowed("evil.example.com",
                                       "https://portal.other.edu.cn")


# ---------------------------------------------------------------------------
# 连接层回退（_attach_dns_fallback）
# ---------------------------------------------------------------------------

class _FakeSock:
    def __init__(self, ip):
        self._ip = ip

    def getpeername(self):
        return (self._ip, 443)


def _fake_conn(handler):
    class Conn:
        pass
    conn = Conn()
    conn._create_connection = handler
    return conn


def test_attach_dns_fallback_caches_success(m, dns_sandbox):
    conn = _fake_conn(lambda addr, t, s: _FakeSock("10.11.12.5"))
    m._attach_dns_fallback(conn, "sam.ahpu.edu.cn", "https://sam.ahpu.edu.cn")
    sock = conn._create_connection(("sam.ahpu.edu.cn", 443), 5, None)
    assert sock.getpeername()[0] == "10.11.12.5"
    assert m._dns_cache_get("sam.ahpu.edu.cn") == "10.11.12.5"


def test_attach_dns_fallback_falls_back_on_dns_error(m, dns_sandbox):
    m._dns_cache_put("sam.ahpu.edu.cn", "10.11.12.5")
    calls = []

    def handler(addr, timeout, src):
        calls.append(addr)
        if addr[0] == "sam.ahpu.edu.cn":
            import socket
            raise socket.gaierror("name resolution failed")  # DNS 坏了
        return _FakeSock(addr[0])

    conn = _fake_conn(handler)
    m._attach_dns_fallback(conn, "sam.ahpu.edu.cn", "https://sam.ahpu.edu.cn")
    sock = conn._create_connection(("sam.ahpu.edu.cn", 443), 5, None)
    # 第一次按域名解析失败，第二次改连缓存 IP，端口不变
    assert calls == [("sam.ahpu.edu.cn", 443), ("10.11.12.5", 443)]
    assert sock.getpeername()[0] == "10.11.12.5"


def test_attach_dns_fallback_reraises_without_cache(m, dns_sandbox):
    import socket

    def handler(addr, timeout, src):
        raise socket.gaierror("name resolution failed")

    conn = _fake_conn(handler)
    m._attach_dns_fallback(conn, "sam.ahpu.edu.cn", "https://sam.ahpu.edu.cn")
    with pytest.raises(OSError):
        conn._create_connection(("sam.ahpu.edu.cn", 443), 5, None)


def test_attach_dns_fallback_skips_foreign_host(m, dns_sandbox):
    # 非门户域名不挂钩子：探测地址失败就失败（有 1.1.1.1 IP 探测兜底）
    conn = _fake_conn(lambda addr, t, s: _FakeSock("93.184.216.34"))
    m._attach_dns_fallback(conn, "www.msftconnecttest.com",
                           "https://sam.ahpu.edu.cn")
    conn._create_connection(("www.msftconnecttest.com", 80), 2, None)
    assert m._dns_cache_load() == {}


# ---------------------------------------------------------------------------
# DNS 体检（check_dns_health 三态）
# ---------------------------------------------------------------------------

def test_dns_health_broken_on_campus(m, monkeypatch):
    monkeypatch.setattr(m, "has_campus_ip", lambda: True)
    state = m.check_dns_health(
        "https://sam.ahpu.edu.cn", resolver=lambda host: (_ for _ in ()).throw(
            OSError("gaierror")))
    assert state == "broken"


def test_dns_health_ok_on_campus(m, monkeypatch):
    monkeypatch.setattr(m, "has_campus_ip", lambda: True)
    state = m.check_dns_health(
        "https://sam.ahpu.edu.cn", resolver=lambda host: ["10.11.12.5"])
    assert state == "ok"


def test_dns_health_unknown_off_campus(m, monkeypatch):
    monkeypatch.setattr(m, "has_campus_ip", lambda: False)
    # 不在校园网：解析不了也不能下结论（外网本来可能解析正常/异常）
    state = m.check_dns_health(
        "https://sam.ahpu.edu.cn", resolver=lambda host: (_ for _ in ()).throw(
            OSError("gaierror")))
    assert state is None


def test_dns_health_ip_portal_needs_no_dns(m, monkeypatch):
    monkeypatch.setattr(m, "has_campus_ip", lambda: True)
    assert m.check_dns_health("http://10.11.12.13") == "ok"


def test_dns_resolves_swallow_exception(m):
    def boom(host):
        raise RuntimeError("unexpected")
    assert m.dns_resolves("sam.ahpu.edu.cn", resolver=boom) is False
    assert m.dns_resolves("10.11.12.13") is True      # IP 不查 DNS
    assert m.dns_resolves("", resolver=boom) is True


# ---------------------------------------------------------------------------
# PowerShell 脚本构造与 repair_dns 分支
# ---------------------------------------------------------------------------

def test_ps_quote_and_reset_script(m):
    assert m._ps_quote("以太网") == "'以太网'"
    assert m._ps_quote("a'b") == "'a''b'"
    script = m._reset_dns_script(["以太网", "WLAN"])
    assert "Set-DnsClientServerAddress -InterfaceAlias '以太网'" in script
    assert "-InterfaceAlias 'WLAN'" in script
    assert "$ErrorActionPreference='Stop'" in script
    assert "Clear-DnsClientCache" in script
    assert "exit 1" in script


def test_repair_dns_no_adapter(m, monkeypatch):
    monkeypatch.setattr(m, "find_campus_adapters", lambda: [])
    ok, detail = m.repair_dns("https://sam.ahpu.edu.cn")
    assert ok is False
    assert "10.x" in detail


def test_repair_dns_adapter_query_failed(m, monkeypatch):
    monkeypatch.setattr(m, "find_campus_adapters", lambda: None)
    ok, detail = m.repair_dns("https://sam.ahpu.edu.cn")
    assert ok is False


def test_repair_dns_runner_failed(m, monkeypatch):
    monkeypatch.setattr(m, "find_campus_adapters", lambda: ["以太网"])
    ok, detail = m.repair_dns(
        "https://sam.ahpu.edu.cn",
        runner=lambda script: (False, "用户取消了 UAC"))
    assert ok is False
    assert "取消" in detail


def test_repair_dns_success_relogin_hint(m, monkeypatch):
    monkeypatch.setattr(m, "find_campus_adapters", lambda: ["以太网", "WLAN"])
    seen = {}

    def runner(script):
        seen["script"] = script
        return True, ""

    ok, detail = m.repair_dns(
        "https://sam.ahpu.edu.cn",
        resolver=lambda host: ["10.11.12.5"],      # 修复后立刻能解析
        runner=runner)
    assert ok is True
    assert "以太网" in detail and "WLAN" in detail
    assert "Set-DnsClientServerAddress" in seen["script"]


def test_repair_dns_still_unresolvable_after_reset(m, monkeypatch):
    monkeypatch.setattr(m, "find_campus_adapters", lambda: ["以太网"])
    monkeypatch.setattr(m.time, "sleep", lambda s: None)

    def boom(host):
        raise OSError("still broken")

    ok, detail = m.repair_dns(
        "https://sam.ahpu.edu.cn", resolver=boom,
        runner=lambda script: (True, ""))
    assert ok is False
    assert "仍解析不了" in detail


def test_repair_dns_ip_portal_skips_verification(m, monkeypatch):
    """门户本身是 IP 时没有"解析"可验证，命令执行完即视为成功"""
    monkeypatch.setattr(m, "find_campus_adapters", lambda: ["以太网"])
    calls = []

    def resolver(host):
        calls.append(host)
        raise OSError("should not be called")

    ok, detail = m.repair_dns(
        "http://10.11.12.13", resolver=resolver,
        runner=lambda script: (True, ""))
    assert ok is True
    assert calls == []          # 验证被跳过


# ---------------------------------------------------------------------------
# 第三轮回归：异常类型、写盘失败恢复、提权进程结果
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("error", [TimeoutError("timeout"),
                                   ConnectionRefusedError("refused")])
def test_dns_fallback_does_not_retry_tcp_errors(m, dns_sandbox, error):
    """TCP 失败不是 DNS 失败，不能改连旧 IP，也不能把超时翻倍。"""
    m._dns_cache_put("sam.ahpu.edu.cn", "10.11.12.5")
    calls = []

    def handler(addr, timeout, src):
        calls.append(addr)
        raise error

    conn = _fake_conn(handler)
    m._attach_dns_fallback(conn, "sam.ahpu.edu.cn", "https://sam.ahpu.edu.cn")
    with pytest.raises(type(error)):
        conn._create_connection(("sam.ahpu.edu.cn", 443), 5, None)
    assert calls == [("sam.ahpu.edu.cn", 443)]


def test_dns_cache_write_failure_retries_next_put(m, dns_sandbox, monkeypatch):
    monkeypatch.setattr(m, "log", lambda msg: None)
    real_dump = m.json.dump

    def fail_dump(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(m.json, "dump", fail_dump)
    m._dns_cache_put("sam.ahpu.edu.cn", "10.11.12.5")
    assert m._DNS_CACHE_DIRTY is True
    assert "sam.ahpu.edu.cn" not in m._DNS_CACHE_MTIME

    monkeypatch.setattr(m.json, "dump", real_dump)
    m._dns_cache_put("sam.ahpu.edu.cn", "10.11.12.5")
    assert m._DNS_CACHE_DIRTY is False
    m._DNS_CACHE = None
    assert m._dns_cache_get("sam.ahpu.edu.cn") == "10.11.12.5"


def test_dns_cache_drop_write_failure_retries(m, dns_sandbox, monkeypatch):
    monkeypatch.setattr(m, "log", lambda msg: None)
    m._dns_cache_put("sam.ahpu.edu.cn", "10.11.12.5")
    real_dump = m.json.dump

    def fail_dump(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(m.json, "dump", fail_dump)
    m._dns_cache_drop("sam.ahpu.edu.cn")
    assert m._DNS_CACHE_DIRTY is True
    assert "sam.ahpu.edu.cn" not in m._DNS_CACHE_MTIME

    monkeypatch.setattr(m.json, "dump", real_dump)
    m._dns_cache_drop("sam.ahpu.edu.cn")      # 条目已不在内存，靠 dirty 重试落盘
    assert m._DNS_CACHE_DIRTY is False
    m._DNS_CACHE = None
    assert m._dns_cache_get("sam.ahpu.edu.cn") is None


class _FakeKernel32:
    """按脚本编排的 WaitForSingleObject / GetExitCodeProcess / TerminateProcess"""

    def __init__(self, waits, exit_code=0, get_exit_ok=True, terminate_ok=True):
        self.waits = list(waits)
        self.exit_code = exit_code
        self.get_exit_ok = get_exit_ok
        self.terminate_ok = terminate_ok
        self.terminated = []

    def WaitForSingleObject(self, handle, ms):
        return self.waits.pop(0)

    def GetExitCodeProcess(self, handle, out_ref):
        if not self.get_exit_ok:
            return False
        out_ref._obj.value = self.exit_code
        return True

    def TerminateProcess(self, handle, code):
        self.terminated.append((handle, code))
        return self.terminate_ok


class _DWORD:
    def __init__(self):
        self.value = 0


def test_elevated_process_nonzero_exit_reported(m):
    k = _FakeKernel32(waits=[0], exit_code=5)
    ok, detail = m._wait_elevated_process(k, 1234, 60)
    assert ok is False
    assert "退出码 5" in detail


def test_elevated_process_wait_failed(m):
    k = _FakeKernel32(waits=[0xFFFFFFFF])
    ok, detail = m._wait_elevated_process(k, 1234, 60)
    assert ok is False
    assert "等待提权命令失败" in detail


def test_elevated_process_timeout_terminates(m):
    k = _FakeKernel32(waits=[258, 0])
    ok, detail = m._wait_elevated_process(k, 1234, 60)
    assert ok is False
    assert "已终止" in detail
    assert k.terminated == [(1234, 1)]


def test_elevated_process_success(m):
    k = _FakeKernel32(waits=[0], exit_code=0)
    ok, detail = m._wait_elevated_process(k, 1234, 60)
    assert ok is True
    assert detail == ""
