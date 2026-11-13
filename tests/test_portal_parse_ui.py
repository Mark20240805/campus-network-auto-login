# -*- coding: utf-8 -*-
"""UI 接线测试：「解析链接」按钮 / 粘贴自动解析 / 保存时截断回写。

用 Fake 对象承载 CampusApp 的这几个方法（真正的 Tk 控件换成最小替身），
因此不实例化 CampusApp、不启托盘、不起线程、不弹窗、不写 login.log，
也不发起任何网络请求。

覆盖 _parse_portal(manual=True/False) 的六条链路：
  1) 输入框里有完整链接；2) 输入框空、剪贴板有链接；3) 二者都空；
  4) 解析失败；5) 已经是纯 origin；6) 非 ePortal 链接（软警告仍填入）
以及 _auto_parse_portal_after_paste 的触发条件和 _collect_config 的截断回写。
"""
import pytest


class _Var:
    """替身 StringVar / BooleanVar：只需要 get/set"""

    def __init__(self, value=""):
        self._value = value

    def get(self):
        return self._value

    def set(self, value):
        self._value = value


class _Entry:
    """替身 ttk.Entry：只记录光标/视图调用"""

    def __init__(self):
        self.calls = []

    def icursor(self, index):
        self.calls.append(("icursor", index))

    def xview(self, index):
        self.calls.append(("xview", index))


class FakeApp:
    """只提供 _parse_portal / _collect_config 依赖的属性"""

    def __init__(self, portal="", clipboard="", user="2023000001",
                 password="pw", service="校园网"):
        self.portal_var = _Var(portal)
        self.user_var = _Var(user)
        self.pass_var = _Var(password)
        self.service_var = _Var(service)
        self.autostart_var = _Var(False)
        self.notify_var = _Var(True)
        self.auto_relogin = True
        self.portal_entry = _Entry()
        self.appended = []
        self.clipboard = clipboard

    # --- 被测方法的依赖 ---
    def _append(self, text):
        self.appended.append(text)

    def _clipboard_text(self):
        return self.clipboard


# CampusApp 里这些方法只用到上面那几个属性，直接绑到 FakeApp 上复用真实实现
_BOUND_METHODS = ("_parse_portal", "on_parse_portal",
                  "_auto_parse_portal_after_paste", "_collect_config")


@pytest.fixture
def make_app(m, monkeypatch):
    """构造 FakeApp 并拦截 messagebox / log（绝不弹窗、绝不写日志文件）"""
    warnings = []
    monkeypatch.setattr(m.messagebox, "showwarning",
                        lambda title, message, **kw: warnings.append((title, message)))
    monkeypatch.setattr(m, "log", lambda message: None)

    def factory(**kwargs):
        app = FakeApp(**kwargs)
        for name in _BOUND_METHODS:
            setattr(app, name, getattr(m.CampusApp, name).__get__(app, FakeApp))
        app.warnings = warnings
        return app

    return factory


LINK = ("https://sam.ahpu.edu.cn/eportal/index.jsp?wlanuserip=494fc4cd"
        "&nasip=3b5f2cf6&mac=531af092&t=wireless-v2")
ORIGIN = "https://sam.ahpu.edu.cn"


# ---------------------------------------------------------------------------
# 链路 1：输入框里有完整链接
# ---------------------------------------------------------------------------

def test_parse_button_truncates_entry_link(make_app):
    app = make_app(portal=LINK)
    assert app._parse_portal(manual=True) == ORIGIN
    assert app.portal_var.get() == ORIGIN
    assert app.warnings == []                       # 成功不弹窗
    assert ("icursor", "end") in app.portal_entry.calls


def test_parse_button_logs_only_origin_and_count(make_app):
    app = make_app(portal=LINK)
    app._parse_portal(manual=True)
    joined = " ".join(app.appended)
    assert ORIGIN in joined
    assert "494fc4cd" not in joined and "wlanuserip" not in joined


# ---------------------------------------------------------------------------
# 链路 2：输入框空，读剪贴板兜底
# ---------------------------------------------------------------------------

def test_parse_button_falls_back_to_clipboard(make_app):
    app = make_app(portal="", clipboard=LINK)
    assert app._parse_portal(manual=True) == ORIGIN
    assert app.portal_var.get() == ORIGIN
    assert app.warnings == []


# ---------------------------------------------------------------------------
# 链路 3：输入框与剪贴板都空
# ---------------------------------------------------------------------------

def test_parse_button_empty_shows_warning(make_app):
    app = make_app(portal="", clipboard="")
    assert app._parse_portal(manual=True) is None
    assert len(app.warnings) == 1
    assert app.warnings[0][0] == "无法解析链接"


def test_auto_parse_empty_is_silent(make_app):
    app = make_app(portal="", clipboard="")
    assert app._parse_portal(manual=False) is None
    assert app.warnings == []


# ---------------------------------------------------------------------------
# 链路 4：解析失败——弹窗给原因，但绝不改动输入框
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad,reason", [
    ("ftp://x.com/a", "只支持 http/https 链接，当前是「ftp://」。"),
    ("abc", "没有识别到有效的认证链接。请粘贴以 http:// 或 https:// 开头的完整认证链接。"),
])
def test_parse_failure_keeps_input_and_warns(make_app, bad, reason):
    app = make_app(portal=bad)
    assert app._parse_portal(manual=True) is None
    assert app.portal_var.get() == bad              # 原文保留，便于核对
    assert app.warnings == [("无法解析链接", reason)]
    assert app.appended == []                       # 失败不写日志


def test_auto_parse_failure_is_silent(make_app):
    app = make_app(portal="ftp://x.com/a")
    assert app._parse_portal(manual=False) is None
    assert app.warnings == []
    assert app.portal_var.get() == "ftp://x.com/a"


