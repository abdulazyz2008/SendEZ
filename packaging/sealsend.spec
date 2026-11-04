# PyInstaller recipe for the SendEZ desktop app (macOS .app, Windows .exe, Linux folder).
# Build from the repository root:  pyinstaller packaging/sealsend.spec --noconfirm
import os
import sys

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
ICON = os.path.join(ROOT, "sealsend", "static", "icon-512.png")  # converted to .icns/.ico by PyInstaller (Pillow)
VERSION = os.environ.get("SEALSEND_VERSION", "1.0.0")

datas = [
    (os.path.join(ROOT, "sealsend", "static"), "sealsend/static"),
    (os.path.join(ROOT, "sealsend", "fonts"), "sealsend/fonts"),
    (os.path.join(ROOT, "sample"), "sample"),
]
binaries = []
hiddenimports = collect_submodules("uvicorn") + collect_submodules("sealsend") + ["webview"]
for pkg in ("pymupdf", "reportlab"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    [os.path.join(ROOT, "desktop.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "matplotlib", "IPython", "pytest"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="SendEZ", console=False, icon=ICON)
coll = COLLECT(exe, a.binaries, a.datas, name="SendEZ")
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="SendEZ.app",
        icon=ICON,
        bundle_identifier="app.sendez.desktop",
        info_plist={
            "CFBundleName": "SendEZ",
            "CFBundleDisplayName": "SendEZ",
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
        },
    )
