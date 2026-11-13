# -*- coding: utf-8 -*-
"""
安徽工程大学 校园网认证 —— 命令行调试工具
==========================================
协议实现全部在主程序 校园网自动登录.py 里，本脚本只是它的命令行外壳，
用于在主程序之外单独排查认证链路。

用法：
  python ahpu_portal_probe.py status                    # 在线状态
  python ahpu_portal_probe.py services                  # 可选运营商出口
  python ahpu_portal_probe.py info                      # 在线详情（账号/IP/MAC/出口/出口网卡）
  python ahpu_portal_probe.py castest 学号 密码          # 只验证统一身份认证（不影响当前在线状态）
  python ahpu_portal_probe.py login 学号 密码 [出口]      # 完整登录
  python ahpu_portal_probe.py login 学号 密码 [出口] force  # 即使已在线也强制走完整流程

依赖：cryptography（pip install cryptography）

注意：castest / login 需要把密码当作命令行参数传入，会留在 shell 历史里。
     日常排错请用 relogin（读 config.json 里的 DPAPI 密文），别把密码写进脚本或历史。

许可：GPL-3.0-or-later（全文见 LICENSE 文件）。Copyright (C) 2026 Mark20240805。
"""

import importlib.util
import json
import os
import sys

_MAIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "校园网自动登录.py")
_spec = importlib.util.spec_from_file_location("ahpu_main", _MAIN)
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)


