# -*- coding: utf-8 -*-
"""pytest 公共 fixture：在无 Tk / 无三方依赖的环境里安全地 import 主模块。

主模块顶层无条件 `import tkinter as tk`，且 `CampusApp(tk.Tk)` 在类定义时就要
引用 `tk.Tk`。内置 Python（如 3.13 精简版）没有 tkinter，所以这里在 import
主模块之前先往 `sys.modules` 里注入桩模块，让纯函数（协议层、配置 IO、日志、
脱敏、退避、Cookie 隔离）能被测试，而不真的拉起 GUI 或触发网络请求。

注意：这只影响「能否 import」，测试里从不实例化 CampusApp，也不发起任何
真实登录请求（那会真的把账号上线/下线）。
"""
import sys
import types

import pytest


def _install_tk_stub():
    tk = types.ModuleType("tkinter")
    # CampusApp(tk.Tk) 需要 Tk 是可调用类；StringVar/BooleanVar 运行时才用，
    # 桩成返回 None 的工厂即可。
    tk.Tk = object
    tk.Toplevel = object
    tk.Frame = object
    tk.Label = object
    tk.StringVar = lambda *a, **k: None
    tk.BooleanVar = lambda *a, **k: None

    ttk = types.ModuleType("tkinter.ttk")
    ttk.Frame = object
    ttk.Label = object
    ttk.Entry = object
    ttk.Button = object
    ttk.Combobox = object
    ttk.Checkbutton = object

    msg = types.ModuleType("tkinter.messagebox")
    msg.showerror = lambda *a, **k: None
    msg.showinfo = lambda *a, **k: None
    msg.showwarning = lambda *a, **k: None
    msg.askyesno = lambda *a, **k: False

    scrolledtext = types.ModuleType("tkinter.scrolledtext")
    scrolledtext.ScrolledText = object

    for name, mod in {
        "tkinter": tk,
        "tkinter.ttk": ttk,
        "tkinter.messagebox": msg,
        "tkinter.scrolledtext": scrolledtext,
    }.items():
        sys.modules[name] = mod


# 只有在 tkinter 缺失时才注入桩（如精简版 Python 3.13）；系统 Python 通常自带
# tkinter，此时保留真模块即可——测试从不实例化 CampusApp，纯函数不受影响。
try:
    import tkinter  # noqa: F401
except ImportError:
    _install_tk_stub()


@pytest.fixture(scope="session")
def m():
    """import 主模块一次，供所有测试复用（import 只建 SSL context、不启 GUI）。"""
    import importlib.util
    import os

    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(here, "校园网自动登录.py")
    spec = importlib.util.spec_from_file_location("campusnet", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["campusnet"] = mod
    spec.loader.exec_module(mod)
    return mod
