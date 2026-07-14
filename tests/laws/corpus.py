"""Deterministic corpus for the operation-ideal kernel's round-trip law harness.

The kernel's correctness is defined by the round-trip laws (put-get, get-put, idempotence,
locality, coverage, squash-remine identification, double-machine mining determinism -- plan
docs/plans/2026-07-06-001-feat-operation-ideal-kernel-plan.md R20/R22), not by prose. This module
builds the git repos those laws run against: small synthetic histories exercising the mining edge
cases the plan calls out (rename, cross-file move, a tangled commit, a squash merge, a chain fork,
a non-parseable path, a binary file) via real ``git`` subprocess calls with a pinned author and
fixed commit timestamps, so two independent builds produce byte-identical commit SHAs -- no LLM,
no network, no wall-clock leakage. This mirrors ``tests/golden/corpus.py``'s discipline of
deterministic, offline fixtures, applied to real git history instead of in-memory `Project` state.

A large (>=50k commit) external repo for BET-E (adoption scale) is opt-in only: set
``SGT_LARGE_CORPUS_REPO`` to a local path. This module never clones one itself -- that is a
multi-gigabyte, multi-minute network operation no test run should trigger silently.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

_IDENTITY = {
    "GIT_AUTHOR_NAME": "sgt-corpus",
    "GIT_AUTHOR_EMAIL": "corpus@semi-git.local",
    "GIT_COMMITTER_NAME": "sgt-corpus",
    "GIT_COMMITTER_EMAIL": "corpus@semi-git.local",
}

# Fixed, monotonically increasing commit dates (never `datetime.now()`) so commit SHAs -- which
# fold in author/committer timestamps -- are byte-identical across independent builds.
_BASE_EPOCH = 1_700_000_000


def _at(n: int) -> str:
    return f"{_BASE_EPOCH + n * 3600} +0000"


def _run(repo: Path, *args: str, env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, **_IDENTITY, **(env_extra or {})}
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, env=env
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr}")
    return proc


def _init(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _run(repo, "init", "-q", "-b", "main")
    # Hermetic regardless of the host's global git config -- these are throwaway fixture repos,
    # never the user's own, and a globally-enforced signing key would otherwise hang the suite.
    _run(repo, "config", "commit.gpgsign", "false")
    _run(repo, "config", "user.name", _IDENTITY["GIT_AUTHOR_NAME"])
    _run(repo, "config", "user.email", _IDENTITY["GIT_AUTHOR_EMAIL"])


def _write(repo: Path, path: str, content: str | bytes) -> None:
    full = repo / path
    full.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        full.write_bytes(content)
    else:
        full.write_text(content, encoding="utf-8")


def _remove(repo: Path, path: str) -> None:
    """Delete a tracked file; the next ``_commit``'s ``git add -A`` stages the removal."""
    (repo / path).unlink()


def _commit(repo: Path, message: str, when: int) -> str:
    _run(repo, "add", "-A")
    _run(
        repo,
        "commit",
        "-q",
        "-m",
        message,
        env_extra={"GIT_AUTHOR_DATE": _at(when), "GIT_COMMITTER_DATE": _at(when)},
    )
    return _run(repo, "rev-parse", "HEAD").stdout.strip()


def commit_shas(repo: Path) -> list[str]:
    """Oldest-first commit SHAs on the current branch."""
    out = _run(repo, "log", "--reverse", "--format=%H").stdout
    return [line for line in out.splitlines() if line]


def changed_paths(repo: Path, before: str | None, after: str) -> list[str]:
    base = before if before is not None else "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
    out = _run(repo, "diff", "--name-only", base, after).stdout
    return [line for line in out.splitlines() if line]


def checkout(repo: Path, ref: str) -> None:
    _run(repo, "checkout", "-q", ref)


def tracked_paths(repo: Path, ref: str = "HEAD") -> list[str]:
    out = _run(repo, "ls-tree", "-r", "--name-only", ref).stdout
    return [line for line in out.splitlines() if line]


# --- corpus cases -----------------------------------------------------------------------------


