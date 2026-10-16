"""打包成 Windows exe（PyInstaller）。

核心设计：**在一个临时副本里脱敏后打包**，项目目录本身一个字节都不改。
这样既能保证产物干净，又不会破坏你的开发环境（配置文件、真实 IP 都保留）。

用法：
  python tools\\build_exe.py                 # 完整流程：复制 -> 脱敏 -> 打包 -> 验证
  python tools\\build_exe.py --keep          # 保留临时构建目录（排查用）
  python tools\\build_exe.py --no-verify     # 跳过产物隐私验证（快）
  python tools\\build_exe.py --embed-ip 10.20.15.27   # 高级：把服务器地址固化进 exe

产物：dist\\Reserve\\  （整个目录一起分发，双击 Reserve.exe 即可）

脱敏规则（只作用于构建副本，不动你的项目）：
  服务器地址 10.20.15.27  -> 10.0.0.10（config.py 的默认值改为空，由用户界面填写）
  accNo      100510484... -> 100000001 / 100000002
  学号       202301050786 -> 202300000000
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 不复制到构建副本里的东西
# - 凭据/运行数据：绝不能进产物
# - tests/ tools/：PyInstaller 已 exclude，且里面的测试常量是隐私检测的特征来源，
#   改掉它们会让「验证」自相矛盾，所以干脆不复制
SKIP_DIRS = {".git", ".venv", "logs", "dist", "build", "__pycache__",
             ".idea", ".pytest_cache", "tests", "tools"}
SKIP_FILES = {"config.json", "config.json.tmp", "test.txt", "user.txt", "capture.har"}

# 脱敏规则：(正则, 替换, 说明)
# 注意：座位名（如 2F-B001 / 4F-A106）也会暴露学校与楼层信息，一并替换
RULES = [
    (r"10\.20\.15\.27", "10.0.0.10", "学校内网服务器地址"),
    (r"\b1005104\d{2}\b", lambda m: "100000001" if m.group(0) == "100510484" else "100000002", "真实 accNo"),
    (r"\b20230105\d{4}\b", "202300000000", "真实学号"),
    (r"\b[2-7]F-[A-Z]\d{3}\b", "A-001", "真实座位名"),
]

SCAN_EXTS = (".py", ".js", ".html", ".css", ".json", ".md", ".bat", ".spec")


def copy_project(dest: str) -> int:
    """把项目复制到构建目录，跳过凭据与无关文件。"""
    n = 0
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        rel = os.path.relpath(dirpath, ROOT)
        target_dir = dest if rel == "." else os.path.join(dest, rel)
        os.makedirs(target_dir, exist_ok=True)
        for fn in filenames:
            if fn in SKIP_FILES:
                continue
            shutil.copy2(os.path.join(dirpath, fn), os.path.join(target_dir, fn))
            n += 1
    return n


def sanitize(dest: str, embed_ip: str = "") -> list:
    """在构建副本里替换隐私串。返回 [(相对路径, 命中说明)]。"""
    changed = []
    for dirpath, dirnames, filenames in os.walk(dest):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if not fn.endswith(SCAN_EXTS):
                continue
            path = os.path.join(dirpath, fn)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    text = orig = f.read()
            except (UnicodeDecodeError, OSError):
                continue

            hits = []
            for rx, rep, why in RULES:
                new_text, cnt = re.subn(rx, rep, text)
                if cnt:
                    text = new_text
                    hits.append(f"{why}×{cnt}")

            # config.py 的 base_url 默认值：默认留空，让用户在界面上填
            if fn == "config.py":
                text, c = re.subn(r'"base_url":\s*"[^"]*"',
                                  f'"base_url": "{embed_ip}"', text)
                if c:
                    hits.append("base_url 默认值" + ("（已固化）" if embed_ip else "（留空待填）"))

            if text != orig:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(text)
                changed.append((os.path.relpath(path, dest).replace("\\", "/"), ", ".join(hits)))
    return changed


def _extract_module_code(za, name: str):
    """从 ZlibArchiveReader 取出模块的 code 对象。

    PyInstaller 版本差异：
      6.x  -> extract() 直接返回 code 对象
      5.x  -> extract() 返回 (magic+flags+...) 16 字节头 + marshal 字节码
    两种都兼容。
    """
    import marshal
    data = za.extract(name)
    if hasattr(data, "co_consts"):        # 已经是 code 对象
        return data
    if isinstance(data, (bytes, bytearray)):
        for payload in (data[16:], data):  # 先跳头，不行再试不跳
            try:
                code = marshal.loads(payload)
                if hasattr(code, "co_consts"):
                    return code
            except Exception:
                continue
    return None


def verify_bundle(dist_dir: str, allow: tuple = ()) -> bool:
    """用 PyInstaller 归档读取器解出真实模块常量，确认无隐私串。

    allow: 允许出现的字符串（例如有意固化进 exe 的服务器地址）。

    注意：不能只做字节搜索 —— PyInstaller 默认用 zlib 压缩 PYZ，
    源码里的字符串在产物里是压缩态，裸搜会「假通过」。
    """
    # 兼容两种调用：传 exe 文件路径，或传包含 Reserve.exe 的目录
    exe = dist_dir if os.path.isfile(dist_dir) else os.path.join(dist_dir, "Reserve.exe")
    if not os.path.isfile(exe):
        print(f"  [错误] 找不到 exe: {exe}")
        return False
    try:
        sys.path.insert(0, ROOT)
        from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader
    except ImportError:
        print("  [跳过] 无法导入 PyInstaller 读取器（无法验证，请人工确认）")
        return False

    tmpd = tempfile.mkdtemp(prefix="verify-")
    try:
        ca = CArchiveReader(exe)
        pyz_path = os.path.join(tmpd, "PYZ.pyz")
        with open(pyz_path, "wb") as f:
            f.write(ca.extract("PYZ.pyz"))
        za = ZlibArchiveReader(pyz_path)
        mods = sorted(m for m in za.toc if m.startswith("app"))

        def walk(code, out):
            for c in code.co_consts:
                if isinstance(c, str):
                    out.append(c)
                elif hasattr(c, "co_consts"):
                    walk(c, out)
                elif isinstance(c, (tuple, frozenset)):
                    for x in c:
                        if isinstance(x, str):
                            out.append(x)

        consts = []
        ok_modules = 0
        for m in mods:
            code = _extract_module_code(za, m)
            if code is not None:
                ok_modules += 1
                walk(code, consts)

        if ok_modules == 0 or not consts:
            print(f"  ⚠️ 验证失败：解出 {ok_modules}/{len(mods)} 个模块、{len(consts)} 个常量")
            print("     无法确认产物是否干净，请勿直接分发！")
            return False

        # 检测特征串。allow 里的（如有意固化的服务器地址）不算泄露。
        # 注意：**不要把真实密码写进这里**。密码是运行期用户在界面输入的，
        #       源码/产物里本来就不该有；把它当特征串反而会把它写进仓库。
        needles = [
            "10.20.15.27", "202301050786", "202301050835",
            "100510484", "100510404",
            "4F-A106", "2F-B001",
            "高希旻", "王月敬",
        ]
        allow_set = set()
        for a in allow:
            if not a:
                continue
            a = str(a).strip()
            allow_set.add(a)
            # --embed-ip 可能是完整 URL，也要把裸主机部分加进白名单
            allow_set.add(re.sub(r"^https?://", "", a).rstrip("/").split("/")[0])
        bad = []
        allowed_hits = []
        for needle in needles:
            if needle in allow_set:
                # 有意固化：确认它确实存在，但不报错
                if any(needle in s for s in consts):
                    allowed_hits.append(needle)
                continue
            hit = [s for s in consts if needle in s]
            if hit:
                bad.append((needle, hit[0][:70]))

        if bad:
            print("  ⚠️ 产物中仍含隐私信息：")
            for needle, ex in bad:
                print(f"      {needle}  ->  {ex!r}")
            return False

        print(f"  ✅ 已验证：{ok_modules}/{len(mods)} 个 app 模块、{len(consts)} 个字符串常量")
        print(f"     检查了 {len(needles) - len(allow_set)} 类隐私特征，全部未命中")
        if allowed_hits:
            print(f"     白名单（有意固化，已确认存在）：{', '.join(allowed_hits)}")
        return True
    finally:
        shutil.rmtree(tmpd, ignore_errors=True)


def remove_file_checked(path: str, tries: int = 6) -> bool:
    """删除文件并确认真的删掉了（Windows 上被占用时会失败）。"""
    import time as _t
    if not os.path.exists(path):
        return True
    for i in range(tries):
        try:
            os.remove(path)
        except OSError:
            pass
        if not os.path.exists(path):
            return True
        _t.sleep(0.5 * (i + 1))
    try:
        os.remove(path)
        return True
    except OSError:
        return False


def rmtree_checked(path: str, tries: int = 6) -> bool:
    """删除目录并**确认真的删掉了**。

    Windows 上文件被占用时 rmtree(ignore_errors=True) 会静默失败，
    紧接着 copytree 就报「目录已存在」。所以这里带重试 + 校验。
    """
    import time as _t
    if not os.path.exists(path):
        return True
    for i in range(tries):
        shutil.rmtree(path, ignore_errors=True)
        if not os.path.exists(path):
            return True
        _t.sleep(0.5 * (i + 1))
    # 最后再试一次并暴露错误
    shutil.rmtree(path)
    return not os.path.exists(path)


def ensure_icon(quiet: bool = False) -> str:
    """确保 app.ico 存在（从 qut.jpg 或其它源图生成）。返回 ico 路径，失败返回 ""。"""
    ico = os.path.join(ROOT, "app.ico")
    src = os.path.join(ROOT, "qut.jpg")
    if not os.path.isfile(src):
        # 找任意一张图当源
        for name in os.listdir(ROOT):
            if name.lower().endswith((".jpg", ".jpeg", ".png")):
                src = os.path.join(ROOT, name)
                break
    if not os.path.isfile(src):
        if not quiet:
            print("      （未找到源图，跳过图标）")
        return ico if os.path.isfile(ico) else ""

    # 源图比 ico 新就重新生成
    if os.path.isfile(ico) and os.path.getmtime(ico) >= os.path.getmtime(src):
        if not quiet:
            print(f"      复用已有图标 {os.path.basename(ico)}")
        return ico

    try:
        from PIL import Image
    except ImportError:
        if not quiet:
            print("      （未安装 Pillow，跳过图标生成）")
        return ico if os.path.isfile(ico) else ""

    try:
        im = Image.open(src).convert("RGBA")
        w, h = im.size
        if w != h:                      # 居中裁成正方形
            side = min(w, h)
            im = im.crop(((w - side) // 2, (h - side) // 2,
                          (w - side) // 2 + side, (h - side) // 2 + side))
        im.save(ico, format="ICO", sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
        if not quiet:
            print(f"      已从 {os.path.basename(src)} 生成图标 "
                  f"{os.path.basename(ico)} ({os.path.getsize(ico):,} B)")
        return ico
    except Exception as e:
        if not quiet:
            print(f"      （生成图标失败: {e!r}）")
        return ico if os.path.isfile(ico) else ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="保留临时构建目录")
    ap.add_argument("--no-verify", action="store_true", help="跳过产物验证")
    ap.add_argument("--embed-ip", default="", help="把服务器地址固化进 exe（默认留空）")
    args = ap.parse_args()

    pyi = os.path.join(ROOT, ".venv", "Scripts", "pyinstaller.exe")
    if not os.path.isfile(pyi):
        pyi = shutil.which("pyinstaller") or ""
    if not pyi:
        print("[错误] 找不到 pyinstaller。先安装：")
        print("  .venv\\Scripts\\python -m pip install pyinstaller -i https://pypi.tuna.tsinghua.edu.cn/simple")
        return 1

    build_dir = os.path.join(tempfile.gettempdir(), f"reserve-build-{int(time.time())}")
    print("=" * 70)
    print("  图书馆座位预约助手 —— 打包 exe")
    print("=" * 70)
    print(f"  项目目录   : {ROOT}")
    print(f"  构建目录   : {build_dir}   (临时副本，项目目录不会被修改)")
    print(f"  服务器地址 : {args.embed_ip or '(留空，由用户界面填写)'}")
    print()

    print("[1/5] 复制项目（跳过 config.json / logs / test.txt / user.txt / .venv）")
    icon = ensure_icon()
    if icon:
        print(f"      图标: {icon}")
    n = copy_project(build_dir)
    print(f"      已复制 {n} 个文件")
    # 确保图标也进了构建目录（copy_project 会带上，这里兜底）
    if icon and os.path.isfile(icon):
        dst = os.path.join(build_dir, os.path.basename(icon))
        if not os.path.isfile(dst):
            shutil.copy2(icon, dst)

    print("[2/5] 在副本中脱敏")
    changed = sanitize(build_dir, args.embed_ip)
    for rel, why in changed:
        print(f"      {rel:<40} {why}")
    if not changed:
        print("      （无需修改）")

    print("[3/5] 打包")
    spec = os.path.join(build_dir, "Reserve.spec")
    proc = subprocess.run([pyi, "--clean", "--noconfirm", "--log-level", "WARN", spec],
                          cwd=build_dir, capture_output=True, text=True)
    if proc.returncode != 0:
        print("      [失败] PyInstaller 返回非零")
        print(proc.stdout[-3000:])
        print(proc.stderr[-3000:])
        return 1
    print("      ok")

    print("[4/5] 收集产物到 dist/")
    # onefile 模式：产物是单个 dist/Reserve.exe
    src_exe = os.path.join(build_dir, "dist", "Reserve.exe")
    dist_dir = os.path.join(ROOT, "dist")
    out_exe = os.path.join(dist_dir, "Reserve.exe")
    if not os.path.isfile(src_exe):
        print(f"      [失败] 找不到打包产物 {src_exe}")
        return 1
    if not remove_file_checked(out_exe):      # 旧文件可能被占用（正在运行）
        print(f"      [失败] 无法覆盖旧文件（可能正被运行）: {out_exe}")
        print("      请先关闭正在运行的 Reserve.exe，然后重试")
        return 1
    # 清理上一次 onedir 留下的目录，避免混淆
    rmtree_checked(os.path.join(dist_dir, "Reserve"))
    os.makedirs(dist_dir, exist_ok=True)
    shutil.copy2(src_exe, out_exe)
    size = os.path.getsize(out_exe)
    print(f"      {out_exe}")
    print(f"      单文件 {size:,} B ({size/1024/1024:.1f} MB) —— 分发这一个文件即可")

    print("[5/5] 验证产物无隐私信息")
    allow = (args.embed_ip,) if args.embed_ip else ()
    ok = True if args.no_verify else verify_bundle(out_exe, allow=allow)

    if args.keep:
        print(f"\n  (--keep) 构建目录保留在: {build_dir}")
    else:
        shutil.rmtree(build_dir, ignore_errors=True)

    print()
    print("=" * 70)
    if ok:
        print("  ✅ 打包完成")
        print(f"  分发这一个文件: {out_exe}")
        print("  用户双击即可，无需安装 Python，也不怕漏拷依赖")
    else:
        print("  ⚠️ 打包完成但隐私验证未通过，请检查上面的输出")
    print("=" * 70)
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
