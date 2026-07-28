"""The semi-git terminal UI (Textual). Optional — install with ``semi-git[tui]``.

Kept behind an extra and lazily imported (the CLI launches the consequence pane only when a
mutating verb runs on a tty) so the core ``sgt`` install stays dependency-light. The UI is a thin
shell over the same ``sgt.api`` projection the CLI and the VSCode extension consume.
"""
