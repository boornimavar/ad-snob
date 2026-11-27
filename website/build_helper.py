"""Pack the Chrome add-on (extension/) into website/downloads/ad-snob-helper.zip,
the file teammates download from the website.

    .venv\\Scripts\\python.exe website\\build_helper.py

Rerun it (and commit the zip) whenever anything in extension/ changes. The zip
is the same bytes every time for the same files, so git only sees real changes.
It refuses to build if the add-on's version is older than the one the website
asks for (NEEDS_HELPER in website/app.js).
"""
import json
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "extension"
OUT = ROOT / "website" / "downloads" / "ad-snob-helper.zip"
FOLDER = "ad-snob-helper"  # what teammates see after unzipping
SKIP = {"meta.test.mjs"}   # tests stay out of the download


def version_tuple(text):
    return tuple(int(x) for x in text.split("."))


def main():
    version = json.loads((SOURCE / "manifest.json").read_text(encoding="utf-8"))["version"]
    app = (ROOT / "website" / "app.js").read_text(encoding="utf-8")
    needed = re.search(r'NEEDS_HELPER = "([\d.]+)"', app).group(1)
    if version_tuple(version) < version_tuple(needed):
        sys.exit(f"extension/manifest.json is {version} but the website needs {needed}: bump the add-on first.")
    files = sorted(p for p in SOURCE.iterdir() if p.is_file() and p.name not in SKIP)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        for p in files:
            info = zipfile.ZipInfo(f"{FOLDER}/{p.name}", date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, p.read_bytes().replace(b"\r\n", b"\n"))
    print(f"wrote {OUT.relative_to(ROOT)} ({OUT.stat().st_size // 1024} KB): add-on {version}, "
          f"{len(files)} files: {', '.join(p.name for p in files)}")


if __name__ == "__main__":
    main()
