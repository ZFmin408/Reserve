# -*- coding: utf-8 -*-
"""把 README 开头的过时部分替换成当前实际情况。

为什么用脚本而不是直接改文件：
  之前用 PowerShell 的 Get-Content -Raw + WriteAllText 编辑 README，
  造成过 GBK/UTF-8 双重编码损坏（29474 字节的乱码），
  所以这里统一用 Python + 显式 UTF-8，并且改完立刻回读校验。

用法： python tools/update_readme.py [--dry-run]
"""
from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
README = os.path.join(ROOT, "README.md")

NEW_HEADER = """# 图书馆座位预约助手

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
cd E:\\Project\\Reserve
Copy-Item config.example.json config.json
python run.py
```

浏览器自动打开 `http://127.0.0.1:8765/`（只监听本机，不对外）。
端口被占用会自动顺延（最多试 50 个），也可指定：`$env:RESERVE_PORT=9000; python run.py`。
不自动开浏览器：`$env:RESERVE_NO_BROWSER=1`。
指定配置文件路径（测试/多实例）：`$env:RESERVE_CONFIG=D:\\tmp\\test.json`。

### 方式二：双击 exe（推荐分发）

```
dist\\Reserve.exe        ← 就这一个文件，拷走即可用
```

打包：

```powershell
python tools\\build_exe.py --embed-ip http://你的服务器地址
```

- 产物是**单文件**，不会出现「只拷了 exe 没拷 `_internal` 导致闪退」
- 打包在临时副本里进行，**项目目录不会被修改**
- 默认脱敏（学号 / 密码 / accNo / 座位名都不会进产物）
- `--embed-ip` 把服务器地址固化进去，用户只需填学号密码
- 不加 `--embed-ip` 则地址留空，由用户在界面上填

验证产物：

```powershell
python tools\\verify_exe.py --password 你的密码
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
python tools\\make_icon.py --src 你的图.png --out app.ico
```

---

## 6. 配置项与界面字段对照

配置文件 `config.json`，界面上每个输入框都直接对应它，两边等价。
"""

# 旧的「## 2. 程序留出的接口（配置项 → 界面字段）」标题要跟着改编号
OLD_SEC2 = "## 2. 程序留出的接口（配置项 → 界面字段）"
NEW_SEC2 = "## 6.1 字段名配置（一般不用动）"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    raw = open(README, "rb").read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        print(f"[错误] README 不是合法 UTF-8，可能已被编辑器破坏: {e}")
        print("       先执行 git checkout -- README.md 恢复")
        return 1

    lines = text.splitlines(keepends=True)

    # 找到旧的第一个 "---" 之后、"## 1. 启动" 所在段落结束的位置：
    # 旧开头 = 第 1 行到 "## 2. 程序留出的接口" 之前
    idx = None
    for i, l in enumerate(lines):
        if l.startswith(OLD_SEC2):
            idx = i
            break
    if idx is None:
        print(f"[错误] 找不到旧标题: {OLD_SEC2}")
        return 1

    rest = lines[idx:]
    # 把紧跟其后的那一行旧标题替换成新编号
    if rest and rest[0].startswith(OLD_SEC2):
        rest[0] = NEW_SEC2 + "\n"

    new_text = NEW_HEADER + "\n" + "".join(rest)

    print("=" * 70)
    print("  更新 README")
    print("=" * 70)
    print(f"  原文件   : {len(raw):,} B, {len(lines)} 行")
    print(f"  替换范围 : 第 1 行 ~ 第 {idx} 行（{idx} 行）")
    print(f"  保留部分 : 第 {idx+1} 行 ~ 第 {len(lines)} 行（{len(lines)-idx} 行）")
    print(f"  新文件   : {len(new_text.encode('utf-8')):,} B, {len(new_text.splitlines())} 行")

    if args.dry_run:
        print()
        print("  (--dry-run) 未写入")
        return 0

    # 原子写：先写临时文件再替换，避免中途失败留下半截文件
    tmp = README + ".tmp"
    with open(tmp, "wb") as f:
        f.write(new_text.encode("utf-8"))
    os.replace(tmp, README)

    # 回读校验：确认是合法 UTF-8，且关键内容都在
    back_raw = open(README, "rb").read()
    back = back_raw.decode("utf-8")
    checks = {
        "合法 UTF-8": True,
        "含「自定义时间段」": "自定义时间段" in back,
        "含单文件 exe 说明": "单文件" in back,
        "含提前预约时间范围": "提前预约时间范围" in back,
        "含重试策略表": "不可重试" in back,
        "含 RESERVE_CONFIG": "RESERVE_CONFIG" in back,
        "含 make_icon": "make_icon" in back,
        "保留了座位 ID 章节": "座位 ID 怎么填" in back,
        "保留了 HTTP API 章节": "HTTP API" in back,
        "开头标题正确": back.startswith("# 图书馆座位预约助手"),
    }
    print()
    for k, v in checks.items():
        print(f"  {'OK ' if v else '!! '} {k}")
    bad = [k for k, v in checks.items() if not v]
    if bad:
        print()
        print(f"  ⚠️ 校验未通过: {bad}")
        print("     可执行 git checkout -- README.md 回滚")
        return 1
    print()
    print(f"  ✅ 已更新: {README}")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