def _case_linear_history(root: Path) -> Path:
    """add -> modify -> rename (same file) -> move (cross file) -> tangled commit (two
    def-use-disjoint symbols in one commit) -> delete -> non-parseable edit -> binary add.
    Exercises U2's tiered identity matcher and whole-file pseudo-symbols end to end."""
    repo = root / "linear_history"
    _init(repo)

    _write(repo, "a.py", "def foo():\n    return 1\n")
    _write(repo, "c.py", "def qux():\n    return 'unrelated'\n")
    _write(repo, "config.yaml", "setting: original\n")
    _write(repo, "logo.bin", bytes([0x89, 0x50, 0x4E, 0x47, 0x00, 0x01, 0x02]))
    _write(repo, "README.md", "# corpus\n")
    _commit(repo, "add foo, qux, config, binary", 0)

    _write(repo, "a.py", "def foo():\n    return 2  # modified body\n")
    _commit(repo, "modify foo", 1)

    _write(repo, "a.py", "def bar():\n    return 2  # modified body\n")
    _commit(repo, "rename foo -> bar within a.py", 2)

    _write(repo, "a.py", "")
    _write(repo, "b.py", "def bar():\n    return 2  # modified body\n")
    _commit(repo, "move bar from a.py to b.py", 3)

    _write(repo, "b.py", "def bar():\n    return 2  # modified body\n\n\ndef baz():\n    return 3\n")
    _write(repo, "c.py", "def qux():\n    return 'changed independently'\n")
    _commit(repo, "tangled: add baz to b.py and edit unrelated qux in c.py", 4)

    _write(repo, "b.py", "def baz():\n    return 3\n")
    _commit(repo, "delete bar", 5)

    _write(repo, "config.yaml", "setting: changed\nextra: true\n")
    _commit(repo, "edit non-parseable config", 6)

    return repo


def _case_squash_merge(root: Path) -> Path:
    """A feature branch (one commit), then squash-merged into main reproducing byte-identical
    final content -- AE1 / the identification law (R8): mining the squash commit must identify
    with the already-mined feature op rather than minting a new one.

    Deliberately a *single* feature commit: mining diffs consecutive commits, so a feature
    branch of N commits mines as N distinct ops (each a smaller step), while a squash always
    collapses to one commit -- one net op. Identification can only match when the feature side
    is itself one commit, so its single op's (footprint, images) is exactly what the squash
    commit re-derives. A multi-commit feature decomposing differently than its own squash is a
    known, separate limitation (per-commit mining can't see that a big diff happens to equal a
    chain of smaller already-known ones) -- not what this fixture is testing.
    """
    repo = root / "squash_merge"
    _init(repo)
    _write(repo, "a.py", "def foo():\n    return 1\n")
    _commit(repo, "base", 0)

    _run(repo, "checkout", "-q", "-b", "feature")
    _write(repo, "a.py", "def foo():\n    return 1\n\n\ndef helper():\n    return 2\n")
    _commit(repo, "feature: add helper", 1)
    feature_final = (repo / "a.py").read_bytes()

    _run(repo, "checkout", "-q", "main")
    # Squash: reproduce the feature branch's final bytes in a single commit on main, the way
    # `git merge --squash` or a GitHub squash-merge would -- same content, one commit, no merge
    # parent linking back to the feature branch's individual commits.
    _write(repo, "a.py", feature_final.decode("utf-8"))
    _commit(repo, "squash-merge feature (add helper)", 2)

    return repo


def _case_diverged_chain(root: Path) -> Path:
    """Two branches independently edit the same function from the same base version -- a
    genuine chain fork (R5's 'only conflict'), fixture for merge-op / pin / transplant tests."""
    repo = root / "diverged_chain"
    _init(repo)
    _write(repo, "slugify.py", "def slugify(s):\n    return s.lower()\n")
    _commit(repo, "base slugify", 0)

    _run(repo, "checkout", "-q", "-b", "release")
    _write(repo, "slugify.py", "def slugify(s):\n    return s.lower().strip()\n")
    _commit(repo, "release: strip in slugify", 1)

    _run(repo, "checkout", "-q", "main")
    _write(repo, "slugify.py", "def slugify(s):\n    return s.lower().replace(' ', '-')\n")
    _commit(repo, "main: dasherize in slugify", 2)

    return repo


