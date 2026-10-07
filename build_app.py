"""Build the Windows app: dist/Creative-Lens-Windows.zip.

    .venv\\Scripts\\python.exe build_app.py

Packs demo.py (the dashboard app) with Python, every library, the CLIP model
weights, the ranking models and the parts of Tesseract that OCR needs, so the
laptop that runs it needs nothing installed. Takes several minutes; the zip
is about 1 GB.
"""
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TESSERACT_DIR = Path(r"C:\Program Files\Tesseract-OCR")
CLIP_WEIGHTS = Path.home() / ".cache" / "clip" / "ViT-B-32.pt"
STAGING = ROOT / "build" / "staging"
APP_NAME = "Creative Lens"
ZIP_PATH = ROOT / "dist" / "Creative-Lens-Windows.zip"

START_HERE = """Creative Lens

1. Keep this whole folder together (don't move the .exe out of it).
2. Double-click "Creative Lens.exe".
3. If Windows says "Windows protected your PC": click More info, then Run anyway.
4. The dashboard opens in your browser after a few seconds.

Your searches and saved ads are kept in %LOCALAPPDATA%\\Creative Lens.
The app stops by itself 15 minutes after you close the dashboard.
"""


def stage_tesseract():
    """Only what OCR needs: tesseract.exe, its DLLs and the language data (no training tools)."""
    target = STAGING / "tesseract"
    shutil.rmtree(target, ignore_errors=True)
    (target / "tessdata").mkdir(parents=True)
    shutil.copy2(TESSERACT_DIR / "tesseract.exe", target)
    for dll in TESSERACT_DIR.glob("*.dll"):
        shutil.copy2(dll, target)
    shutil.copytree(TESSERACT_DIR / "tessdata", target / "tessdata", dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("*.jar"))
    return target


def check_inputs():
    missing = [str(p) for p in (TESSERACT_DIR / "tesseract.exe", CLIP_WEIGHTS,
                                ROOT / "design_classifier.pkl", ROOT / "rank_model_v1.pkl", ROOT / "dashboard.html")
               if not p.exists()]
    if missing:
        sys.exit("Missing, can't build:\n  " + "\n  ".join(missing))


def torchvision_compiled_ops():
    """torchvision's *_stable.pyd files: loaded by file path at start-up, so they are
    never seen as imports and must be added by hand."""
    import torchvision
    pyds = sorted(Path(torchvision.__file__).parent.glob("*.pyd"))
    if not pyds:
        sys.exit("torchvision's compiled .pyd files not found")
    return pyds


def run_pyinstaller(tesseract):
    sep = ";"  # Windows --add-data separator
    args = [
        sys.executable, "-m", "PyInstaller", str(ROOT / "demo.py"),
        "--name", APP_NAME, "--onedir", "--noconsole", "--noconfirm", "--clean",
        "--distpath", str(ROOT / "dist"), "--workpath", str(ROOT / "build" / "pyinstaller"),
        "--specpath", str(ROOT / "build"),
        "--add-data", f"{ROOT / 'dashboard.html'}{sep}.",
        "--add-data", f"{ROOT / 'design_classifier.pkl'}{sep}models",
        "--add-data", f"{ROOT / 'rank_model_v1.pkl'}{sep}models",
        "--add-data", f"{CLIP_WEIGHTS}{sep}clip",
        "--add-data", f"{tesseract}{sep}tesseract",
        "--collect-all", "clip",  # its tokenizer vocabulary file
        # torchvision 0.29 renamed its compiled ops to _C_stable.pyd, which PyInstaller's hook
        # misses ("operator torchvision::nms does not exist" at start-up without these).
        "--collect-binaries", "torchvision",
        *[arg for pyd in torchvision_compiled_ops() for arg in ("--add-binary", f"{pyd}{sep}torchvision")],
        "--collect-all", "curl_cffi",  # native library used by the Meta collector
        "--collect-submodules", "meta_ads_collector",
        "--hidden-import", "sklearn.linear_model", "--hidden-import", "sklearn.preprocessing",
        "--exclude-module", "matplotlib", "--exclude-module", "IPython", "--exclude-module", "pytest",
        "--exclude-module", "tkinter",
    ]
    subprocess.run(args, check=True)


# Windows paths stop at 260 characters; 130 leaves room for a long Downloads\<zip name>\ folder.
# (Without shorten_deep_paths torch reaches 137; the deepest real program file is ~117.)
MAX_PATH_IN_ZIP = 130


def shorten_deep_paths(app_dir):
    """Libraries ship third-party license notices in very deep folders (torch's
    reach 137 characters), which makes "Extract All" fail in an ordinary
    Downloads folder. Keep the notices, but as one zip per library."""
    for third_party in app_dir.glob("_internal/*.dist-info/licenses/third_party"):
        archive = third_party.with_suffix(".zip")
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in third_party.rglob("*"):
                if path.is_file():
                    zf.write(path, path.relative_to(third_party))
        shutil.rmtree(third_party)
    too_long = [p for p in app_dir.rglob("*") if len(str(Path(APP_NAME) / p.relative_to(app_dir))) > MAX_PATH_IN_ZIP]
    if too_long:
        sys.exit("Paths too long for Windows' Extract All:\n  " + "\n  ".join(str(p) for p in too_long[:10]))


def make_zip():
    app_dir = ROOT / "dist" / APP_NAME
    shorten_deep_paths(app_dir)
    (app_dir / "START HERE.txt").write_text(START_HERE, encoding="utf-8")
    ZIP_PATH.unlink(missing_ok=True)
    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path in sorted(app_dir.rglob("*")):
            if path.is_file():
                zf.write(path, Path(APP_NAME) / path.relative_to(app_dir))
    return ZIP_PATH


def main():
    check_inputs()
    tesseract = stage_tesseract()
    run_pyinstaller(tesseract)
    zip_path = make_zip()
    size = sum(f.stat().st_size for f in (ROOT / "dist" / APP_NAME).rglob("*") if f.is_file())
    print(f"\nBuilt {ROOT / 'dist' / APP_NAME} ({size / 1e9:.2f} GB unpacked)")
    print(f"Zip:  {zip_path} ({zip_path.stat().st_size / 1e9:.2f} GB)")


if __name__ == "__main__":
    main()
