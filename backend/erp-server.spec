# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：把后端打成 onedir 目录，供 Electron 作为子进程启动。

构建前请先执行 `npm run build`（frontend），让 ../frontend/dist 存在；
dist 会被打进包内的 web/ 目录，由 FastAPI 同源托管。
"""
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

backend_dir = Path(SPECPATH).resolve()
web_src = backend_dir.parent / "frontend" / "dist"

datas = [(str(web_src), "web")] if web_src.is_dir() else []
# reportlab 自带字体/度量数据文件，PDF 渲染缺了会直接报错
datas += collect_data_files("reportlab")

hiddenimports = (
    collect_submodules("app")
    + collect_submodules("uvicorn")
    + collect_submodules("multipart")
    + collect_submodules("reportlab")
    + collect_submodules("pypdf")
    + [
        "uvicorn.logging",
        "uvicorn.loops.auto",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan.on",
        "sqlalchemy.dialects.sqlite",
        "email.mime.multipart",
        "reportlab.pdfbase.cidfonts",
        "reportlab.pdfbase._cidfontdata",
        "reportlab.graphics.barcode.code128",
    ]
)

icon_file = backend_dir.parent / "desktop" / "build" / "icon.ico"

a = Analysis(
    ["desktop_entry.py"],
    pathex=[str(backend_dir)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy", "pandas", "pytest", "IPython"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="erp-server",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    icon=str(icon_file) if icon_file.is_file() else None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="erp-server",
)