def _case_mixed_coverage(root: Path) -> Path:
    """Two parseable Python files (each one def) plus two non-parseable paths (YAML, Markdown),
    all added cleanly in one commit -- a mixed tree with a known, stable entity-granularity
    coverage fraction (2 of 4 covered paths are entity-granular = 0.5). Fixture for R7 /
    `sgt.api.state_view`: config and docs get honest whole-file coverage, code gets entity
    coverage."""
    repo = root / "mixed_coverage"
    _init(repo)
    _write(repo, "pkg.py", "def compute():\n    return 1\n")
    _write(repo, "util.py", "def helper():\n    return 2\n")
    _write(repo, "config.yaml", "setting: value\n")
    _write(repo, "notes.md", "# notes\n")
    _commit(repo, "add mixed python and non-parseable paths", 0)
    return repo


def _case_removed_paths(root: Path) -> Path:
    """A file added with a single top-level entity and then fully ``git rm``'d must vanish from
    both ``code(I)`` and coverage -- its entity's anchor pseudo-symbol is pure ordering metadata
    that mining never revises to BOTTOM, so after the entity and residue are pruned the anchor
    lingers alone at the frontier; it must not keep the path alive as an empty ``b''`` (R7/R20
    get-put fidelity). A sibling file that loses *one* of two top-level entities is the positive
    control: it stays covered and materializes only the surviving entity's exact bytes."""
    repo = root / "removed_paths"
    _init(repo)
    _write(repo, "gone.py", "def gone():\n    return 1\n")
    _write(repo, "survivor.py", "def keep():\n    return 1\n\n\ndef drop():\n    return 2\n")
    _commit(repo, "add gone.py and survivor.py(keep, drop)", 0)

    _remove(repo, "gone.py")
    _write(repo, "survivor.py", "def keep():\n    return 1\n")
    _commit(repo, "rm gone.py entirely; drop `drop` from survivor.py", 1)

    return repo


def _case_revert_to_original(root: Path) -> Path:
    """A function's body changes then reverts to its exact original bytes -- an add/modify/
    revert after-value collision on the same symbol (regression fixture for order.py's
    frontier/is_valid_ideal fix, U7.5)."""
    repo = root / "revert_to_original"
    _init(repo)
    _write(repo, "a.py", "def foo():\n    return 1\n")
    _commit(repo, "add foo", 0)

    _write(repo, "a.py", "def foo():\n    return 2\n")
    _commit(repo, "modify foo", 1)

    _write(repo, "a.py", "def foo():\n    return 1\n")
    _commit(repo, "revert foo to its original body", 2)

    return repo


def _case_crlf_endings(root: Path) -> Path:
    """A Python file using CRLF line endings throughout -- byte-native addressing must slice
    `\\r\\n` exactly, not silently normalize to `\\n` the way a `splitlines()`/`str.join()`
    pipeline does (kernel byte-fidelity audit, 2026-07-08)."""
    repo = root / "crlf_endings"
    _init(repo)
    _write(repo, "a.py", b"def foo():\r\n    return 1\r\n\r\n\r\ndef bar():\r\n    return 2\r\n")
    _commit(repo, "add CRLF file", 0)
    return repo


def _case_no_trailing_newline(root: Path) -> Path:
    """A file with no trailing newline -- the fold must not synthesize one that wasn't there."""
    repo = root / "no_trailing_newline"
    _init(repo)
    _write(repo, "a.py", b"def foo():\n    return 1")
    _commit(repo, "add file with no trailing newline", 0)
    return repo


def _case_formfeed_and_unicode_sep(root: Path) -> Path:
    """A form feed inside a function body and a U+2028 line separator inside a string literal --
    both bytes a line-based differ can silently truncate on, but tree-sitter's byte-native parse
    and a raw-byte slice pass through untouched."""
    repo = root / "formfeed_and_unicode_sep"
    _init(repo)
    _write(
        repo, "a.py",
        "def foo():\n    x = 1\x0c\n    return x\n\n\ndef bar():\n    return \"a b\"\n".encode("utf-8"),
    )
    _commit(repo, "add form-feed and U+2028 content", 0)
    return repo


