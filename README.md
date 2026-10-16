# 图书馆座位预约助手

按实测协议直接构造 HTTP 请求，定时向图书馆「IC 空间预约」系统提交预约。
**运行期零第三方依赖**（纯 Python 标准库 `http.server` + `urllib`），不用装 `requests`/`flask`。

也可以打包成**单个 exe** 发给别人，双击即用，对方无需安装 Python。

```
Reserve/
├─ run.py                 启动入口（自动选端口、自动开浏览器）
├─ start.bat              双击启动（Windows）
├─ Reserve.spec           PyInstaller 打包配置（单文件）
├─ qut.jpg                图标源图（打包时转成 app.ico）
├─ config.example.json    配置样例，复制成 config.json
├─ app/
│  ├─ config.py           配置读写、脱敏、默认值
│  ├─ httpclient.py       urllib 客户端（gzip/deflate、Cookie、计时）
│  ├─ adapters.py         协议适配层（IC 系 / 通用重放）
│  ├─ scheduler.py        定时 + 重试 + 成功即停
│  ├─ crypto.py           RSA PKCS#1 v1.5（登录密码加密）
│  ├─ aes.py              AES-128-CBC（座位与口令加密）
│  └─ server.py           Web 服务 + JSON API
├─ web/                   前端界面（原生 HTML/CSS/JS）
├─ tools/
│  ├─ build_exe.py        打包单文件 exe（自动脱敏 + 隐私验证）
│  ├─ make_icon.py        图片 → 多尺寸 ico
│  └─ verify_exe.py       打包产物验证（18 项）
└─ logs/                  运行日志
```

---

## 1. 启动

### 方式一：直接跑源码

```powershell
cd E:\Project\Reserve
Copy-Item config.example.json config.json
python run.py
```

浏览器自动打开 `http://127.0.0.1:8765/`（只监听本机，不对外）。
端口被占用会自动顺延（最多试 50 个），也可指定：`$env:RESERVE_PORT=9000; python run.py`。
不自动开浏览器：`$env:RESERVE_NO_BROWSER=1`。
指定配置文件路径（测试/多实例）：`$env:RESERVE_CONFIG=D:\tmp\test.json`。

### 方式二：双击 exe（推荐分发）

```
dist\Reserve.exe        ← 就这一个文件，拷走即可用
```

打包：

```powershell
python tools\build_exe.py --embed-ip http://你的服务器地址
```

- 产物是**单文件**，不会出现「只拷了 exe 没拷 `_internal` 导致闪退」
- 打包在临时副本里进行，**项目目录不会被修改**
- 默认脱敏（学号 / 密码 / accNo / 座位名都不会进产物）
- `--embed-ip` 把服务器地址固化进去，用户只需填学号密码
- 不加 `--embed-ip` 则地址留空，由用户在界面上填

验证产物：

```powershell
python tools\verify_exe.py --password 你的密码
```

---

## 2. 界面说明

界面从上到下四块：

| 区域 | 内容 |
|---|---|
| **① 账号** | 服务器地址、学号、密码，以及「登录检测」按钮 |
| **② 预约参数** | 自习室下拉、日期下拉、座位表格（点选）、预约时段 |
| **③ 定时预约** | 定时日期、定时时间、「开始定时 / 立即执行 / 预约验证 / 停止」 |
| **④ 运行日志** | 实时日志，含每次请求的完整 payload 与响应 |

### 2.1 预约时段支持自定义

下拉选项：**上午（08:00–12:00）/ 下午（13:30–18:00）/ 晚上（19:00–22:00）/ 自定义时间段…**

选「自定义时间段」后填**开始**和**结束**时间，界面实时显示时长并提示规则：

```
时段 09:30 – 11:30，时长 2 小时（120 分钟）
图书馆规则：时长为 60 ~ 300 分钟，且须在开放时段 07:00–22:00 内，否则服务器会拒绝
```

时长违反规则会在**本地**直接拦下（这是确定性错误，重试无意义）：

```
预约时长 30 分钟 < 规则要求最短 60 分钟，请改时段。
```

### 2.2 定时时间支持自定义

