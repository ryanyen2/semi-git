"""Every third-party module `sgt/` imports must be declared in pyproject.toml.

This has now broken a release twice. `tree_sitter` and `igraph` were declared as optional extras
while being imported at module scope, so a stock install died on the first command. Then `rich` and
`pydantic` were not declared at all and only worked because a development virtualenv happened to
have them, which CI found and a local run never could.

A developer's virtualenv accumulates packages, so "it imports on my machine" proves nothing about
what a user gets. This walks the AST instead, and asks the installed metadata which distribution
each module belongs to, so the check is on names a user would actually have to install.
"""

from __future__ import annotations

import ast
import pathlib
import sys
from importlib.metadata import packages_distributions

import pytest

_REPO = pathlib.Path(__file__).resolve().parent.parent
_PACKAGE = _REPO / "sgt"
_LOCAL = {"sgt", "tests", "scripts", "experiments"}


def _imported_modules() -> dict[str, str]:
    """Top-level third-party module name -> the first file that imports it."""
    found: dict[str, str] = {}
    for path in sorted(_PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module.split(".")[0]]
            else:
                continue
            for name in names:
                if name and name not in sys.stdlib_module_names and name not in _LOCAL:
                    found.setdefault(name, str(path.relative_to(_REPO)))
    return found


def test_every_third_party_import_is_declared():
    declared = (_REPO / "pyproject.toml").read_text(encoding="utf-8")
    dist_of = packages_distributions()

    undeclared = []
    for module, where in sorted(_imported_modules().items()):
        dists = dist_of.get(module)
        if not dists:
            pytest.skip(f"{module} is not installed, so its distribution name cannot be resolved")
        # A declared name can be written either way round (`tree-sitter` / `tree_sitter`), and
        # pyproject holds it inside a quoted requirement string with a version specifier.
        if not any(f'"{d}' in declared or f'"{d.replace("-", "_")}' in declared for d in dists):
            undeclared.append(f"{module} (from {dists[0]}), imported by {where}")

    assert not undeclared, (
        "third-party imports missing from pyproject.toml:\n  " + "\n  ".join(undeclared)
    )