def _case_latin1_encoded(root: Path) -> Path:
    """A `.py` file that is not valid UTF-8 (latin-1 encoded, e.g. from a legacy codebase) --
    extraction and the fold must round-trip its exact bytes rather than decode-with-replacement,
    which would permanently corrupt the non-ASCII byte into U+FFFD."""
    repo = root / "latin1_encoded"
    _init(repo)
    # b"\xe9" is 'é' in latin-1; invalid as a UTF-8 continuation byte on its own.
    _write(repo, "a.py", b"def foo():\n    return '\xe9'\n")
    _commit(repo, "add latin-1 encoded file", 0)
    return repo


def _case_decorated_routes(root: Path) -> Path:
    """Two top-level decorated functions -- without span-widening, both decorators land in one
    file-top residue blob and materialize piled onto whichever function's image happens to
    render first (silent semantic corruption: a Flask route swap), not a formatting nit."""
    repo = root / "decorated_routes"
    _init(repo)
    _write(
        repo, "routes.py",
        "from framework import app\n\n\n"
        "@app.route(\"/a\")\ndef handle_a():\n    return \"a\"\n\n\n"
        "@app.route(\"/b\")\ndef handle_b():\n    return \"b\"\n",
    )
    _commit(repo, "add two decorated routes", 0)
    return repo


def _case_overload_group(root: Path) -> Path:
    """`@overload` stubs beside their implementation -- all three share the surface id `f`;
    without coalescing, the fold's last-write-wins entity image silently drops the stubs."""
    repo = root / "overload_group"
    _init(repo)
    _write(
        repo, "ov.py",
        "from typing import overload\n\n\n"
        "@overload\ndef f(x: int) -> int: ...\n"
        "@overload\ndef f(x: str) -> str: ...\n"
        "def f(x):\n    return x\n",
    )
    _commit(repo, "add overload group", 0)
    return repo


def _case_property_pair(root: Path) -> Path:
    """A `@property` getter beside its `@x.setter` -- both named `Widget.val`; a nested-entity
    duplicate-id collision, distinct from the top-level overload case."""
    repo = root / "property_pair"
    _init(repo)
    _write(
        repo, "w.py",
        "class Widget:\n"
        "    @property\n    def val(self):\n        return self._v\n\n"
        "    @val.setter\n    def val(self, v):\n        self._v = v\n",
    )
    _commit(repo, "add property getter/setter pair", 0)
    return repo


def _case_class_with_methods(root: Path) -> Path:
    """A class with an `__init__` and two methods, one calling the other -- the corpus's first
    real class fixture (every other case is bare top-level functions); exercises containment,
    nested-entity subsumption, and a call resolved to its owning method rather than the class."""
    repo = root / "class_with_methods"
    _init(repo)
    _write(
        repo, "service.py",
        "class Service:\n"
        "    def __init__(self, name):\n        self.name = name\n\n"
        "    def label(self):\n        return self._format(self.name)\n\n"
        "    def _format(self, name):\n        return name.upper()\n",
    )
    _commit(repo, "add class with methods", 0)
    return repo


def _case_imports_and_main(root: Path) -> Path:
    """A module docstring, imports, a constant, a function, and a trailing `__main__` guard --
    positional residue (head + tail) must place each where it actually sits, not collapse
    everything to one file-top blob (the fold's old, honestly-documented limitation)."""
    repo = root / "imports_and_main"
    _init(repo)
    _write(
        repo, "app.py",
        '"""Module docstring."""\n\nimport os\n\nMAX = 100\n\n\n'
        "def run():\n    return os.getpid() + MAX\n\n\n"
        'if __name__ == "__main__":\n    run()\n',
    )
    _commit(repo, "add module docstring, imports, const, fn, and __main__ guard", 0)
    return repo


