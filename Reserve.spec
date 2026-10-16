# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置。

构建:  python tools\\build_exe.py            （推荐，会自动脱敏并验证）
      或  .venv\\Scripts\\pyinstaller.exe --clean --noconfirm Reserve.spec

产物:  dist\\Reserve.exe  —— **单个文件**，怎么传都不会漏依赖

为什么用 onefile（而不是 onedir）:
  实测 onedir 产物是「Reserve.exe + _internal 目录」，用户只拷 exe 会报
      Failed to load Python DLL '...\\_internal\\python311.dll'
  而且双击闪退、什么都不显示，用户看起来就是「打不开」。
  onefile 把依赖全塞进一个 exe，彻底消除这类传递事故。
  代价：首次启动要解压到临时目录，慢 2~3 秒。

隐私: 只打包 web/ 静态资源；绝不打包 config.json / logs / test.txt / user.txt
      另外 tests/ tools/ 也排除（本地调试用，且含测试常量）
"""
import os

block_cipher = None
ROOT = os.path.abspath(os.getcwd())

# 图标：tools/build_exe.py 会先由 qut.jpg 生成 app.ico；没有就不加图标
ICON = os.path.join(ROOT, "app.ico")
if not os.path.isfile(ICON):
    ICON = None

# 只把这些打进产物
datas = [
    (os.path.join(ROOT, "web"), "web"),      # 前端页面（必需）
]

excludes = [
    "tests", "tools",          # 本地调试用，不分发
    "pytest", "unittest",
    "tkinter",                 # 用不到，减体积
    "matplotlib", "numpy", "pandas", "PIL", "PyQt5", "PySide2",
]

hiddenimports = ["app", "app.server", "app.adapters", "app.scheduler",
                 "app.config", "app.crypto", "app.aes", "app.httpclient"]

a = Analysis(
    ["run.py"],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# onefile：所有依赖都打进这个 exe
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="Reserve",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                 # UPX 压缩会显著提高杀软误报，关掉
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,              # 保留控制台：端口、报错可见
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON,                 # 由 tools/build_exe.py 从 qut.jpg 生成
)