下拉预设 `08:00 / 12:00 / 13:00 / 13:30 / 18:00 / 19:00 / 21:00 / 22:00`，
再加一项 **「自定义…」**，可以手填任意时刻，支持多种写法：

| 输入 | 解析 |
|---|---|
| `13:01` | `13:01:00` |
| `13:01:30` | `13:01:30`（支持秒级） |
| `1301` | `13:01:00` |
| `9:5` | `09:05:00` |
| `13：01`（中文冒号） | `13:01:00` |

输入过程中**不会**改写你正在打的字（只在失去焦点时补全成 `HH:MM:SS`）。

---

## 3. 提前预约时间范围（重要）

图书馆有条硬性规则，写在 `resvRule.earliestResvTime` 里（实测 **360 分钟**）：

> **只能预约「当前时间 + 6 小时」之后开始的时段。**

不满足时服务器返回：

```json
{"code":1,"message":"不在提前预约时间范围内","data":null}
```

对照表：

| 你要约 | 什么时候才能约 |
|---|---|
| 今天 19:00 | 今天 **13:00** 之后 |
| 今天 22:00 | 今天 **16:00** 之后 |
| 明天任意时段 | 随时可约（距现在远超 6 小时） |
| 今天已经过去的时段 | 永远不行 |

**本程序不对此做本地拦截** —— 本机时钟抖动、分钟取整都可能造成误判，
把本来能成功的预约拦掉。改为直接发给服务器：若被回
「不在提前预约时间范围内」，会归类为**可重试**，按间隔持续发送，
**进入可约范围后立刻成功**。

所以正确用法是**卡着时间点定时**，例如：

> 定时「自定义 `12:59:50`」→ 预约「自定义 `19:00 – 22:00`」→
> 到点开始发送，前几次被拒 → 每 2 秒重试 → 13:00 一过立刻成功。

---

## 4. 重试策略

下单失败会**分类处理**，不是无脑重试：

| 服务器返回 | 分类 | 行为 |
|---|---|---|
| `不在提前预约时间范围内` | 可重试 | 继续发送（等窗口开放） |
| `该时段已被预约` / `已被占用` | 可重试 | 继续发送（可能有人取消） |
| `请求频繁,请稍后重试` | 可重试 | 继续发送 |
| `请求参数错误`（code 100） | 不可重试 | **立即停止** |
| `用户未登录`（code 300） | 不可重试 | **立即停止** |
| `座位不存在` / `权限不足` | 不可重试 | **立即停止** |
| HTTP 404 / 网络错误 | 不可重试 | **立即停止** |
| 时长违反 `minResvTime` / `maxResvTime` | 不可重试 | **立即停止**（本地拦下） |

默认参数（改 `config.json` 的 `schedule`）：

```json
{
  "retry": true,
  "retry_max": null,
  "retry_interval_ms": 2000,
  "retry_timeout_s": 1800
}
```

`retry_max: null` 表示不限次数，只受 `retry_timeout_s`（30 分钟）约束。

**预约成功后立即停止**，不再发送任何请求。

---

## 5. 打包与隐私

`tools/build_exe.py` 的隐私处理：

| 内容 | 处理 |
|---|---|
| 学号 / 密码 / accNo | **从不进入源码**，只在运行期由用户输入 |
| 真实服务器地址 | 默认脱敏成 `10.0.0.10`；用 `--embed-ip` 可有意固化 |
| 真实座位名（如 `4F-A106`） | 脱敏成 `A-001` |
| `config.json` / `logs/` / `test.txt` / `user.txt` | 不复制进构建目录 |

产物验证用 PyInstaller 的归档读取器解出**真实模块常量**再比对
（直接对 exe 做字节搜索会因 zlib 压缩而「假通过」）：

```
✅ 已验证：8/8 个 app 模块、2467 个字符串常量
   检查了 9 类隐私特征，全部未命中
```

图标：把 `qut.jpg`（或任意图片，非正方形会自动居中裁剪）转成
含 16/24/32/48/64/128/256 多尺寸的 `app.ico`：

```powershell
python tools\make_icon.py --src 你的图.png --out app.ico
```

---

## 6. 配置项与界面字段对照

