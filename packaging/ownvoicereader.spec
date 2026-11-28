# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for OwnVoiceReader — a cross-platform desktop binary.

Builds a **one-dir** bundle (NOT one-file: one-file extracts to a temp dir on every
launch, which is slow and trips antivirus). The bundle ships:
  * the **windowed** entry (ownvoicereader_entry.py) — opens the viewer in its own
    native window and forces --window (the browser is a *loud* last resort only);
  * the **piper** TTS subprocess (piper_entry.py) — so the binary is self-contained
    and offline; ``locate_piper_binary()`` finds it at ``sys.executable.parent/piper``;
  * the **viewer** assets (index.html, transport.js, vendored PDF.js + Phosphor, the
    branding imgs) and the **bundled voice** (en_US-lessac-medium), copied into the
    user cache on first run;
  * **core + vision + ocr** — the C-extension deps are collect_all'd so the native
    libs + in-package models ship.

Console is controllable via the ``OV_CONSOLE`` env var: 1 = a console window (for
--auto/debug, the default for local builds), 0 = a silent GUI build (the release
default). Build with:  pyinstaller packaging/ownvoicereader.spec
"""

import os

from PyInstaller.utils.hooks import collect_all, collect_data_files

# SPECPATH is the dir holding this .spec (packaging/); the repo root is its parent.
REPO = os.path.dirname(os.path.abspath(SPECPATH))
VIEWER = os.path.join(REPO, "ownvoicereader", "viewer")
VOICE = os.path.join(REPO, "ownvoicereader", "voice", "models")

# console=1 (debuggable) for local builds; the release GUI build sets OV_CONSOLE=0.
CONSOLE = os.environ.get("OV_CONSOLE", "1") == "1"

# The C-extension / heavy deps that PyInstaller's static analysis can't see fully —
# their native libs + in-package models must be collected wholesale.
HEAVY = [
    "pymupdf", "pydantic_core", "yaml", "uvicorn",
    "rapidocr", "onnxruntime", "cv2", "numpy",
    "ollama", "piper", "PySide6", "qtpy", "webview",
    "fastapi", "starlette", "pydantic", "websockets", "python_multipart",
]

all_datas: list = []
all_binaries: list = []
all_hidden: list = []

# --- trim the non-essential layer of PySide6 ----------------------------------
# A Qt *Widgets*-based webview (QtWebEngineWidgets, what pywebview's Qt backend
# uses) loads the QtWebEngine/Chromium C++ core but NOT Qt's UI translations,
# its QML/Quick data, the Qt command-line dev tools, or build-time type stubs.
# Trimming them cuts ~110 MB off PySide6 (673 MB -> ~560 MB) with no effect on the
# window. The 457 MB Qt/lib (libQt6WebEngineCore + Chromium) is the actual
# renderer and is kept. Match on exact path components (not substrings) so, e.g.,
# "qml" trims the Qt/qml data dir but not the QtQml module binding. metatypes is
# deliberately kept (Qt's meta-object system; borderline for a pure-Widgets app).
_PYSIDE6_TRIM_DIRS = {
    "translations", "qml", "include", "typesystems",
    "qmlls", "qmlformat", "qmlscene", "assistant", "linguist",
    "lupdate", "lrelease", "designer",
}


def _trim_pyside6(items):
    out = []
    for it in items:
        src = it[0] if isinstance(it, (list, tuple)) else it
        s = str(src)
        if s.lower().endswith(".pyi"):
            continue
        parts = set(s.replace("\\", "/").split("/"))
        if parts & _PYSIDE6_TRIM_DIRS:
            continue
        out.append(it)
    return out


for _pkg in HEAVY:
    try:
        _d, _b, _h = collect_all(_pkg)
    except Exception as _exc:  # pragma: no cover - a missing extra shouldn't abort the build
        print(f"warning: collect_all({_pkg!r}) failed: {_exc}")
        continue
    if _pkg == "PySide6":
        _d = _trim_pyside6(_d)
        _b = _trim_pyside6(_b)
    all_datas += _d
    all_binaries += _b
    all_hidden += _h

# rapidocr ships its onnx models inside the package; collect their data explicitly.
try:
    all_datas += collect_data_files("rapidocr")
except Exception:  # pragma: no cover
    pass

# Conda's Python is linked against conda's OpenSSL; PyInstaller otherwise bundles the
# system libcrypto, which lacks the symbols conda's _ssl needs (OPENSSL_3.x). Detect
# the building Python's own lib dir (conda: <env>/lib) and bundle its libcrypto/libssl
# so the frozen _ssl loads. For a venv/system build there's no such lib dir here, so
# this is a no-op and the system OpenSSL (consistent with that Python) is used.
import sys as _sys
_own_libdir = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(_sys.executable))), "lib"
)
for _ssl_lib in ("libcrypto.so.3", "libssl.so.3"):
    _ssl_path = os.path.join(_own_libdir, _ssl_lib)
    if os.path.isfile(_ssl_path):
        all_binaries.append((_ssl_path, "."))

# The app's own submodules, collected explicitly in case any are lazily imported.
APP_MODULES = [
    "ownvoicereader", "ownvoicereader.cli", "ownvoicereader.config",
    "ownvoicereader.ingest", "ownvoicereader.reader", "ownvoicereader.llm",
    "ownvoicereader.voice", "ownvoicereader.server",
    "ownvoicereader.server.app", "ownvoicereader.server.api",
    "ownvoicereader.server.window",
]

# App data: the viewer (served from _MEIPASS/viewer) + the bundled voice (copied to
# the user cache on first run). Target paths match the frozen resolvers in
# server/app.py (_viewer_dir) and voice/tts_engine.py (_bundled_voice_dir).
APP_DATAS = [
    (VIEWER, "viewer"),
    (VOICE, "voice/models"),
]

MAIN_ENTRY = os.path.join(SPECPATH, "ownvoicereader_entry.py")
PIPER_ENTRY = os.path.join(SPECPATH, "piper_entry.py")

# A GUI icon (Linux/macOS take the PNG; the Windows build swaps in an .ico).
ICON = os.path.join(VIEWER, "imgs", "ownvoicereader-icon-256.png")
ICON = ICON if os.path.isfile(ICON) else None

# --- main app ----------------------------------------------------------------
# A one-dir build: each EXE gets exclude_binaries=True + an empty binaries/datas
# list, and COLLECT gathers the shared binaries/datas (passing them to the EXE too
# is what triggers the "Resource ... is not a valid file" collision).
a = Analysis(
    [MAIN_ENTRY],
    pathex=[REPO],
    binaries=all_binaries,
    datas=all_datas + APP_DATAS,
    hiddenimports=all_hidden + APP_MODULES,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe_main = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="OwnVoiceReader",
    debug=False,
    strip=False,
    upx=False,
    console=CONSOLE,
    icon=ICON,
)

# --- bundled piper TTS subprocess --------------------------------------------
# A *separate* Analysis (its own PYZ + EXE) so its entry runs its own script. The
# two-exes-from-one-analysis pattern mis-assigns entries under PyInstaller 6, so the
# piper exe ran the main app. Separate analyses avoid that. Gated behind
# OV_BUNDLE_PIPER so a main-only build (system piper via PATH) can be validated
# faster; the release build sets it to 1 (default) for a self-contained binary.
extra_exes = []
if os.environ.get("OV_BUNDLE_PIPER", "1") == "1":
    ap = Analysis(
        [PIPER_ENTRY],
        pathex=[REPO],
        binaries=all_binaries,
        datas=all_datas,
        hiddenimports=all_hidden + ["piper", "piper.__main__", "onnxruntime"],
        hookspath=[],
        runtime_hooks=[],
        excludes=[],
        noarchive=False,
    )
    pyzp = PYZ(ap.pure)
    exe_piper = EXE(
        pyzp,
        ap.scripts,
        [],
        exclude_binaries=True,
        name="piper",
        debug=False,
        strip=False,
        upx=False,
        console=True,
    )
    extra_exes = [exe_piper]

# One-dir: the main exe (+ optional piper) + the shared binaries/datas.
coll = COLLECT(
    exe_main,
    *extra_exes,
    a.binaries,
    a.datas,
    name="OwnVoiceReader",
    strip=False,
    upx=False,
    contents_directory=".",
)

# --- trim Qt data the built-in PySide6 hook pulled in but a Widgets webview never
# --- loads. These come from PyInstaller's PySide6 hook (not collect_all), so they're
# --- removed post-COLLECT from the onedir (a glob finds them regardless of the
# --- PyInstaller onedir layout — _internal/ in 6.x).
import shutil as _shutil
import glob as _glob

_onedir = os.path.join(DISTPATH, "OwnVoiceReader")

# Qt/qml: a Qt*Widgets* webview (QtWebEngineWidgets, what pywebview's Qt backend
# uses) never loads the QML/Quick data — confirmed: the window renders without it.
for _qt in _glob.glob(os.path.join(_onedir, "**", "PySide6", "Qt", "qml"), recursive=True):
    if os.path.isdir(_qt):
        _mb = sum(os.path.getsize(os.path.join(r, f))
                  for r, _d, fs in os.walk(_qt) for f in fs) / 1e6
        _shutil.rmtree(_qt, ignore_errors=True)
        print(f"trimmed PySide6/Qt/qml ({_mb:.0f} MB)")

# Qt/translations: trim the Qt UI *.qm files but KEEP qtwebengine_locales/ —
# Chromium's locale .pak files, which QtWebEngine loads at start (dropping it makes
# the webview log a benign "could not find translation file for en-US.pak" warning).
for _tr in _glob.glob(os.path.join(_onedir, "**", "PySide6", "Qt", "translations"), recursive=True):
    _removed = 0
    for _qm in _glob.glob(os.path.join(_tr, "*.qm")):
        try:
            os.remove(_qm)
            _removed += 1
        except OSError:
            pass
    if _removed:
        print(f"trimmed {_removed} Qt UI .qm files from PySide6/Qt/translations")
