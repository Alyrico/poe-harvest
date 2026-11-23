# PyInstaller spec: one folder with "PoE Harvest Tracker.exe", Python and the libraries.
# Tesseract is copied in afterwards (see .github/workflows/build.yml).
import os

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
SRC = os.path.join(ROOT, "src")
PKG = os.path.join(SRC, "poe_harvest")

datas = [
    (os.path.join(PKG, "web"), "poe_harvest/web"),
    (os.path.join(PKG, "assets"), "poe_harvest/assets"),
]

a = Analysis(
    [os.path.join(SPECPATH, "launcher.py")],
    pathex=[SRC],
    datas=datas,
    hiddenimports=["poe_harvest.app", "poe_harvest.settings_window", "mss.windows"],
    excludes=["matplotlib", "scipy", "pandas", "pytest", "IPython", "PyQt5", "PySide2", "PyQt6", "PySide6"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PoE Harvest Tracker",
    icon=os.path.join(PKG, "assets", "app.ico"),
    console=False,                      # a tray app: no black window
    version=os.path.join(SPECPATH, "version_info.txt") if os.path.exists(os.path.join(SPECPATH, "version_info.txt")) else None,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="PoE Harvest Tracker", upx=False)
