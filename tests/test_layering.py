"""The layer rule, enforced (Phase 2).

ugh is built as a stack: the store and kernel underneath, `ugh.api` as the one canonical projection
over them, and the surfaces (CLI, MCP) on top rendering that projection. The rule that keeps it
honest is one-directional: **nothing below may import from a surface above it.**

This is worth a test rather than a convention because the violation is easy to introduce and
invisible once it works. While adding `show_view`, `ugh/api.py` grew a `from ugh.cli.ideal_edit
import _save_of` -- it ran fine, and it silently inverted the stack: the projection every surface is
supposed to read became dependent on one particular surface's private helper, so MCP and the
extension would have been importing CLI internals to render a view. A grep-shaped test catches that
at the moment it appears.

Deliberately checked by reading source rather than by importing: an import-time check would only see
modules that happen to be loaded, and most of these imports are function-local (done for startup
latency), which no import-graph inspection would reach.
"""

from __future__ import annotations

import ast
import pathlib

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_PKG = _ROOT / "ugh"

# layer -> the packages it must never import from. Read as "ugh.api must not import ugh.cli/mcp".
_FORBIDDEN = {
    "api.py": ("ugh.cli", "ugh.mcp"),
    "core": ("ugh.cli", "ugh.mcp", "ugh.api"),
    "store": ("ugh.cli", "ugh.mcp", "ugh.api", "ugh.core", "ugh.lens"),
    "entities": ("ugh.cli", "ugh.mcp", "ugh.api"),
    "select": ("ugh.cli", "ugh.mcp", "ugh.api"),
}


def _imported_modules(path: pathlib.Path) -> set[str]:
    """Every module named by an `import x` / `from x import y` anywhere in the file -- including
    inside functions, which is where most of ugh's imports live."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
    return found


def _files_for(target: str) -> list[pathlib.Path]:
    node = _PKG / target
    return [node] if node.is_file() else sorted(node.rglob("*.py"))


def test_lower_layers_never_import_a_surface():
    violations: list[str] = []
    for target, forbidden in _FORBIDDEN.items():
        for path in _files_for(target):
            for module in _imported_modules(path):
                for banned in forbidden:
                    if module != banned and not module.startswith(banned + "."):
                        continue
                    rel = path.relative_to(_ROOT)
                    violations.append(f"{rel} imports {module} (forbidden for {target})")
    assert not violations, (
        "layer inversion — a lower layer is importing a surface above it:\n  "
        + "\n  ".join(sorted(violations))
        + "\n\nIf a surface has logic a lower layer needs, move the logic down (into the projection "
          "or the kernel), don't import upward: every other surface has to be able to reach it too."
    )


def test_surfaces_read_the_projection_rather_than_reimplementing_it():
    """The other half of the rule: the surfaces are expected to depend on `ugh.api`. If a surface
    stopped importing it, that is the signal it has started deriving views of its own."""
    for surface in ("cli", "mcp"):
        imports: set[str] = set()
        for path in _files_for(surface):
            imports |= _imported_modules(path)
        assert any(m == "ugh.api" or m.startswith("ugh.api.") for m in imports), (
            f"ugh/{surface} no longer imports ugh.api — it is deriving its own projection"
        )
