# -*- coding: utf-8 -*-
"""第三轮 bug 修复的回归测试：在线状态三态、授权失败分类、UI 状态机。

前两轮只测了 DNS 纯函数，这里补上会真正影响"自动重连"与"修复后重登"的
几条状态路径。CampusApp 用 object.__new__ 裸造，不实例化 Tk。
"""
import pytest


# ---------------------------------------------------------------------------
# check_online 三态：网络失败/无效响应不能当成"明确离线"
# ---------------------------------------------------------------------------

class _FakeStatusSess:
    def __init__(self, post, get=("200", {}, '{"data":{"portalOnlineUserInfo":{}}}')):
        self.post = post
        self.get_result = get

    def post_json(self, path, obj):
        return self.post

    def get(self, path, follow=True):
        return self.get_result


@pytest.mark.parametrize("post,expected", [
    ((200, {}, '{"data":{"online":false}}'), (False, "")),
    ((200, {}, '{"data":{"online":true}}'), (True, "张三")),
    ((500, {}, ""), (None, "")),
    ((0, {}, "err"), (None, "")),
    ((200, {}, '{"data":{}}'), (None, "")),
    ((200, {}, "not json"), (None, "")),
    ((200, {}, '{"data":{"online":1}}'), (None, "")),   # 非布尔不算在线
])
def test_check_online_tri_state(m, monkeypatch, post, expected):
    monkeypatch.setattr(m, "get_session_id", lambda sess: "sid")
    sess = _FakeStatusSess(post, get=(
        200, {}, '{"data":{"portalOnlineUserInfo":{"userName":"张三"}}}'))
    assert m.check_online(sess) == expected


# ---------------------------------------------------------------------------
# service_login / do_login：授权阶段网络失败必须归 KIND_NETWORK
# ---------------------------------------------------------------------------

class _FakeLoginSess:
    def __init__(self, post):
        self.portal = "https://sam.ahpu.edu.cn"
        self.local_ip = None
        self.timeout = 8
        self.last_cas_location = ""
        self.cookies = {}
        self.post = post

    def post_json(self, path, obj):
        return self.post


def test_service_login_keeps_http_status(m, monkeypatch):
    sess = _FakeLoginSess((0, {}, "boom"))
    st, payload = m.service_login(sess, "sid", "校园网")
    assert st == 0
    assert payload == {}


def test_do_login_network_failure_is_kind_network(m, monkeypatch):
    """授权请求 HTTP 0 时不能归 KIND_OTHER（那会误计入 CAS 每日上限）。"""
    sess = _FakeLoginSess((0, {}, "boom"))
    monkeypatch.setattr(m, "new_session", lambda portal: sess)
    monkeypatch.setattr(m, "check_online", lambda s: (False, ""))
    monkeypatch.setattr(m, "has_campus_ip", lambda: True)
    monkeypatch.setattr(m, "wait_campus_ip", lambda t: True)
    monkeypatch.setattr(m, "wait_network", lambda p, t: True)
    monkeypatch.setattr(m, "get_redirect_info",
                        lambda s, retries=0, interval=0, secondaries=False:
                        {"sessionId": "sid"})
    monkeypatch.setattr(m, "cas_login",
                        lambda s, u, p, info=None: (True, "ticket", m.KIND_OK))
    monkeypatch.setattr(m, "get_services", lambda s, sid: ["校园网"])
    monkeypatch.setattr(m, "current_node", lambda s, sid: {})
    monkeypatch.setattr(m, "get_session_id", lambda s: "sid")

    ok, msg, reason = m.do_login({
        "portalHost": "https://sam.ahpu.edu.cn",
        "userId": "123", "password": "x", "service": "校园网",
    })
    assert ok is False
    assert reason == m.KIND_NETWORK


# ---------------------------------------------------------------------------
# CampusApp 状态机：巡检代际、修复后重登、DNS 未测不误判恢复
# ---------------------------------------------------------------------------

def _bare_app(m):
    app = object.__new__(m.CampusApp)
    app.status_epoch = 0
    app.relogin_pending = False
    app.dns_broken = False
    app.dns_streak = 0
    app.auto_relogin = True
    app.need_manual = False
    app.next_retry_at = 0.0
    app.fail_streak = 0
    app.worker = None
    app.dns_worker = None
    return app


def test_monitor_stale_epoch_dropped(m, monkeypatch):
    app = _bare_app(m)
    app.status_epoch = 5
    app._set_tray_status = lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("旧巡检不该动托盘"))
    app._update_dns_state = lambda *a: (_ for _ in ()).throw(
        AssertionError("旧巡检不该动 DNS"))
    # 不抛异常即通过
    app._dispatch(("monitor", False, "", None, 4))


def test_monitor_fresh_offline_triggers_relogin(m, monkeypatch):
    app = _bare_app(m)
    app.status_epoch = 5
    app._set_tray_status = lambda *a, **k: None
    app._update_dns_state = lambda *a: None
    called = {}
    app._run_login_thread = lambda cfg, notify=False, auto=False: called.update(
        auto=auto)
    monkeypatch.setattr(m, "load_config", lambda: {})
    app._dispatch(("monitor", False, "", None, 5))
    assert called.get("auto") is True


def test_done_stale_epoch_dropped(m, monkeypatch):
    app = _bare_app(m)
    app.status_epoch = 5
    app._set_busy = lambda *a: (_ for _ in ()).throw(AssertionError("旧 done"))
    app._after_login = lambda *a: (_ for _ in ()).throw(AssertionError("旧 done"))
    app._drain_dns_relogin = lambda: (_ for _ in ()).throw(AssertionError("旧 done"))
    app._dispatch(("done", True, m.KIND_OK, False, 4))


def test_dns_fixed_relogins_immediately_when_idle(m, monkeypatch):
    app = _bare_app(m)
    app._update_dns_state = lambda *a: None
    app._notify = lambda *a, **k: None
    started = {}
    app._start_login = lambda from_tray=False: started.update(tray=from_tray)
    app._dispatch(("dns_fixed", "已恢复自动获取"))
    assert app.relogin_pending is False
    assert started.get("tray") is True


def test_dns_fixed_waits_for_busy_worker(m, monkeypatch):
    app = _bare_app(m)
    app._update_dns_state = lambda *a: None
    app._notify = lambda *a, **k: None
    started = []

    class Busy:
        def is_alive(self):
            return True

    app.worker = Busy()
    app._start_login = lambda from_tray=False: started.append(from_tray)
    app._dispatch(("dns_fixed", "已恢复自动获取"))
    assert app.relogin_pending is True
    assert started == []

    app.worker = None
    app._drain_dns_relogin()
    assert app.relogin_pending is False
    assert started == [True]


def test_update_dns_state_none_does_not_clear_broken(m, monkeypatch):
    app = _bare_app(m)
    app.dns_broken = True
    app.dns_streak = 1
    app._notify = lambda *a, **k: None
    app._update_dns_state(None)      # 未测/不在校园网：不得当"已恢复"
    assert app.dns_broken is True
    app._update_dns_state("ok")
    assert app.dns_broken is False