配置文件 `config.json`，界面上每个输入框都直接对应它，两边等价。

## 6.1 字段名配置（一般不用动）

配置文件 `config.json`，界面上的每个输入框都直接对应它，两边等价。

| 你的要求 | 配置键 | 说明 |
|---|---|---|
| 账号密码 | `account.username` / `account.password` | 仅存本地 `config.json`，不会外发到别处 |
| 预约座位 | `target.seat_id` | **座位 ID**，见第 4 节 |
| 场馆 | `target.space_id` | `spaceId`，一般是自习室/楼层 ID |
| 预约起始时间 | `target.start_time` / `target.end_time` | `HH:MM`，如 `08:00` / `12:00` |
| 预约日期 | `target.reserve_date` | `YYYY-MM-DD`，留空=当天，也支持 `今天/明天` |
| 时段编号 | `target.segment_ids` / `target.segments` | IC 系接口常吃这个，见第 5 节 |
| 定时发送时间 | `schedule.fire_time` / `schedule.mode` | 见第 3 节 |
| 会话凭证 | `session.cookie` / `session.token` | 可手填抓包值，也可留空自动登录 |
| 请求头覆盖 | `request.extra_headers` | 例如你要加 `Referer`、`Origin` |
| 重放模板 | `replay.*` | 最稳的路子，见第 6 节 |

### 字段名对不上？改 `config.json` 的 `fields`，不用改代码

不同学校部署的 IC 系版本字段名会变。`fields` 里就是「程序内部语义 → 实际发出的键名」的映射：

```json
"fields": {
  "login_user": "username",     // 有的版本是 logName
  "login_pass": "password",     // 有的版本是 pwd
  "seat_id":    "seatId",       // 有的版本是 id / seatID
  "space_id":   "spaceId",
  "date":       "date",         // 有的版本是 reserveDate / bookingDate
  "start":      "startTime",
  "end":        "endTime",
  "segment_ids":"segmentIds",   // 有的版本是 segments / segmentId
  "user_id":    "userId",       // 有的版本是 readerId / cardNo
  "lib_id":     "libId"
}
```

`paths` 同理，是各个接口的路径映射（`login` / `seat_free` / `booking` …）。
抓包看到的键名跟这里不一样，对照着改就行。

### 定时模式

| mode | 行为 |
|---|---|
| `at_time` | 到点发一次。`fire_time` 支持 `07:59:58`、`2026-10-16 07:59:58` |
| `countdown` | `countdown_seconds` 秒后开抢（调试用） |
| `daily` | 每天该时刻都跑，失败自动等第二天 |

另外三个关键项：

- `advance_ms`：提前量（毫秒）。服务器时间比本机快就填正数，比如服务器快 800ms 就填 `800`。
- `retry` + `retry_interval_ms` + `retry_timeout_s`：失败后按间隔高频重发，直到成功或超时。
- `prewarm` + `prewarm_advance_s`：开抢前 60 秒先登录/刷 token，避免抢单那一刻把时间耗在登录上。

对齐精度：先粗睡到目标时刻前 50ms，再自旋对齐到毫秒，避免 `time.sleep` 的调度抖动。

---

## 2.5 登录返回 `code=100 请求参数错误` 怎么修

这个错误**不是密码错**，是接口不认我们发的字段名/编码。按下面顺序来：

### 第 1 招：界面点「穷举登录字段名」

界面 ① 区那个黄色的 **「穷举登录字段名」** 按钮。
它先用假账号 `__probe__` 探测哪条登录路径是活的，然后分层穷举 `账号字段名 × 密码字段名 × 编码方式 × 包装层`：

- `账号字段名`：`logName` `username` `cardNo` `readerNo` `userNo` `studentNo` …
- `密码字段名`：`pwd` `password` `passwd` `userPwd` `loginPwd` …
- `编码方式`：`json` / `form-urlencoded`
- `包装层`：平铺 / `{"param":{...}}` / `{"data":{...}}`

**判定原理**：字段名不对时，服务端反序列化拿不到账号，仍然回 `code=100`；
字段名对了，就会走到业务校验，回「密码错误 / 用户不存在」之类的**其他** code。
所以「**code 不再是 100**」就是命中信号。