# ---------------------------------------------------------------------------
# 链路 5：已经是纯 origin，不重复写、不多余移动光标
# ---------------------------------------------------------------------------

def test_parse_origin_is_not_rewritten(make_app):
    app = make_app(portal=ORIGIN)
    assert app._parse_portal(manual=True) == ORIGIN
    assert app.portal_entry.calls == []
    assert any("已是纯认证地址" in line for line in app.appended)


# ---------------------------------------------------------------------------
# 链路 6：非 ePortal 链接——软警告，但仍然填入
# ---------------------------------------------------------------------------

def test_non_eportal_link_still_fills(make_app):
    app = make_app(portal="https://www.example.com/a")
    assert app._parse_portal(manual=True) == "https://www.example.com"
    assert app.portal_var.get() == "https://www.example.com"
    assert app.warnings == []                       # 软警告走日志，不打断用户
    assert any("未检测到锐捷" in line for line in app.appended)


# ---------------------------------------------------------------------------
# 粘贴后自动解析的触发条件
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,should_fire", [
    ("https://sam.ahpu.edu.cn/eportal/index.jsp?x=1", True),
    ("https://sam.ahpu.edu.cn/eportal/index.jsp", True),
    ("https://sam.ahpu.edu.cn/cas-sso/login", True),
    ("https://sam.ahpu.edu.cn/portal/x", True),
    ("https://sam.ahpu.edu.cn", False),      # 纯 origin 不动
    ("https://sam.ahpu.edu.cn:8443", False), # 只想改端口时不动
    ("sam.ahpu.edu.cn", False),
])
def test_auto_parse_after_paste_trigger(make_app, text, should_fire):
    app = make_app(portal=text)
    app._auto_parse_portal_after_paste()
    assert bool(app.appended) is should_fire


def test_auto_parse_after_paste_fullwidth(make_app):
    """从 PDF / Word / 微信复制的是全角链接：标记匹配前要先做 NFKC 归一"""
    text = "ＨＴＴＰＳ：／／sam.ahpu.edu.cn／ｅｐｏｒｔａｌ／ｘ？ａ＝１"
    app = make_app(portal=text)
    app._auto_parse_portal_after_paste()
    assert app.portal_var.get() == ORIGIN
    assert any(ORIGIN in line for line in app.appended)


def test_auto_parse_after_paste_fullwidth_query_only(make_app):
    """只有全角 ? 也要能触发"""
    app = make_app(portal="https：／／sam.ahpu.edu.cn？ａ＝１")
    app._auto_parse_portal_after_paste()
    assert app.portal_var.get() == ORIGIN


# ---------------------------------------------------------------------------
# 保存时截断回写：用户没点「解析链接」直接保存
# ---------------------------------------------------------------------------

def test_collect_config_writes_back_origin(make_app):
    app = make_app(portal=LINK)
    cfg = app._collect_config()
    assert cfg["portalHost"] == ORIGIN
    assert app.portal_var.get() == ORIGIN           # 输入框同步成 origin
    assert any("已自动去除路径与参数" in line for line in app.appended)
    assert "494fc4cd" not in str(cfg)


def test_collect_config_origin_unchanged(make_app):
    app = make_app(portal=ORIGIN)
    assert app._collect_config()["portalHost"] == ORIGIN
    assert not any("已自动去除" in line for line in app.appended)


def test_collect_config_adds_scheme(make_app):
    app = make_app(portal="sam.ahpu.edu.cn")
    assert app._collect_config()["portalHost"] == ORIGIN


def test_collect_config_requires_portal(make_app):
    app = make_app(portal="")
    with pytest.raises(ValueError):
        app._collect_config()


# ---------------------------------------------------------------------------
# 保存前拦截：写了 :// 却解析不出 origin 的地址不许进 config.json
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad,reason", [
    # 老兜底会把它变成 "https://http:" 写进配置，下次启动只会莫名失败
    ("http://", "没有识别到有效的认证链接。请粘贴以 http:// 或 https:// 开头的完整认证链接。"),
    ("https:///x", "没有识别到有效的认证链接。请粘贴以 http:// 或 https:// 开头的完整认证链接。"),
    ("ftp://x.com/a", "只支持 http/https 链接，当前是「ftp://」。"),
    ("https://256.1.1.1/x", "链接里的主机「256.1.1.1」不是合法的域名或 IP 地址。"),
    ("https://sam.ahpu.edu.cn:99999/x", "链接里的端口「99999」无效（应为 1–65535）。"),
    ("https://2001:db8::1/x", "链接里的 IPv6 地址缺少方括号，请写成 https://[地址]/… 的形式。"),
])
def test_collect_config_rejects_unparsable_portal(make_app, bad, reason):
    app = make_app(portal=bad)
    with pytest.raises(ValueError) as excinfo:
        app._collect_config()
    assert str(excinfo.value) == reason
    assert app.portal_var.get() == bad          # 原文保留，便于用户核对
    assert app.appended == []                   # 失败不写日志


@pytest.mark.parametrize("text,expect", [
    # 不含 :// 的输入保持宽松：内网单标签主机名不该被当成非法地址拦掉
    ("portal", "https://portal"),
    ("portal:8080", "https://portal:8080"),
    ("sam.ahpu.edu.cn", ORIGIN),
    ("https://sam.ahpu.edu.cn:8443", "https://sam.ahpu.edu.cn:8443"),
])
def test_collect_config_accepts_lenient_forms(make_app, text, expect):
    app = make_app(portal=text)
    assert app._collect_config()["portalHost"] == expect