def _case_commuting_features(root: Path) -> Path:
    """Two branches from a common base, each inserting a *different* new top-level entity at a
    *different* anchor -- `feature_a` (adds `foo` after `bar`) and `feature_b` (adds `baz` after
    `qux`) never touch each other's insertion point. Fixture for the "anchor-disjoint additions
    commute" law re-stated under the positional-residue segment model: unioning both branches'
    mined ops must materialize all four entities, correctly interleaved, with the original gaps
    (before `bar`, between `bar` and `qux`, after `qux`) intact."""
    repo = root / "commuting_features"
    _init(repo)
    _write(repo, "a.py", "def bar():\n    return 1\n\n\ndef qux():\n    return 2\n")
    _commit(repo, "base: bar, qux", 0)

    _run(repo, "checkout", "-q", "-b", "feature_a")
    _write(
        repo, "a.py",
        "def bar():\n    return 1\n\n\ndef foo():\n    return 3\n\n\ndef qux():\n    return 2\n",
    )
    _commit(repo, "feature_a: insert foo after bar", 1)

    _run(repo, "checkout", "-q", "main")
    _run(repo, "checkout", "-q", "-b", "feature_b")
    _write(
        repo, "a.py",
        "def bar():\n    return 1\n\n\ndef qux():\n    return 2\n\n\ndef baz():\n    return 4\n",
    )
    _commit(repo, "feature_b: insert baz after qux", 1)

    _run(repo, "checkout", "-q", "main")
    return repo


def _case_residue_fork(root: Path) -> Path:
    """Two branches from a common base independently edit the *same* residue segment (an
    import line) differently -- a genuine chain fork on a residue symbol, exactly like an
    entity chain fork, since residue footprints are ordinary (before_version, after_version)
    chains with no special-casing."""
    repo = root / "residue_fork"
    _init(repo)
    _write(repo, "a.py", "import os\n\n\ndef run():\n    return os.getpid()\n")
    _commit(repo, "base: import os", 0)

    _run(repo, "checkout", "-q", "-b", "feature_a")
    _write(repo, "a.py", "import os\nimport sys\n\n\ndef run():\n    return os.getpid()\n")
    _commit(repo, "feature_a: add import sys", 1)

    _run(repo, "checkout", "-q", "main")
    _run(repo, "checkout", "-q", "-b", "feature_b")
    _write(repo, "a.py", "import os\nimport json\n\n\ndef run():\n    return os.getpid()\n")
    _commit(repo, "feature_b: add import json", 1)

    _run(repo, "checkout", "-q", "main")
    return repo


def _case_ts_export_decorated(root: Path) -> Path:
    """TypeScript's decorator/export shapes: a decorated exported class, an exported const
    arrow function, and a class member whose decorator is a *sibling*, not a child or wrapping
    parent -- the grammar shape that top-level Python decorator handling alone would miss."""
    repo = root / "ts_export_decorated"
    _init(repo)
    _write(
        repo, "widget.ts",
        "@Component({selector: 'app'})\n"
        "export class Widget {\n"
        "  @HostListener('click')\n"
        "  onClick() { return 1; }\n"
        "}\n\n"
        "export const submit = (e: Event) => {\n  e.preventDefault();\n};\n",
    )
    _commit(repo, "add TS export/decorator shapes", 0)
    return repo


@dataclass(frozen=True)
class CorpusCase:
    name: str
    build: Callable[[Path], Path]
    description: str


