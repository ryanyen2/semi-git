"""The layer rule, enforced (Phase 2).

sgt is built as a stack: the store and kernel underneath, `sgt.api` as the one canonical projection
over them, and the surfaces (CLI, TUI, MCP) on top rendering that projection. The rule that keeps it
honest is one-directional: **nothing below may import from a surface above it.**

This is worth a test rather than a convention because the violation is easy to introduce and
invisible once it works. While adding `show_view`, `sgt/api.py` grew a `from sgt.cli.ideal_edit
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
_PKG = _ROOT / "sgt"

# layer -> the packages it must never import from. Read as "sgt.api must not import sgt.cli/tui/mcp".
_FORBIDDEN = {
    "api.py": ("sgt.cli", "sgt.tui", "sgt.mcp"),
    "core": ("sgt.cli", "sgt.tui", "sgt.mcp", "sgt.api"),
    "store": ("sgt.cli", "sgt.tui", "sgt.mcp", "sgt.api", "sgt.core", "sgt.lens"),
    "entities": ("sgt.cli", "sgt.tui", "sgt.mcp", "sgt.api"),
    "select": ("sgt.cli", "sgt.tui", "sgt.mcp", "sgt.api"),
}


def _imported_modules(path: pathlib.Path) -> set[str]:
    """Every module named by an `import x` / `from x import y` anywhere in the file -- including
    inside functions, which is where most of sgt's imports live."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
    return found


# Known inversions, each one a bug that is allowlisted rather than hidden: the rule stays enforced
# for everything else, and removing an entry here is the definition of "fixed".
#
# `api.py -> sgt.tui.graph`: `_project_land_preview`/`_project_feature_preview` call
# `render_collab_preview_lines` to precompute terminal-rendered strings into the projection's
# `summary` field. So the canonical projection contains presentation, which means the VS Code webview
# and MCP receive text laid out for a terminal. The fix is to move the rendering up into the CLI/TUI
# and have `summary` carry structured rows -- deferred because `summary` is consumed by the
# consequence pane, `so_what_for`, and `confirm_collab`, so it is a real refactor of the collab
# confirm path rather than a mechanical move.
_KNOWN_INVERSIONS = {("api.py", "sgt.tui.graph")}


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
                    if (target, module) in _KNOWN_INVERSIONS:
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
    """The other half of the rule: the surfaces are expected to depend on `sgt.api`. If a surface
    stopped importing it, that is the signal it has started deriving views of its own."""
    for surface in ("cli", "mcp"):
        imports: set[str] = set()
        for path in _files_for(surface):
            imports |= _imported_modules(path)
        assert any(m == "sgt.api" or m.startswith("sgt.api.") for m in imports), (
            f"sgt/{surface} no longer imports sgt.api — it is deriving its own projection"
        )
