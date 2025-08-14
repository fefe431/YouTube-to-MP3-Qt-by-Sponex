import base64
import json
import os
import sys
import urllib.parse
import urllib.request
from typing import List


def _github_get_sha(token: str, repo: str, branch: str, path: str) -> str:
    url_path = urllib.parse.quote(path.replace("\\", "/"))
    url = f"https://api.github.com/repos/{repo}/contents/{url_path}?ref={branch}"
    req = urllib.request.Request(url, method="GET")
    req.add_header("Authorization", f"token {token}")
    req.add_header("Accept", "application/vnd.github+json")
    try:
        with urllib.request.urlopen(req) as resp:
            if resp.status != 200:
                return ""
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("sha", "")
    except Exception:
        return ""


def github_put_file(token: str, repo: str, branch: str, path: str, content_bytes: bytes, message: str) -> None:
    url_path = urllib.parse.quote(path.replace("\\", "/"))
    url = f"https://api.github.com/repos/{repo}/contents/{url_path}?branch={branch}"
    payload = {
        "message": message,
        "content": base64.b64encode(content_bytes).decode("ascii"),
        "branch": branch,
    }
    sha = _github_get_sha(token, repo, branch, path)
    if sha:
        payload["sha"] = sha
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

    # Optional: set topics/description only when provided
    topics_csv = os.environ.get("GITHUB_TOPICS", "").strip()
    desc = os.environ.get("GITHUB_DESCRIPTION", "").strip()
    homepage = os.environ.get("GITHUB_HOMEPAGE", "").strip()
    if topics_csv or desc or homepage:
        if topics_csv:
            set_repo_topics(token, repo, [t.strip() for t in topics_csv.split(",") if t.strip()])
        if desc or homepage:
            patch_repo_metadata(token, repo, desc, homepage)

    files = collect_files(os.getcwd())
    if ".gitignore" not in files and os.path.isfile(".gitignore"):
        files.append(".gitignore")
    for rel in files:
        with open(rel, "rb") as fh:
            content = fh.read()
        message = f"Add {rel}"
        print(f"Uploading {rel}...")
        github_put_file(token, repo, branch, rel, content, message)
    print("Uploaded all files.")


def set_repo_topics(token: str, repo: str, topics: List[str]) -> None:
    url = f"https://api.github.com/repos/{repo}/topics"
    payload = {"names": topics}
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="PUT")
    req.add_header("Authorization", f"token {token}")
    req.add_header("Accept", "application/vnd.github+json")
    with urllib.request.urlopen(req) as resp:
        if resp.status not in (200, 201):
            raise RuntimeError(f"GitHub API topics response {resp.status}: {resp.read().decode('utf-8','ignore')}")
    print("Updated repository topics.")


def patch_repo_metadata(token: str, repo: str, description: str, homepage: str) -> None:
    url = f"https://api.github.com/repos/{repo}"
    payload = {}
    if description:
        payload["description"] = description
    if homepage:
        payload["homepage"] = homepage
    if not payload:
        return
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="PATCH")
    req.add_header("Authorization", f"token {token}")
    req.add_header("Accept", "application/vnd.github+json")
    with urllib.request.urlopen(req) as resp:
        if resp.status != 200:
            raise RuntimeError(f"GitHub API repo patch response {resp.status}: {resp.read().decode('utf-8','ignore')}")
    print("Updated repository metadata.")


if __name__ == "__main__":
    main()


