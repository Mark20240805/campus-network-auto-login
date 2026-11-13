# -*- coding: utf-8 -*-
"""配置读写与日志轮转测试。

通过 monkeypatch 模块级 CONFIG_FILE / LOG_FILE 指向 tmp 目录，避免碰真实
config.json / login.log（里面有用户的真实账号）。DPAPI 加密被 monkeypatch
成确定值，测试聚焦「空值保留旧密文 / 明文迁移 / 原子写 / 轮转保留 3 份」。
"""
import json
import os

import pytest


@pytest.fixture
def tmp_paths(m, tmp_path, monkeypatch):
    """把 CONFIG_FILE / LOG_FILE 指到 tmp 目录，并打桩加密函数"""
    monkeypatch.setattr(m, "CONFIG_FILE", str(tmp_path / "config.json"))
    monkeypatch.setattr(m, "LOG_FILE", str(tmp_path / "login.log"))
    # DPAPI 打桩：返回确定的密文串，避免测试真的调 Windows DPAPI
    monkeypatch.setattr(m, "_encrypt_secret",
                        lambda plain: "ENC:" + plain)
    monkeypatch.setattr(m, "_decrypt_secret",
                        lambda token, label: token[4:] if token.startswith("ENC:") else "")
    return tmp_path


# ---------------------------------------------------------------------------
# 配置保存：空值必须保留旧密文（防止"切一下开关就把账号密码删掉"）
# ---------------------------------------------------------------------------

def test_save_config_preserves_old_secret_when_new_empty(m, tmp_paths):
    m.save_config({"userId": "2023001", "password": "secret123",
                   "autoRelogin": True})
    # 再存一次，密码/学号都为空 → 应保留旧密文
    m.save_config({"autoRelogin": False})
    with open(m.CONFIG_FILE, encoding="utf-8") as f:
        data = json.load(f)
    assert data.get("passwordEnc") == "ENC:secret123"
    assert data.get("userIdEnc") == "ENC:2023001"
    assert "password" not in data           # 不出现明文
    assert data.get("autoRelogin") is False


def test_save_config_encrypts_when_new_value(m, tmp_paths):
    m.save_config({"userId": "2023001", "password": "secret123"})
    with open(m.CONFIG_FILE, encoding="utf-8") as f:
        data = json.load(f)
    assert data["passwordEnc"] == "ENC:secret123"
    assert data["userIdEnc"] == "ENC:2023001"
    assert "password" not in data and "userId" not in data


def test_load_config_decrypts(m, tmp_paths):
    with open(m.CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump({"passwordEnc": "ENC:pw", "userIdEnc": "ENC:uid",
                   "autoRelogin": True}, f)
    cfg = m.load_config()
    assert cfg["password"] == "pw"
    assert cfg["userId"] == "uid"
    assert cfg["autoRelogin"] is True


def test_load_config_missing_file_returns_empty(m, tmp_paths):
    assert m.load_config() == {}


def test_save_config_atomic_no_tmp_left(m, tmp_paths):
    m.save_config({"userId": "2023001", "password": "pw"})
    # 成功后不应残留 .tmp
    assert not os.path.exists(m.CONFIG_FILE + ".tmp")


# ---------------------------------------------------------------------------
# 明文迁移
# ---------------------------------------------------------------------------

def test_migrate_config_plaintext_to_cipher(m, tmp_paths):
    with open(m.CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump({"userId": "2023001", "password": "legacy_plain"}, f)
    assert m.migrate_config() is True
    with open(m.CONFIG_FILE, encoding="utf-8") as f:
        data = json.load(f)
    assert data.get("passwordEnc") == "ENC:legacy_plain"
    assert data.get("userIdEnc") == "ENC:2023001"


def test_migrate_config_second_call_noop(m, tmp_paths):
    with open(m.CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump({"passwordEnc": "ENC:pw", "userIdEnc": "ENC:uid"}, f)
    assert m.migrate_config() is False      # 已是密文，不再迁移


# ---------------------------------------------------------------------------
# 日志轮转（保留 .1~.3，.1 最新）
# ---------------------------------------------------------------------------

def test_rotate_log_keeps_three_backups(m, tmp_paths):
    # 写一个超过上限的日志，触发轮转
    big = "x" * (m.LOG_MAX_BYTES + 10)
    with open(m.LOG_FILE, "w", encoding="utf-8") as f:
        f.write(big)
    m._rotate_log()
    assert os.path.exists(m.LOG_FILE + ".1")
    # 再触发一次，.1 是刚才那份、.2 是更早的
    with open(m.LOG_FILE, "w", encoding="utf-8") as f:
        f.write(big)
    m._rotate_log()
    assert os.path.exists(m.LOG_FILE + ".2")
    # 最多保留 LOG_BACKUPS 份（.4 不应出现）
    for _ in range(5):
        with open(m.LOG_FILE, "w", encoding="utf-8") as f:
            f.write(big)
        m._rotate_log()
    assert not os.path.exists(m.LOG_FILE + ".%d" % (m.LOG_BACKUPS + 1))


# ---------------------------------------------------------------------------
# 日志脱敏：scrub 幂等收敛
# ---------------------------------------------------------------------------

def test_scrub_log_is_idempotent(m, tmp_paths):
    sensitive = "sessionId=6787abcdef012345 userMac=aabbccddeeff\n"
    with open(m.LOG_FILE, "w", encoding="utf-8") as f:
        f.write(sensitive)
    m.scrub_log()
    with open(m.LOG_FILE, encoding="utf-8") as f:
        first = f.read()
    m.scrub_log()
    with open(m.LOG_FILE, encoding="utf-8") as f:
        second = f.read()
    assert first == second                  # 第二次重写不应再产生变化
    assert "sessionId=6787****" in first
    assert "userMac=aabbcc******" in first
