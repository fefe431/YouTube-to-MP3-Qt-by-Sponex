import base64
import json
import os
import sys
import urllib.parse
import urllib.request
from typing import List


def github_put_file(token: str, repo: str, branch: str, path: str, content_bytes: bytes, message: str) -> None:
    url_path = urllib.parse.quote(path.replace("\\", "/"))
    url = f"https://api.github.com/repos/{repo}/contents/{url_path}?branch={branch}"
    payload = {
        "message": message,
        "content": base64.b64encode(content_bytes).decode("ascii"),
        "branch": branch,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="PUT")
    req.add_header("Authorization", f"token {token}")
    req.add_header("Accept", "application/vnd.github+json")
    with urllib.request.urlopen(req) as resp:
        if resp.status not in (200, 201):
            raise RuntimeError(f"GitHub API response {resp.status}: {resp.read().decode('utf-8', 'ignore')}")


def collect_files(root: str) -> List[str]:
    include_files: List[str] = []
    exclude_dirs = {".git", ".venv", "downloads", "tools", "__pycache__"}
    for dirpath, dirnames, filenames in os.walk(root):
        # prune excluded dirs
        dirnames[:] = [d for d in dirnames if d not in exclude_dirs]
        for f in filenames:
            rel = os.path.relpath(os.path.join(dirpath, f), root)
            # skip .pyc and logs
            if rel.endswith((".pyc", ".pyo", ".pyd", ".log")):
                continue
            include_files.append(rel)
    return include_files


def main() -> None:
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    repo = os.environ.get("GITHUB_REPO", "").strip()
    branch = os.environ.get("GITHUB_BRANCH", "main").strip()
    if not token or not repo:
        print("Missing GITHUB_TOKEN or GITHUB_REPO in environment.", file=sys.stderr)
        sys.exit(1)

    files = collect_files(os.getcwd())
    # Ensure .gitignore is included if present
    if ".gitignore" not in files and os.path.isfile(".gitignore"):
        files.append(".gitignore")

    for rel in files:
        with open(rel, "rb") as fh:
            content = fh.read()
        message = f"Add {rel}"
        print(f"Uploading {rel}...")
        github_put_file(token, repo, branch, rel, content, message)
    print("Uploaded all files.")


if __name__ == "__main__":
    main()