全程用假账号，**不会污染你真实账号的失败计数、不会触发锁定**。

命中后界面会自动把字段名填进下面的输入框，你点「保存配置」→「测试登录」即可。

命令行等价版本（结果也写进 `logs/probe-fields.json`）：

```powershell
python tools\probe_fields.py
python tools\probe_fields.py --base http://10.20.15.27
```

### 第 2 招：手动改「登录请求形态」

如果穷举没命中，说明问题不在字段名。在界面 ① 区下面手动调：

| 配置键 | 可选值 | 什么时候改 |
|---|---|---|
| `login.encoding` | `json` / `form` / `query` | 登录接口吃表单不吃 JSON，或参数要拼在 URL 上 |
| `login.wrap` | 空 / `param` / `data` / `loginDto` | 请求体外面套了一层 |
| `login.pwd_mode` | `plain` / `md5` / `sha1` / `sha256` / `base64` / `rsa_hex` / `rsa_b64` / `rsa_upper` | 前端 JS 里密码先做过哈希或 RSA 加密再发 |
| `login.rsa_public_key` | base64 或 PEM 公钥 | `pwd_mode` 选 `rsa_*` 时必填，从 JS 的 `setPublicKey` 抓 |
| `login.body` | 请求体模板 | ⭐ **最稳**：把抓到的 Payload 原文贴进去，直接照发 |
| `login.extra_fields` | 任意 JSON | 必须带固定字段，如 `{"loginType":1}` |
| `login.origin` / `login.referer` | URL | 后端校验来源头（抓包里出现过 `Origin` 就要带） |

`login.body` 模板的占位符：

| 占位符 | 含义 |
|---|---|
| `{{username}}` | 账号 |
| `{{password}}` | 明文密码 |
| `{{password_enc}}` | **按 `pwd_mode` 处理后的密码**（RSA 加密结果就填这个） |
| `{{password_md5}}` `{{password_sha256}}` | 强制指定哈希 |
| `{{timestamp}}` | 13 位毫秒时间戳 |
| `{{cookie}}` | 当前 Cookie |

**这是最省事的路子**：把抓到的 body 原样贴进界面那个模板框，只把账号密码换成占位符，
不需要关心字段名、包装层、加密方式 —— 程序按模板发，跟浏览器一模一样：

```
{"param":{"logName":"{{username}}","pwd":"{{password_enc}}"}}
```

### 第 3 招：从 259 字节反推 + 直接扒前端 JS

`user.txt` 里 `Content-Length: 259`，而明文账号密码只有 ~50 字节，说明体里有别的东西。
倒推最贴合的结构：

```
{"param":{"logName":"<12位学号>","pwd":"<128位hex>"}}   ≈ 259 字节  ✅
        ↑ 包装层              ↑ 字段名    ↑ 128位hex = 64字节 = RSA-512 密文
```

配合 `Set-Cookie: rememberMe=deleteMe`（**Shiro 特征**，后端是 Java + Spring + Shiro），
基本可以确定：**密码在前端被 RSA 加密成 hex 再发**。

前端 JS 是明文的，直接下下来挖公钥：

```powershell
python tools\fetch_js.py                       # 默认 http://10.20.15.27
python tools\fetch_js.py http://10.20.15.27 --save
```

它会下登录页 + 所有引用的 JS，然后：

1. 按 `MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQ...` 等模式挖 RSA 公钥，并**当场验证能不能解析出 (n, e)**
2. 统计 `JSEncrypt` / `setPublicKey` / `CryptoJS` / `sm2` / `md5` / `sha256` 的出现次数 —— 一眼看出密码到底怎么处理的
3. 结果写进 `logs\js-keys.json`

拿到公钥后：界面 `rsa_public_key` 填公钥，`pwd_mode` 选 `rsa_hex`，点「测试登录」。
不行就把 `pwd_mode` 换成 `rsa_b64`；再不行试 `login.rsa_padding` 改 `raw`。

### 第 4 招：还是不行 → 抓一次真实登录包

