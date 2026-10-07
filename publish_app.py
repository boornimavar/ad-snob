"""Upload the built app (dist/Creative-Lens-Windows.zip) to Hugging Face.

First log in once (it asks for a token with "Write" access from
huggingface.co/settings/tokens):
    .venv\\Scripts\\hf auth login
Then:
    .venv\\Scripts\\python.exe publish_app.py YOUR-USERNAME/creative-lens-app

It creates the repository if needed (public, so anyone with the link can
download; this build holds no passwords or keys) and prints the download link.
"""
import sys
from pathlib import Path

from huggingface_hub import HfApi

ROOT = Path(__file__).resolve().parent
ZIP_PATH = ROOT / "dist" / "Creative-Lens-Windows.zip"

REPO_README = """---
license: other
tags:
- windows-app
---

# Creative Lens for Windows

Download **Creative-Lens-Windows.zip** (Files and versions tab), extract it,
and double-click **Creative Lens.exe**. If Windows says "Windows protected
your PC", click **More info**, then **Run anyway**. Nothing else needs to be
installed.

Creative Lens searches Meta's public Ad Library from your laptop and shows
the best-designed ads first.
"""


def main(argv):
    if len(argv) != 1 or "/" not in argv[0]:
        print(__doc__)
        return 1
    if not ZIP_PATH.exists():
        print(f"Build the app first: {ZIP_PATH} doesn't exist (run build_app.py).")
        return 1
    repo = argv[0]
    api = HfApi()
    api.create_repo(repo, repo_type="model", private=False, exist_ok=True)
    api.upload_file(path_or_fileobj=REPO_README.encode("utf-8"), path_in_repo="README.md", repo_id=repo,
                    commit_message="Describe the download")
    print(f"Uploading {ZIP_PATH.stat().st_size / 1e9:.2f} GB; this can take a while...")
    api.upload_file(path_or_fileobj=str(ZIP_PATH), path_in_repo=ZIP_PATH.name, repo_id=repo,
                    commit_message="Upload Creative Lens for Windows")
    print("\nDone. Share this download link with your team:")
    print(f"https://huggingface.co/{repo}/resolve/main/{ZIP_PATH.name}?download=true")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
