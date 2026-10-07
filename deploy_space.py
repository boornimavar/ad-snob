"""Publish the team gallery to a free Hugging Face Static Space.

First log in once (it asks for a token from huggingface.co/settings/tokens):
    .venv\\Scripts\\hf auth login
Then:
    .venv\\Scripts\\python.exe deploy_space.py YOUR-USERNAME/creative-lens

It uploads gallery.html as the Space's page, plus a config.js holding the two
PUBLIC Supabase values from .env (SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY), so
the page can read the team library. Secret keys are refused.
"""
import base64
import json
import os
import sys
from pathlib import Path

from huggingface_hub import CommitOperationAdd, HfApi

import collector

ROOT = Path(__file__).resolve().parent


class DeployError(Exception):
    pass


def _jwt_role(key):
    try:
        payload = key.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))).get("role")
    except Exception:
        return None


def public_config(url, key):
    """config.js for the gallery; refuses anything that isn't a public key."""
    if not url or not key:
        raise DeployError("SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY must both be filled in in .env.")
    if key.startswith("sb_secret_") or _jwt_role(key) == "service_role":
        raise DeployError("SUPABASE_PUBLISHABLE_KEY holds a SECRET key. Use the publishable (or anon) key: "
                          "the gallery page is public, so a secret key there would let anyone change the library.")
    config = {"supabaseUrl": url.rstrip("/"), "publishableKey": key}
    return f"window.CREATIVE_LENS = {json.dumps(config)};\n"


def main(argv):
    if len(argv) != 1 or "/" not in argv[0]:
        print(__doc__)
        return 1
    space = argv[0]
    collector.load_settings(ROOT / ".env")
    try:
        config_js = public_config(os.environ.get("SUPABASE_URL", ""), os.environ.get("SUPABASE_PUBLISHABLE_KEY", ""))
    except DeployError as exc:
        print(f"Not uploaded: {exc}")
        return 1
    operations = [
        CommitOperationAdd(path_in_repo="index.html", path_or_fileobj=str(ROOT / "gallery.html")),
        CommitOperationAdd(path_in_repo="config.js", path_or_fileobj=config_js.encode("utf-8")),
        CommitOperationAdd(path_in_repo="README.md", path_or_fileobj=str(ROOT / "space_README.md")),
    ]
    HfApi().create_commit(repo_id=space, repo_type="space", operations=operations,
                          commit_message="Update Creative Lens gallery")
    print(f"Published: https://huggingface.co/spaces/{space}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
