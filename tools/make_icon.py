"""把图片转成 Windows 图标（.ico），供 PyInstaller 打包使用（纯标准库 + Pillow）。

用法：
  python tools/make_icon.py                       # qut.jpg -> app.ico
  python tools/make_icon.py --src 图片.png --out app.ico
  python tools/make_icon.py --sizes 16,24,32,48,64,128,256

说明：
- 非正方形图片会自动居中裁剪成正方形，避免变形
- 默认生成 Windows 常用的多尺寸 ICO（任务栏/桌面/缩略图各用一档）
- 只有打包时需要，不参与程序运行
"""
from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SRC = os.path.join(ROOT, "qut.jpg")
DEFAULT_OUT = os.path.join(ROOT, "app.ico")

# Windows 图标常用尺寸；256 用于大图标视图，是必须的
DEFAULT_SIZES = [16, 24, 32, 48, 64, 128, 256]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=DEFAULT_SRC)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--sizes", default=",".join(str(s) for s in DEFAULT_SIZES))
    args = ap.parse_args()

    try:
        from PIL import Image
    except ImportError:
        print("[错误] 需要 Pillow。安装：")
        print("  .venv\\Scripts\\python -m pip install pillow "
              "-i https://pypi.tuna.tsinghua.edu.cn/simple")
        return 1

    if not os.path.isfile(args.src):
        print(f"[错误] 找不到源图片: {args.src}")
        return 1

    sizes = []
    for x in args.sizes.split(","):
        x = x.strip()
        if x.isdigit() and 0 < int(x) <= 256:
            sizes.append(int(x))
    if not sizes:
        print("[错误] --sizes 无效")
        return 1
    sizes = sorted(set(sizes))

    print("=" * 66)
    print("  生成 Windows 图标")
    print("=" * 66)
    im = Image.open(args.src)
    print(f"  源图   : {args.src}")
    print(f"  原始   : {im.size[0]}x{im.size[1]} {im.mode}")

    # RGBA 便于保留透明通道（JPG 没有 alpha，补成不透明）
    im = im.convert("RGBA")

    # 非正方形 -> 居中裁剪成正方形，避免拉伸变形
    w, h = im.size
    if w != h:
        side = min(w, h)
        left = (w - side) // 2
        top = (h - side) // 2
        im = im.crop((left, top, left + side, top + side))
        print(f"  裁剪   : 居中裁成 {side}x{side}（原图非正方形）")

    # Pillow 的 .ico 保存会在给定 sizes 里自动缩放；先放大到最大尺寸保证清晰
    biggest = max(sizes)
    if im.size[0] < biggest:
        print(f"  放大   : {im.size[0]} -> {biggest}（原图偏小，放大后 256 档会略糊）")

    out_dir = os.path.dirname(os.path.abspath(args.out))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    im.save(args.out, format="ICO", sizes=[(s, s) for s in sizes])
    size = os.path.getsize(args.out)
    print(f"  输出   : {args.out}")
    print(f"  尺寸   : {', '.join(str(s) for s in sizes)}")
    print(f"  体积   : {size:,} B")
    print()
    print("  ✅ 完成。打包时会自动使用它（见 Reserve.spec）")
    print("=" * 66)
    return 0


if __name__ == "__main__":
    sys.exit(main())
