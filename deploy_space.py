"""Upload the analysis service to a Hugging Face Space.

First log in once (it asks for a token from huggingface.co/settings/tokens):
    .venv\\Scripts\\hf auth login
Then:
    .venv\\Scripts\\python.exe deploy_space.py YOUR-USERNAME/creative-lens

Hugging Face rebuilds the Space after every upload (about 10 minutes).
Plain `git push` is not used because the Space rejects binary files
(the .pkl models and the collector zip) that aren't stored with Git LFS.
"""
import sys
from pathlib import Path

from huggingface_hub import CommitOperationAdd, HfApi

ROOT = Path(__file__).resolve().parent
FILES = {
    "Dockerfile": "Dockerfile",
    "space_README.md": "README.md",  # the Space reads its settings from README.md
    "requirements.txt": "requirements.txt",
    "analysis_api.py": "analysis_api.py",
    "library.py": "library.py",
    "meta_collection.py": "meta_collection.py",
    "meta_design_scraper_session.py": "meta_design_scraper_session.py",
    "gallery.html": "gallery.html",
    "design_classifier.pkl": "design_classifier.pkl",
    "rank_model_v1.pkl": "rank_model_v1.pkl",
    "meta_ads_collector_source.zip": "meta_ads_collector_source.zip",
}


def main(argv):
    if len(argv) != 1 or "/" not in argv[0]:
        print(__doc__)
        return 1
    space = argv[0]
    api = HfApi()
    operations = [CommitOperationAdd(path_in_repo=target, path_or_fileobj=str(ROOT / source))
                  for source, target in FILES.items()]
    api.create_commit(repo_id=space, repo_type="space", operations=operations,
                      commit_message="Update Creative Lens")
    print(f"Uploaded. Hugging Face is now rebuilding: https://huggingface.co/spaces/{space}")
    print("Watch the 'Logs' tab there; it's ready when the status says Running.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
