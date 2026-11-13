> [← 返回 README](../README.md) · 相关：[配置与隐私](config-and-privacy.md) · [排错](troubleshooting.md) · [构建](build.md)

# 认证协议与工作原理（实测验证）

本文记录本工具对锐捷 ePortal 5.x + CAS 统一身份认证协议的逆向与实测结论，以及常驻运行的请求特征。只想使用工具的话，读 [README](../README.md) 即可。

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
# 校园网自动登录.py 第 146 行附近
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
