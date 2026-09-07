#!/usr/bin/env python3
"""Commit freshly scraped .ics files back to the GitHub repo.

Keeps the GitHub Pages URLs working after the scraping itself has moved
to Railway, so existing calendar subscriptions do not have to change.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

WORK_DIR = Path(os.environ.get("GIT_WORK_DIR", "/data/repo"))


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, "").strip() or default


def _run(args, cwd=None, token=None):
    """Run a git command, keeping the token out of logs and exceptions."""
    proc = subprocess.run(
        args, cwd=cwd, capture_output=True, text=True, timeout=180
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        if token:
            detail = detail.replace(token, "***")
        raise RuntimeError(f"git {' '.join(args[1:3])} failed: {detail}")
    return proc.stdout.strip()


def configured() -> bool:
    return bool(_env("GITHUB_TOKEN") and _env("GITHUB_REPOSITORY"))


def publish(src_dir, message="Update team calendars") -> str:
    """Copy .ics files into the repo's docs/ and push. Returns a status line."""
    token = _env("GITHUB_TOKEN")
    repo = _env("GITHUB_REPOSITORY")
    branch = _env("GIT_BRANCH", "main")
    subdir = _env("GIT_TARGET_DIR", "docs")

    if not token or not repo:
        return "github push skipped (GITHUB_TOKEN / GITHUB_REPOSITORY not set)"

    remote = f"https://x-access-token:{token}@github.com/{repo}.git"
    src_dir = Path(src_dir)
    sources = sorted(src_dir.glob("*.ics"))
    if not sources:
        return "github push skipped (no .ics files to publish)"

    # A shallow clone per push keeps this stateless: no drift to reconcile
    # if the volume outlives a branch rename or a force-push upstream.
    if WORK_DIR.exists():
        shutil.rmtree(WORK_DIR, ignore_errors=True)
    WORK_DIR.parent.mkdir(parents=True, exist_ok=True)

    _run(
        ["git", "clone", "--depth", "1", "--branch", branch, remote, str(WORK_DIR)],
        token=token,
    )

    target = WORK_DIR / subdir
    target.mkdir(parents=True, exist_ok=True)
    for f in sources:
        shutil.copy2(f, target / f.name)

    _run(["git", "config", "user.name", _env("GIT_AUTHOR_NAME", "calendar-bot")],
         cwd=WORK_DIR)
    _run(["git", "config", "user.email",
          _env("GIT_AUTHOR_EMAIL", "calendar-bot@users.noreply.github.com")],
         cwd=WORK_DIR)
    _run(["git", "add", "--", f"{subdir}"], cwd=WORK_DIR)

    status = _run(["git", "status", "--porcelain"], cwd=WORK_DIR)
    if not status:
        return "github push skipped (no changes)"

    _run(["git", "commit", "-m", message], cwd=WORK_DIR)
    _run(["git", "push", "origin", branch], cwd=WORK_DIR, token=token)

    changed = len([line for line in status.splitlines() if line.strip()])
    return f"github push ok ({changed} file(s) to {repo}@{branch})"


if __name__ == "__main__":
    out = _env("SCRAPER_OUTPUT_DIR", "docs")
    try:
        print(publish(out))
    except Exception as e:
        print(f"github push failed: {e}", file=sys.stderr)
        sys.exit(1)
