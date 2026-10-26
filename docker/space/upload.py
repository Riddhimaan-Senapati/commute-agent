"""Upload the files the Space build needs to the Hugging Face Space named by HF_SPACE_ID.

The Space repo gets a README with the Spaces YAML header (Docker SDK, port 7860) in front of
the project README. Files that the Space no longer needs are deleted from it.

    HF_TOKEN=... HF_SPACE_ID=user/commute-agent python docker/space/upload.py
"""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from huggingface_hub import HfApi

ROOT = Path(__file__).resolve().parents[2]
# Everything the Dockerfile copies, plus the Dockerfile itself.
PATHS = (
    "Dockerfile",
    ".dockerignore",
    "pyproject.toml",
    "uv.lock",
    "commute_agent",
    "web",
    "data",
    "webapp",
    "docker/space",
)
SPACE_HEADER = """---
title: Pareto Commute
emoji: 🚲
colorFrom: blue
colorTo: green
sdk: docker
app_port: 7860
pinned: false
short_description: Pareto-optimal commutes in Boston, Cambridge, Somerville, Brookline
---

"""


def main() -> None:
    space_id = os.environ["HF_SPACE_ID"]
    tracked = subprocess.run(
        ["git", "ls-files", "--", *PATHS], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.split()
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        for name in tracked:
            dest = out / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, dest)
        readme = (ROOT / "README.md").read_text()
        (out / "README.md").write_text(SPACE_HEADER + readme)
        HfApi(token=os.environ["HF_TOKEN"]).upload_folder(
            repo_id=space_id,
            repo_type="space",
            folder_path=out,
            commit_message=f"Deploy {os.environ.get('GITHUB_SHA', 'local')[:7]}",
            delete_patterns="*",
        )
    print(f"uploaded {len(tracked) + 1} files to {space_id}")


if __name__ == "__main__":
    main()