CORPUS: dict[str, CorpusCase] = {
    "linear_history": CorpusCase(
        "linear_history", _case_linear_history,
        "add/modify/rename/move/tangle/delete/non-parseable-edit over one linear history",
    ),
    "squash_merge": CorpusCase(
        "squash_merge", _case_squash_merge,
        "feature branch squash-merged into main reproducing identical final bytes",
    ),
    "diverged_chain": CorpusCase(
        "diverged_chain", _case_diverged_chain,
        "two branches independently edit the same symbol from a shared base -- a chain fork",
    ),
    "mixed_coverage": CorpusCase(
        "mixed_coverage", _case_mixed_coverage,
        "two Python files plus a YAML and a Markdown file -- a known entity-granularity fraction",
    ),
    "removed_paths": CorpusCase(
        "removed_paths", _case_removed_paths,
        "a file fully git-rm'd (must vanish, no anchor-only b'' phantom) alongside a sibling that "
        "loses one of two entities (must stay covered) -- get-put fidelity for removals",
    ),
    "revert_to_original": CorpusCase(
        "revert_to_original", _case_revert_to_original,
        "a function's body changes then reverts to its exact original bytes -- an after-value "
        "collision regression for order.py's frontier/is_valid_ideal",
    ),
    "crlf_endings": CorpusCase(
        "crlf_endings", _case_crlf_endings,
        "a Python file using CRLF line endings throughout -- byte-native addressing must not "
        "normalize to LF",
    ),
    "no_trailing_newline": CorpusCase(
        "no_trailing_newline", _case_no_trailing_newline,
        "a file with no trailing newline -- the fold must not synthesize one",
    ),
    "formfeed_and_unicode_sep": CorpusCase(
        "formfeed_and_unicode_sep", _case_formfeed_and_unicode_sep,
        "a form feed in a function body and a U+2028 separator inside a string literal -- both "
        "survive a byte-native slice, both truncate a line-based one",
    ),
    "latin1_encoded": CorpusCase(
        "latin1_encoded", _case_latin1_encoded,
        "a .py file that is not valid UTF-8 -- extraction/fold must round-trip its exact bytes, "
        "not decode-with-replacement and corrupt them",
    ),
    "decorated_routes": CorpusCase(
        "decorated_routes", _case_decorated_routes,
        "two top-level decorated functions -- without span-widening both decorators pile onto "
        "one function, silent semantic corruption",
    ),
    "overload_group": CorpusCase(
        "overload_group", _case_overload_group,
        "@overload stubs beside their implementation -- all share one surface id; without "
        "coalescing the fold's last-write-wins image silently drops the stubs",
    ),
    "property_pair": CorpusCase(
        "property_pair", _case_property_pair,
        "a @property getter beside its @x.setter -- a nested-entity duplicate-id collision",
    ),
    "class_with_methods": CorpusCase(
        "class_with_methods", _case_class_with_methods,
        "a class with __init__ and two methods, one calling the other -- the corpus's first "
        "real class fixture",
    ),
    "imports_and_main": CorpusCase(
        "imports_and_main", _case_imports_and_main,
        "a module docstring, imports, a constant, a function, and a trailing __main__ guard -- "
        "positional residue must place each where it actually sits",
    ),
    "ts_export_decorated": CorpusCase(
        "ts_export_decorated", _case_ts_export_decorated,
        "TypeScript's decorator/export shapes, including a class member decorator that is a "
        "sibling rather than a child or wrapping parent",
    ),
    "commuting_features": CorpusCase(
        "commuting_features", _case_commuting_features,
        "two branches each insert a different entity at a different anchor -- anchor-disjoint "
        "additions must compose with no shared bytes to disagree about",
    ),
    "residue_fork": CorpusCase(
        "residue_fork", _case_residue_fork,
        "two branches independently edit the same residue segment (an import line) -- a "
        "genuine chain fork on a residue symbol, same as an entity chain fork",
    ),
}

# Fixtures exercising the general-code robustness fixes (kernel byte-fidelity audit,
# 2026-07-08) -- parametrize the byte-fidelity/coverage laws over these in addition to the
# original mining-edge-case fixtures above.
GENERAL_CODE_CASES = [
    "crlf_endings",
    "no_trailing_newline",
    "formfeed_and_unicode_sep",
    "latin1_encoded",
    "decorated_routes",
    "overload_group",
    "property_pair",
    "class_with_methods",
    "imports_and_main",
    "ts_export_decorated",
]


def self_repo_clone(root: Path) -> Path:
    """A local (no-network) clone of this repo itself, for dogfood-scale smoke checks. Not used
    for byte-exact law assertions -- this repo's history is not test-authored, so its content
    isn't pinned the way the synthetic cases above are."""
    src = Path(__file__).resolve().parents[2]
    dest = root / "self_clone"
    subprocess.run(
        ["git", "clone", "--quiet", "--local", str(src), str(dest)],
        check=True, capture_output=True, text=True,
    )
    return dest


# BET-E budgets (R22): provisional numbers, to be recalibrated from real measurement once
# sgt.core exists and `sgt init` can actually run against the large corpus (see the plan's Open
# Questions -- genesis-horizon default is explicitly deferred to that measurement). Encoded as
# numbers now, not prose, per U1's Verification requirement; tightened in U6/U10.
MAX_INIT_SECONDS_PER_1K_COMMITS = 5.0
MAX_STORE_BYTES_PER_COMMIT = 20_000


def large_corpus_repo() -> Path | None:
    """An opt-in >=50k-commit repo for BET-E. Set ``SGT_LARGE_CORPUS_REPO`` to a local clone;
    this module never fetches one on its own."""
    raw = os.environ.get("SGT_LARGE_CORPUS_REPO")
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_dir() else None
