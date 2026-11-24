> [← 返回 README](../README.md) · 相关：[配置与隐私](config-and-privacy.md) · [排错](troubleshooting.md) · [构建](build.md)

# 认证协议与工作原理（实测验证）

本文记录本工具对锐捷 ePortal 5.x + CAS 统一身份认证协议的逆向与实测结论，以及常驻运行的请求特征。只想使用工具的话，读 [README](../README.md) 即可；其他学校（同为这套认证体系）想适配的，直接看文末[「其他学校适配」](#其他学校适配)。

### 第 0 步：拿到「会话」（两种情况，必须先判断用哪条）

**A. 已认证过 / 网关认识本机** —— 直接调 `redirect.jsp` 就有会话：

```
GET /eportal/redirect.jsp
    → 302，Location 里带 sessionId / userIp / userMac / nasIp / customPageId
```

**B. 未认证且网关不认识本机** —— `redirect.jsp` 只会把你踢到占位地址：

```
GET /eportal/redirect.jsp
    → 302  http://123.123.123.123          ← 没有任何参数，走不通
```

这时必须走浏览器的真实路径：先访问任意普通网页，让网关拦截：

```
GET http://www.msftconnecttest.com/connecttest.txt
    → 200 + 一小段 JS：
      <script>top.self.location.href='https://sam.ahpu.edu.cn/eportal/index.jsp?
        wlanuserip=494fc4cd…&wlanacname=4338bd8d…&ssid=&nasip=3b5f2cf6…
        &snmpagentip=&mac=531af092…&t=wireless-v2&url=709db9dc…&apmac=
        &nasid=…&vid=…&port=…&nasportid=…'</script>

GET  <上面那段 /eportal/index.jsp 的完整地址>
    → 302  /portal/portal-main?sessionId=xxxx&userIp=…&userMac=…&nasIp=…
```

要点：

- 入口是 **`/eportal/index.jsp`**，不是 `redirect.jsp`
- 参数（`wlanuserip`、`nasip`、`mac`、`nasid`…）是 NAS 加密下发的十六进制串，**不需要解密，原样转发**
- 这些参数无法自己伪造：给 `redirect.jsp` 手动加 `userip/usermac` 会被忽略（实测），它只按源 IP 查

### DNS 被改手动时（1.1.0 起）

认证前网关只放行校园网自己的 DNS。加速器、去广告类软件把网卡 DNS 改成手动后，
所有域名都解析不了：浏览器打不开认证页，上面第 0 步的域名探测也会失败。此时有两条路：

- 探测列表里放了 **IP 直连地址**（如 `http://1.1.1.1/`），不走 DNS，仍会被网关拦截并
  302 到门户入口，可以继续按上面 B 路径拿会话
- 主流程发往门户域名的请求在解析失败时改连 `dns_cache.json` 里上次成功的 IP
  （Host 头、TLS SNI、证书校验仍按原域名），细节见[配置与隐私](config-and-privacy.md)

有个原理上的盲区：**解析"成功"但结果被劫持**不会触发回退（流程认为解析成功）。
此时 TLS 证书校验会拒绝连接并清除该域名的缓存条目，保证缓存不帮倒忙；
根治要靠托盘右键「修复 DNS」把网卡 DNS 恢复成自动获取。

### 第 1~3 步：统一身份认证

```
GET  /cas-sso/login?flowSessionId=…&customPageId=…&nasIp=…&userIp=…&userMac=…
        → 下发 SESSION cookie；页面含两个隐藏字段：
          <p id="login-croypto">      本次 AES 密钥（每次刷新随机）
          <p id="login-page-flowkey"> execution
POST /cas-sso/login?<同上>   (application/x-www-form-urlencoded)
        username / type=UsernamePassword / _eventId=submit / geolocation=
        execution=<flowkey> / captcha_code=
        croypto=<密钥>
        password        = base64(AES-128-ECB-PKCS7(明文, 密钥))
        captcha_payload = base64(AES-128-ECB-PKCS7("{}", 密钥))
        → 302，Location 里带 ticket=ST-…（失败则回渲染登录页给 login-error-msg）
```

### 第 4~6 步：票据落地与授权

```
GET  <ticket 跳转>                        建立 ePortal 会话
判定：CAS 通过后先查在线状态（校园网有无感知认证）
      ├─ 已在线  → 认证完成（最常见）
      └─ 仍离线  → 继续下面两步
POST /eportal/network/serviceSelection {sessionId}          → 运营商列表
POST /eportal/network/serviceLogin     {sessionId, service} → authResult=success
POST /eportal/network/userOnline       {sessionId}          → online=true
```

要点：

- 密码不是明文传输，用登录页当次下发的 `croypto` 做 **AES-128-ECB + PKCS7** 加密
- `execution` 与 `SESSION` cookie 绑定，必须先 GET 登录页再 POST
- `serviceLogin` 的服务端 DTO 只有两个字段：`sessionId`、`service`
- **流程节点决定 `serviceLogin` 能不能调**（`POST /eportal/workFlow/getCurrentNode` 可查）：
  - 未认证时是 `serviceSelection` → 可以正常授权上线
  - 已在线时是 `finish` → 服务端返回 `workFlowNode is empty`，属正常现象，不是错误
- **未认证时 `redirect.jsp` 不可用**（只会 302 到 `123.123.123.123`），必须先走
  「普通网页 → 网关拦截 JS → `/eportal/index.jsp`」这条路拿会话
- **认证后不要立刻重新访问 `redirect.jsp`**：它可能新建流程会话，把刚认证好的流程顶掉
- 多网卡机器（有线 + WiFi + Hyper-V/VMware 虚拟网卡）默认路由可能不走校园网，
  程序会枚举本机 IPv4、逐张绑定网卡试到能取到 `sessionId` 为止
- 刚插网线/连 WiFi 有几秒到几十秒的网关初始化窗口，此时 `redirect.jsp` 会返回 302 但不带
  `sessionId`，程序会自动轮询等待（最长约 40 秒）
- 点「下线」后 `userOnline` 会立刻变 false，但网关真正释放会话还要 5~6 秒，
  排查时请等 10~15 秒再抓包（`autodiag` 已内置这段稳定期）

### 常态请求特征（每 60 秒的轮询）

挂机运行时程序**每 60 秒**做一次在线检测，每次 tick 向
`sam.ahpu.edu.cn/eportal/` 发 **2~3 个请求**：

```
GET  /eportal/redirect.jsp                     取会话（302 Location 里的 sessionId）
POST /eportal/network/userOnline               查是否在线
GET  /eportal/adaptor/getOnlineUserInfo?…      仅已在线时补查用户名
```

- 属于稳定的小流量（约 2~3 请求/分钟），网关完全无感，但**可被识别为自动客户端**；
  离线退避期间请求更少（最长 15 分钟才一次）
- 间隔由主程序顶部的常量控制，**源码用户改一行即可调整**：

```python
# 校园网自动登录.py 第 187 行附近
MONITOR_INTERVAL = 60000  # 断线检测间隔（毫秒）→ 改成 300000 就是每 5 分钟一次
```

- 用打包版 exe 的同学改不了这个常量（值已编译进二进制），想自定义请参照
  [自行打包 exe](build.md#自行打包-exe) 一节从源码重新编译

## 启动耗时（实测）

2026-10 优化后，从进程启动到登录完成（网络已就绪）：

| 版本 | 优化前 | 优化后 |
|---|---|---|
| 源码版 `--login` | 3.84 s | **0.95 s** |
| 打包 exe `--login` | 5.67 s | **1.03 s** |
| 安装版 exe（装到 `%LOCALAPPDATA%`） | 5.7 s | **0.87 s** |

协议本身很快（6 个请求合计 0.35 秒），耗时主要在等待，所以优化都围绕等待：

| 改动 | 效果 |
|---|---|
| 删掉 `_initial_login` 里的固定 `time.sleep(3)` | 直接省 3 秒 |
| 等待网关改为「多次短超时」：单次 2 秒、间隔 300ms、总上限 45 秒 | 失败一次只花 2 秒，不再一卡 20 秒 |
| 就绪判据改为「普通网页被拦截 **或** 能取到 sessionId」 | 未认证状态也能立刻判定就绪 |
| 轮询 1000ms → 300ms | 最多省 0.7 秒 |
| `pystray`/`Pillow` 延迟导入、图标延迟生成、托盘初始化后置 | exe 启动省约 1.3 秒 |

> 冷知识：原来 `wait_network(portal, 45)` 的 45 是**循环次数**，而单次失败请求会阻塞 20 秒，
> 最坏能等 15 分钟。现在参数改成了**墙钟秒数**，实测 `timeout=6` 在不可达网关上 8.35 秒返回。

可调参数在文件顶部：

```python
PROBE_TIMEOUT = 2.0    # 单次探测超时（秒）
PROBE_INTERVAL = 0.3   # 两次探测间隔（秒）
READY_TIMEOUT = 45     # 等待网卡/网关就绪的总时长上限（秒）
```

## 其他学校适配

本工具按安徽工程大学的部署逆向与实测，但认证流程本身是锐捷 ePortal 5.x + CAS 的
标准流程，代码里没有只有本校才能跑通的逻辑。同为这套认证体系的学校，按改动量
从小到大试：

### 1. 只改配置（多数学校到这一步就够了）

- 认证地址换成自己学校的门户（只填「协议 + 主机 + 端口」）。ePortal 5.x + CAS
  集成部署里 `/cas-sso/login`、`/eportal/*` 都挂在门户同一主机下，程序的重定向
  放行与 Cookie 共享按「同主机 / 同主域」工作，换域名即可用
- 出口名称按自己学校门户返回的选项填；填了列表外的出口程序会明确报错而不是静默换

### 2. 改一行源码（仅当 CAS 在独立子域）

如果你们学校 CAS 与门户不同主机（如 `cas.xxx.edu.cn` 与 `portal.xxx.edu.cn`），
重定向白名单会拦下 portal → CAS 的跳转：把 `校园网自动登录.py` 第 659 行
`_redirect_allowed()` 里写死的 `ahpu.edu.cn`（第 671 行一行出现两处）换成自己学校的注册域。
（第 656 行的 `_ALLOWED_REDIRECT_SUFFIXES` 是遗留常量，未被引用，顺手可删可改。）

### 3. 按需修改（不改也能登录，只是慢或提示不准）

- **网段前缀**：`has_campus_ip()`（第 1114 行）只认 `10.` 开头的校园网地址。
  你们学校若用 `172.16.` 等其他内网段，登录前会白等约 45 秒再继续——把第 1116 行的
  `"10."` 换成自己的网段前缀即可（第 1236 行日志提示里还有一处，仅影响提示文案）。
  另外「修复 DNS」定位网卡用的 `find_campus_adapters()`（第 1677 行）也按 `10.` 找，
  换学校时要一起改，否则托盘「修复 DNS」找不到网卡
- **备用入口**：`get_redirect_info()` 第 1089–1090 行写有本校的 `sam.ahpu.edu.cn` 与
  网关 IP `10.11.12.13`，只在主入口取不到会话后的重试里用到；换学校后这两条会
  请求失败、多花一两秒，不删也能登录，介意可改成自己学校的地址或删掉
- **拦截识别**：`trigger_captive()` 第 1130 行按 `"portal" / "ahpu" / "10.11.12.13"`
  判断「网关已拦截」；ePortal 部署的跳转地址一般含 `eportal`，通常命中 `portal`，
  一般无需改
- **DNS 回退白名单**：`_dns_fallback_allowed()`（第 822 行）只放行门户 host 与
  `*.ahpu.edu.cn`（第 828–829 行），换学校后需改成自己学校的注册域，否则域名解析
  失败时不会触发缓存 IP 回退

### 适配方法

对照本文逐节抓包：哪一步的响应与文中对不上，差异点就在那——常见差异是 CAS 登录页
字段 ID、验证码策略、运营商服务名。改完源码跑 `python -m pytest -q`，重新打包见
[自行打包 exe](build.md#自行打包-exe)。