上面都试过还不行，就上 F12：
登录页 `F12` → Network → 勾 Fetch/XHR → 点 🚫 清空 → 输账号密码点登录 →
找到那条 POST → 右侧 **Payload / 载荷** 标签 → **把那一段完整复制贴给我**。

如果请求头里有 `sign`、`nonce`、`timestamp` 组合的**签名**，纯参数调整解决不了，
需要拿到生成签名的 JS 函数，一样用 `tools\fetch_js.py --save` 把 JS 存下来发我。

---

## 2.6 IC 空间预约系统 —— 实测确定的完整协议

以下**全部来自实机抓包与实测**，不是推测。系统标识：`http://10.20.15.27`，
Vue SPA + nginx + Java/Spring/Shiro，返回码 `0`=成功、`100`=参数错误、`302`=权限不足。

### 登录（两步 + 信封加密）

```
① GET  /ic-web/login/publicKey
   -> {"code":0,"data":{"publicKey":"MIGfMA0G...","nonceStr":"<32位hex>"}}
      ⚠️ publicKey 和 nonceStr 每次请求都会变

② POST /ic-web/login/user
   Content-Type: application/json
   {"logonName":"<学号>","password":"<RSA密文 base64>","captcha":"","consoleType":16,"privacy":true}
      密码加密明文 = 密码 + ";" + nonceStr
      加密方式     = RSA PKCS#1 v1.5 → base64（1024 位密钥 → 172 字符）

   -> {"code":0,"message":"登录成功","data":{"token":"<32位hex>","accNo":100510484,...}}
      后续所有请求带  token: <token>  和  lan: 1
```

**关键坑**：`pwd_mode` 必须是 `auto_pubkey`。填 `plain` 会返回 `code=100`；
填 `plain` 且服务端走到密码校验时也可能返回 `code=900 账号或密码不正确`（误导性极强）。

**限流**：连续登录会返回 `code=1 请求频繁,请稍后重试`。程序已内置会话复用 + 退避。

### 场馆与座位

```
GET /ic-web/room/getAll?sysKind=8          # sysKind=8 是座位；sysKind=1 是研讨室
   -> 15 个自习室：二楼A区(100492994) 二楼B区(100492996) ... 七楼东(C区)(100494744)

GET /ic-web/seatRoom/openScope?roomId=<id>&resvDate=YYYY-MM-DD
   -> 开放时段 08:00-22:00（按星期给出）

POST /ic-web/seatDevice/coordinate         # 座位图（返回每个座位的 devId）
   body/params: {"roomIds":<roomId>,"resvDates":"YYYYMMDD","sysKind":8}
   ⚠️ 程序化调用返回 code=302 权限不足 —— 需要浏览器同源的会话/权限；
      实测用浏览器 F12 里那条请求的 token 可以正常返回
```

### 下单（`POST /ic-web/reserve/seat`）

实测抓包的**完整请求体**：

```json
{"sysKind":8,"appAccNo":100510484,"memberKind":1,"resvMember":[100510484],
 "resvBeginTime":"2026-10-16 08:00:00","resvEndTime":"2026-10-16 22:00:00",
 "testName":"","captcha":"","resvProperty":0,"resvDev":[0],
 "encryptVlaue":"<172字符 RSA>",
 "resvDevEncrypts":["522C1B95D2B40C5F05BB3BAAD1380099"],"memo":""}
```

**信封加密**（这是全系统最关键的一环）：

```
a = 16 位随机口令（[A-Za-z0-9]，对应前端 Object(d["e"])(16)）
encryptVlaue      = RSA(a)            → base64，128 字节 / 172 字符
resvDevEncrypts[] = AES(devId, a)     → 大写 hex，16 字节 / 32 字符
```

即：**座位 ID 用口令做 AES 加密，口令本身再用 RSA 加密**。服务端先解 RSA 拿口令，
再解 AES 拿 devId。所以 `encryptVlaue` 和 `resvDevEncrypts` 是**配对**的，不能只改一个。

程序已完整实现（`app/aes.py` 纯标准库 AES-128，通过 FIPS-197 官方测试向量）。
AES 参数可配：`booking.aes_mode`(ecb/cbc)、`booking.aes_padding`(zero/pkcs7)。
默认 `ecb` + `zero` —— 依据是抓包密文只有 **1 个 AES 块**，
而 PKCS7 对 16 字节整数倍明文会膨胀成 2 块。

