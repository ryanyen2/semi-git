#!/usr/bin/env python3
"""Build the three fixture repos the sgt-agent evals run against.

Each is a real sgt-tracked git repo, built the same way every time, so a with-skill run and its
baseline see byte-identical starting conditions. Run from the repo root:

    python sgt-agent-workspace/make_fixtures.py <dest-dir>
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

FETCH_V1 = '''import time


def fetch(url):
    """Fetch a URL once."""
    return _get(url)


def _get(url):
    return {"url": url, "status": 200}
'''

FETCH_V2 = '''import time


def fetch(url):
    """Fetch a URL, retrying with backoff."""
    for attempt in range(3):
        try:
            return _get(url)
        except OSError:
            time.sleep(backoff(attempt))
    raise OSError(f"giving up on {url}")


def backoff(attempt):
    """Exponential delay between retries."""
    return 2 ** attempt


def _get(url):
    return {"url": url, "status": 200}
'''

CACHE = '''_STORE = {}


def get_cached(key):
    return _STORE.get(key)


def set_cached(key, value):
    _STORE[key] = value
'''


def _git(repo, *args, check=True):
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=check)


def _sgt(repo, *args):
    return subprocess.run([sys.executable, "-m", "sgt.cli", *args], cwd=str(repo),
                          capture_output=True, text=True)


def _init(repo: pathlib.Path):
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "fixture@example.com")
    _git(repo, "config", "user.name", "Fixture")
    return repo


def _commit(repo, message):
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", message)


def build_healthy(dest: pathlib.Path) -> pathlib.Path:
    """A normal repo with a few saves: fetch grows a retry/backoff path, plus a cache module."""
    repo = _init(dest / "healthy")
    (repo / "fetch.py").write_text(FETCH_V1, encoding="utf-8")
    _commit(repo, "add fetch")
    (repo / "cache.py").write_text(CACHE, encoding="utf-8")
    _commit(repo, "add a tiny cache")
    (repo / "fetch.py").write_text(FETCH_V2, encoding="utf-8")
    _commit(repo, "retry with backoff")
    _sgt(repo, "init")
    _sgt(repo, "log", "--tree")  # build the feature tree so labels/handles exist
    return repo


def build_paused_merge(dest: pathlib.Path) -> pathlib.Path:
    """Same repo, left mid-conflicted `git merge`. sgt cannot record anything in this state."""
    repo = _init(dest / "paused-merge")
    (repo / "fetch.py").write_text(FETCH_V1, encoding="utf-8")
    _commit(repo, "add fetch")
    _sgt(repo, "init")

    _git(repo, "checkout", "-q", "-b", "side")
    (repo / "fetch.py").write_text(FETCH_V1.replace('"status": 200', '"status": 201'), encoding="utf-8")
    _commit(repo, "side: status 201")
    _git(repo, "checkout", "-q", "-")
    (repo / "fetch.py").write_text(FETCH_V1.replace('"status": 200', '"status": 202'), encoding="utf-8")
    _commit(repo, "main: status 202")
    _git(repo, "merge", "side", check=False)  # conflicts on purpose; leaves MERGE_HEAD set
    return repo


def main() -> int:
    dest = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/sgt-agent-fixtures").resolve()
    dest.mkdir(parents=True, exist_ok=True)
    for build in (build_healthy, build_paused_merge):
        repo = build(dest)
        print(f"built {repo}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