def print_json(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def cmd_diag(portal, path="diag.txt"):
    """把"未认证状态下"网关与门户的真实响应全部落盘，便于定位问题

    请在断网/下线后运行：python ahpu_portal_probe.py diag
    运行完用 python ahpu_portal_probe.py relogin 恢复上网。
    """
    import re
    import time
    import urllib.parse

    out = []
    raw_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "diag_raw")
    try:
        os.makedirs(raw_dir, exist_ok=True)
    except Exception:
        raw_dir = None

    def w(line=""):
        out.append(str(line))
        print(line)
        # 边跑边写，万一中途异常也不至于什么都没留下
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(out))
        except Exception:
            pass

    w("=" * 78)
    w("安徽工程大学 认证链路诊断  %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    w("=" * 78)
    w("本机 IPv4: %s" % M.local_ipv4_list())
    w("校园网地址: %s" % M.has_campus_ip())
    w("")

    sess = M.new_session(portal)
    sess.timeout = 6                  # 离线时别卡太久
    w("选用网卡: %s" % (sess.local_ip or "系统默认路由"))
    w("")

    def dump(title, url, follow=False):
        st, hd, body = sess.request(url, follow=follow)
        w("-" * 78)
        w("[%s] %s" % (title, url))
        w("  status : %s" % st)
        for k in ("Location", "location", "Content-Type", "Set-Cookie"):
            if hd.get(k):
                w("  %-13s: %s" % (k, str(hd[k])[:300]))
        w("  body   : %d 字节" % len(body or ""))
        if raw_dir:
            try:
                with open(os.path.join(raw_dir, re.sub(r"[^\w.-]", "_", title) + ".html"),
                          "w", encoding="utf-8") as f:
                    f.write(body or "")
            except Exception:
                pass
        w("  %s" % (body or "")[:1800].replace("\n", " "))
        for m in re.finditer(r'(?:sessionId|userIp|userMac|nasIp|ticket|flowParams)=([^&;"\'\s]{1,60})',
                             body or ""):
            w("  参数: %s=%s" % (m.group(1), m.group(2)))
        return st, hd, body

    w("### 1. 门户入口")
    dump("redirect.jsp_域名", portal + "/eportal/redirect.jsp")
    dump("redirect.jsp_网关IP", "http://10.11.12.13/eportal/redirect.jsp")
    dump("portal-main", portal + "/portal/portal-main")
    dump("authenticate_页", portal + "/portal/entry/pc/authenticate")

    w("\n### 2. 普通网页拦截（浏览器路径）")
    for i, u in enumerate(M.CAPTIVE_PROBES, 1):
        w("")
        w("[探测 %d] %s" % (i, u))
        hops = sess.trace(u)
        for j, (hu, hs, hl) in enumerate(hops):
            w("  跳转%d: %s → %s  %s" % (j, hu[:70], hs, str(hl)[:160]))
        st, hd, body = sess.request(u, follow=False)
        w("  最终 status: %s | body: %d 字节" % (st, len(body or "")))
        if raw_dir and body:
            try:
                with open(os.path.join(raw_dir, "probe%d.html" % i), "w",
                          encoding="utf-8") as f:
                    f.write(body)
            except Exception:
                pass
        if body and len(body) > 100:
            w("  响应体 前 1500 字:")
            w("  " + body[:1500].replace("\n", " "))
            for m in re.finditer(r'(?:sessionId|flowParams|ticket)=([^&;"\'\s]{1,60})', body):
                w("  参数: %s=%s" % (m.group(1), m.group(2)))

    w("\n### 3. 会话与状态接口")
    info = M.get_redirect_info(sess, retries=1)
    w("get_redirect_info: %s" % json.dumps(info, ensure_ascii=False)[:400])
    sid = info.get("sessionId")
    if sid:
        for name, fn in (("userOnline", lambda: sess.post_json(
                "/eportal/network/userOnline", {"sessionId": sid})),
                         ("getOnlineUserInfo", lambda: sess.get(
                             "/eportal/adaptor/getOnlineUserInfo?sessionId=" + sid))):
            st, hd, body = fn()
            w("  %s → %s" % (name, (body or "")[:400].replace("\n", " ")))

    w("\n### 4. 结论")
    if not sid:
        w("  未取到 sessionId —— 属于『网关未识别本机』状态，重点看第 2 节响应体")
    else:
        w("  已取到 sessionId=%s" % sid)
    if raw_dir:
        w("  原始响应体已存到 %s\\" % raw_dir)
    w("")
    w("诊断结束：%s" % path)
    w("恢复上网：python ahpu_portal_probe.py relogin")
    return out


def cmd_relogin(portal):
    """用已保存的配置重新登录（诊断后恢复上网用）"""
    cfg = M.load_config()
    if not (cfg.get("userId") and cfg.get("password")):
        print("[FAIL] 没有可用配置，请先用图形界面保存一次")
        return
    ok, msg, kind = M.do_login(cfg, progress=lambda s: print("   ", s), force=True)
    print(("[OK] " if ok else "[FAIL] ") + msg + ("" if ok else "  [%s]" % kind))


def _online_quick(sess):
    """轻量在线判断（2 次请求，用于盯梢轮询）"""
    sid = M.get_redirect_info(sess, retries=1).get("sessionId")
    if not sid:
        return False
    st, hd, txt = sess.post_json("/eportal/network/userOnline", {"sessionId": sid})
    return bool((M._loads(txt).get("data") or {}).get("online"))


def _wait_settled_offline(portal, settle=10, tries=6):
    """检测到离线后再等一段稳定期，确认网关真的摘掉了会话

    点「下线」后 userOnline 会立刻变 false，但网关释放会话还要 5~6 秒，
    这期间抓包抓到的是过渡态，没有参考价值。
    """
    import time
    for n in range(tries):
        print(">> 已离线，等待网关稳定 %d 秒后再抓包（%d/%d）..." % (settle, n + 1, tries))
        for s in range(settle):
            time.sleep(1)
            if (s + 1) % 5 == 0:
                print("   ...稳定等待 %d/%d 秒" % (s + 1, settle))
        sess = M.new_session(portal)
        sess.timeout = 5
        try:
            if not _online_quick(sess):
                print(">> 已确认稳定处于未认证状态")
                return True
            print(">> 又变回在线了（可能触发无感知重连），继续等待...")
        except Exception:
            pass
    return False


def cmd_autodiag(portal, wait=180, settle=10):
    """盯梢 → 等稳定 → 抓包 → 自动恢复上网（全程本地，不需要外网，也不需要我在线）

    用法：先运行本命令，然后去浏览器点「下线」，之后什么都不用做。
    """
    import time
    print("=" * 78)
    print("自动诊断：等待你进入未认证状态")
    print("请现在去浏览器打开 https://sam.ahpu.edu.cn/eportal/redirect.jsp 并点「下线」")
    print("最多等待 %d 秒；检测到离线后再等 %d 秒让网关稳定，然后抓包并自动恢复上网"
          % (wait, settle))
    print("=" * 78)

    went_offline = False
    for i in range(wait):
        sess = M.new_session(portal)
        sess.timeout = 5
        try:
            if not _online_quick(sess):
                print(">> 检测到已离线（等待 %d 秒）" % i)
                went_offline = True
                break
        except Exception:
            pass
        if i % 10 == 0:
            print("   第 %d 秒：仍在线，继续等待..." % i)
        time.sleep(1)

    if not went_offline:
        print(">> %d 秒内没检测到离线，仍然执行一次诊断（结果可能是在线基线）" % wait)
    else:
        _wait_settled_offline(portal, settle)

    cmd_diag(portal)

    print("\n" + "=" * 78)
    print("诊断完成，开始恢复上网...")
    print("=" * 78)
    cmd_relogin(portal)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    portal = M.DEFAULT_PORTAL
    try:
        cfg = M.load_config()
        portal = M.normalize_portal(cfg.get("portalHost")) or portal
    except Exception:
        pass

    if cmd == "status":
        sess = M.new_session(portal)
        online, uid = M.check_online(sess)
        print_json({"online": online, "userName": uid, "localIp": sess.local_ip})
    elif cmd == "services":
        sess = M.new_session(portal)
        sid = M.get_session_id(sess)
        print_json({"sessionId": sid, "localIp": sess.local_ip,
                    "services": M.get_services(sess, sid) if sid else []})
    elif cmd == "info":
        sess = M.new_session(portal)
        sid = M.get_session_id(sess)
        st, hd, text = sess.get("/eportal/adaptor/getOnlineUserInfo?sessionId=" + (sid or ""))
        print_json(M._loads(text))
    elif cmd == "diag":
        cmd_diag(portal)
    elif cmd == "autodiag":
        wait = int(sys.argv[2]) if len(sys.argv) > 2 else 180
        settle = int(sys.argv[3]) if len(sys.argv) > 3 else 10
        cmd_autodiag(portal, wait, settle)
    elif cmd == "relogin":
        cmd_relogin(portal)
    elif cmd == "castest":
        if len(sys.argv) < 4:
            print(__doc__)
            return
        sess = M.new_session(portal)
        ok, detail, kind = M.cas_login(sess, sys.argv[2], sys.argv[3])
        print(("[OK] " if ok else "[FAIL] ") + str(detail) +
              ("" if ok else "  [%s]" % kind))
    elif cmd == "login":
        if len(sys.argv) < 4:
            print(__doc__)
            return
        user, pwd = sys.argv[2], sys.argv[3]
        svc = sys.argv[4] if len(sys.argv) > 4 and sys.argv[4] != "force" else None
        force = "force" in sys.argv[1:]
        ok, msg, kind = M.do_login(
            {"portalHost": portal, "userId": user, "password": pwd, "service": svc or ""},
            progress=lambda s: print("   ", s), force=force)
        print(("[OK] " if ok else "[FAIL] ") + msg + ("" if ok else "  [%s]" % kind))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