**devId 就是你要的「座位 ID」**：它是座位图里每个座位的设备 ID（数字），
不是座位号（如 `A-037`），也不能手填数字 —— 必须走上面的 AES 加密。

---

## 2.7 还差最后一样：座位图的 devId

协议层面**只差 devId**，其它全部打通。devId 来自座位图接口
`POST /ic-web/seatDevice/coordinate`，但它要求 `{roomIds, resvDates:"YYYYMMDD", sysKind:8}`，
且程序化调用会被判 `code=302 权限不足`。

**两个取法，任选**：

**A. 浏览器 F12（最快）**
1. 进座位预约页 → 点某个自习室（如「二楼A区」）
2. Network 里找 `seatDevice/coordinate`
3. 右键 → **Copy → Copy as cURL (bash)** 贴给我，**并把 Response 也贴我**
4. 响应里每个座位对象的 `devId` 就是座位 ID，形如
   `{"devId":1004930xx,"devName":"A-037","devProp":0,"resvInfo":[...]}`

**B. 直接把 devId 填进配置**
界面上「座位 ID」填数字即可，程序会自动做 AES+RSA 信封加密。

**C. 用重放模板（连 devId 都不需要）**
把上面那条 cURL 的 body 原样贴进界面第 ④ 块「重放模板」，
把 `encryptVlaue` 和 `resvDevEncrypts` 换成占位符 —— 但这两个是每次随机的，
所以更推荐 A/B：让程序自己算。

---

## 3. 历史：早期采集清单（已全部完成）

> 这一节是最初找协议时列的待办，现在**已全部拿到并实测确认**，保留作排查参考。

`test.txt` 里只有一条 `GET /ic-web/sysInfo`，**没有登录包、没有座位列表包、没有预约 POST 包，也没有响应体**。
程序的骨架、定时器、重试、前端都已完成；剩下要填的是「你们学校这版 IC 系统的确切字段名」。给我下面这几条，我改 `config.json`（甚至不用改代码）就能精确命中：

1. **登录请求**：`POST /ic-web/login/user` 的完整 URL + 请求体 + 响应体 ✅ 已完成（见 2.6）
2. **座位列表请求**：点进某个自习室时那条请求 + 响应
3. **预约 POST（最关键）**：真正点「提交预约」那一条，完整 URL + 请求体 + 响应 ✅ 已完成（见 2.6）
4. **响应成功判定**：`code` 0=成功、100=参数错误、302=权限不足 ✅ 已完成

拿不到第 3 条就用下面的中继器：

```powershell
# 仓库里没有内置中继脚本，用 mitmproxy / Charles 即可
# mitmproxy 示例: mitmproxy --mode regular@8800
```

浏览器代理插件（SwitchyOmega）把 HTTP 代理指到 `127.0.0.1:8800`，然后正常走一遍「登录→选座→提交预约」，
`logs/capture-*.txt` 里就是逐字节的请求和响应。（只支持 HTTP；HTTPS 请用 F12 或 Charles/mitmproxy。）

### HAR 一键导出 + 自动解析（推荐）

手工扒包容易漏。用 HAR 一步搞定：

1. F12 → Network → 勾上 **Preserve log**（不勾的话登录后跳转会把包清空）
2. 走完「登录 → 进自习室 → 点座位 → 提交预约」
3. Network 面板空白处右键 → **Save all as HAR with content**
4. 存到 `E:\Project\Reserve\capture.har`
5. 跑解析器：

```powershell
python tools\parse_har.py capture.har
```

它会自动分类出 `LOGIN` / `SEATS` / `BOOKING` / `OTHER` 四组，打印每条请求的**完整 URL、请求头、
请求体、响应体**，并反推配置建议：

| 输出 | 内容 |
|---|---|
| 控制台 | 分类摘要 + 每条包全文 |
| `logs/har-report.txt` | 完整报告（不截断） |
| `logs/har-config-suggestion.json` | 根据包反推的字段名/路径/编码/密码哈希方式建议 |

