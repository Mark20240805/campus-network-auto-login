# -*- coding: utf-8 -*-
"""开机自启命令解析与体检测试。

只测纯逻辑：注册表读写、文件存在性、启动项管理器状态都在真机上，测试里用桩替换。
重点回归 1.0.3 的事故：打包版把自启命令写成了带 .py 的源码形式，
开机静默失败且没有任何提示。
"""
import os


def test_targets_quoted(m):
    value = '"E:\\App\\校园网自动认证系统.exe" --login'
    assert m.autostart_targets(value) == [r"E:\App\校园网自动认证系统.exe"]


def test_targets_source_mode(m):
    value = '"C:\\Py\\pythonw.exe" "C:\\App\\校园网自动登录.py" --login'
    assert m.autostart_targets(value) == [
        r"C:\Py\pythonw.exe", r"C:\App\校园网自动登录.py"]
    assert "--login" not in m.autostart_targets(value)


def test_targets_unquoted(m):
    assert m.autostart_targets(r"C:\App\a.exe --login") == [r"C:\App\a.exe"]


def test_targets_empty(m):
    assert m.autostart_targets("") == []
    assert m.autostart_targets(None) == []


def test_problems_ok_when_target_exists(m, monkeypatch):
    cmd = '"%s" --login' % os.path.abspath(__file__)
    monkeypatch.setattr(m, "autostart_cmd", lambda: cmd)
    monkeypatch.setattr(m, "autostart_policy_disabled", lambda: False)
    assert m.autostart_problems(cmd) == []


def test_problems_missing_file(m, monkeypatch):
    cmd = '"C:\\NoSuchDir\\nope.exe" --login'
    monkeypatch.setattr(m, "autostart_cmd", lambda: cmd)
    monkeypatch.setattr(m, "autostart_policy_disabled", lambda: False)
    problems = m.autostart_problems(cmd)
    assert any("不存在" in p for p in problems)


def test_problems_mismatch_and_disabled(m, monkeypatch):
    monkeypatch.setattr(m, "autostart_cmd", lambda: '"C:\\A\\a.exe" --login')
    monkeypatch.setattr(m, "autostart_policy_disabled", lambda: True)
    problems = m.autostart_problems('"C:\\B\\b.exe" --login')
    assert any("指向别处" in p for p in problems)
    assert any("禁用" in p for p in problems)


def test_problems_none_when_unset(m, monkeypatch):
    monkeypatch.setattr(m, "autostart_registered_value", lambda: None)
    assert m.autostart_problems() == ["注册表里没有自启项"]