`har-config-suggestion.json` 里的 `notes` 会明确告诉你：登录是 form 还是 json、密码是 md5 还是
sha256、请求体外面套没套 `param`、预约该用 `segmentIds` 还是 `startTime`。

把这个 `.har`（或那两个输出文件）发我，我一次把配置定死。

### 手机 App 怎么办

App 走不了 F12。两个选择：
- 用 `mitmproxy` / `Charles` 做 HTTP 中继（手机 Wi-Fi 里设手动代理，指向你电脑的 `8800` 端口）
- 或 `mitmproxy` / Charles（能装根证书就支持 HTTPS）

---

## 4. 座位 ID 怎么填 / 怎么拿（你最关心的）

**先纠正一个常见误解：`seatId` 不是座位号，而是后端数据库里的座位主键。**
座位号（`A-037` / `西区3楼-12号`）是给人看的，接口只认 `seatId`。两者完全不同，填错会报「座位不存在」。
所以：**不要猜、不要按顺序编，必须抓一次真实请求。**

`seatId` 通常是纯数字（如 `1048576`、`20173`、`93`），也可能是带前缀的串（如 `SEAT_20240101_0001`）。
程序对两种都兼容，填数字时不加引号，填字符串时原样写。

### 方法 A：浏览器 F12（推荐，1 分钟）

1. Chrome/Edge 打开图书馆座位预约网页，先登录进「座位预约」页。
2. `F12` → **Network（网络）** → 勾 **Fetch/XHR** → 点 🚫 清空。
3. **点击你要预约的那个具体座位**，让它弹出时段选择框。
4. 在 Network 列表里找这些名字（按顺序试）：
   - `freeSeat`、`seatList`、`list`、`seatStatus`、`spaceSeat`、`seat/detail`
5. 点开 → 看 **Response（响应）**，里面每个座位对象都会有 `seatId` 和 `seatName`：

```json
{
  "code": 200,
  "data": [
    { "seatId": 1048576, "seatName": "西区3楼-A037", "spaceId": 12, "status": 1 },
    { "seatId": 1048577, "seatName": "西区3楼-A038", "spaceId": 12, "status": 0 }
  ]
}
```

`status` 一般是 `1/0`、`true/false`、`"free"/"used"`，含义以你实际看到的为准。

6. **更保险的做法**：走到最后一步真正点「提交预约」，抓那条 POST，请求体里就有系统自己填的 `seatId`：

```json
{ "seatId": 1048576, "spaceId": 12, "date": "2026-10-16", "segmentIds": [1, 2] }
```

   → 把 `1048576` 原样填进界面的「座位 ID」。

7. 顺手在 Network 里右键任意一条 → **Copy → Copy as cURL (bash)**，整行粘给我，字段名我一次就定死。

### 方法 B：用本程序直接查

界面上填好 `base_url`、`space_id`、`reserve_date`、`segment_ids`，点 **「查询可用座位（拿 seatId）」**
（等价于 `GET http://127.0.0.1:8765/api/seats?date=2026-10-16`）。
程序会请求 `/ic-web/seat/freeSeat` 并把结果渲染成表格 —— **点任意一行，`seatId` 自动填进输入框**。

解析不出来也不要紧：界面会把服务器原始返回原样显示（那个「原始返回」框），把它贴给我即可。

### 方法 C：手机 App

App 走不了 F12。用 `mitmproxy` / `Charles` 做 HTTP 中继，
同上找预约 POST 的 body。

### 方法 D：先用「探测接口」摸清端点

界面点 **「探测接口」**（`GET /api/probe/login`），程序依次 GET 几个常见路径，
把状态码和非 404 的返回列出来。先确认这套系统确实是 `/ic-web/*` 系，再往下填。

---

## 5. 时段（`segment_ids` vs `segments`）

IC 系有版本差异，两种编码都可能出现，程序两种都发得出去（填哪个就带哪个）：

- `segment_ids: "1,2,3"` —— **时段序号**，最常见，指当天第 1/2/3 个时段。
- `segments: "930,931"` —— **时段编码/ID**，有些版本用它。

**怎么确定用哪个**：看预约 POST 原文。
如果 body 里是 `"segmentIds":[1,2]` 就填 `1,2`；是 `"segments":[930]` 就填 `930`。
两者都空的话，程序只发 `date + startTime + endTime`，能否成功取决于你们学校那版接口。

---

## 6. 重放模板（最稳、最不依赖我猜字段）

抓包原文粘进界面第 ④ 块，勾上「启用重放模板」，程序**只替换占位符，其余原样发送**：

```
请求路径: /ic-web/reserve/seat
请求体:   {"sysKind":8,"appAccNo":{{user_id}},"memberKind":1,"resvMember":[{{user_id}}],
           "resvBeginTime":"{{date}} {{start_time}}","resvEndTime":"{{date}} {{end_time}}",
           "testName":"","captcha":"","resvProperty":0,"resvDev":[0],"memo":""}
（注意：IC 系的下单体还需要 encryptVlaue / resvDevEncrypts 两个加密字段，
  重放模式无法生成它们，只能填抓到的固定值 —— 所以对本系统**不推荐用重放模式**，
  直接用内置的 IC 适配器即可。）
```

可用占位符：

| 占位符 | 来源 |
|---|---|
| `{{seat_id}}` `{{space_id}}` `{{seat_name}}` | 预约参数 |
| `{{date}}` `{{reserve_date}}` | 预约日期（已归一化成 `YYYY-MM-DD`） |
| `{{start_time}}` `{{end_time}}` | 起止时间 |
| `{{segment_ids}}` `{{segment_id}}` | 时段序号，输出 `1,2,3` 与首项 |
| `{{segments}}` `{{segment}}` | 时段编码，同上 |
| `{{username}}` `{{password}}` | 账号 |
| `{{cookie}}` `{{token}}` | 当前会话 |
| `{{timestamp}}` | 13 位毫秒时间戳 |
| `{{now}}` | `YYYY-MM-DD HH:MM:SS` |

注意 JSON 里数字字段**不要加引号**：`{{seat_id}}` 渲染成 `1048576` 就是数字，加引号会变字符串，接口可能拒绝。

---

## 7. HTTP API（想用脚本调就用这些）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/config` | 取配置（密码/token 已打码） |
| POST | `/api/config` | 存配置（只提交要改的键即可） |
| POST | `/api/login` | 测试登录，成功后回写 token/cookie |
| GET | `/api/seats?date=YYYY-MM-DD` | 查可用座位 |
| GET | `/api/probe/login` | 探测常见接口路径 |
| POST | `/api/probe` | 透传探测 `{method,path,body,headers}` |
| POST | `/api/start` | 开始定时 |
| POST | `/api/trigger` | 立即执行一次 |
| POST | `/api/stop` | 停止 |
| GET | `/api/state` | 任务状态（剩余秒数、尝试次数） |
| GET | `/api/logs?since=N` | 增量日志 |

---

## 8. 排错速查

| 现象 | 原因 / 处理 |
|---|---|
| 登录返回 401/403 | 密码字段名不对（试 `logName`/`pwd`）或需要 SHA256；贴登录响应给我 |
| 登录返回「验证码」 | 这套系统开了图形验证码，纯请求方案走不通，得先解决验证码 |
| 预约返回 400 参数错误 | 字段名或时段编码不对 → 用重放模板贴原文 |
| 「座位不存在」 | `seatId` 拿错（很可能填成了座位号） |
| 「不在开放时间」 | 学校有开放预约窗口，提前量算错；用 `advance_ms` 微调 |
| 返回 HTML 而不是 JSON | 被 WAF/登录页拦了，或 `base_url` 写错 |
| 定时到点但没发 | 看日志「=== 开抢 ===」是否出现；`fire_time` 已过会被推到第二天 |

---

## 9. 边界说明

- 程序只做**你自己账号的一次预约**，不涉及绕过验证码、不批量占座、不改后端数据。
- 学校系统通常有「单账号同时段仅一个有效预约」限制，重复下单会被拒 —— 这是正常返回，不是程序坏了。
- 抢座违反校规的话后果自负；本工具定位是「把手工点击自动化 + 准点发出」，和手动点按钮是同一个请求。
