"""The ugh terminal UI (Textual). Optional — install with ``ugh[tui]``.

Kept behind an extra and lazily imported (the CLI launches the consequence pane only when a
mutating verb runs on a tty) so the core ``ugh`` install stays dependency-light. The UI is a thin
shell over the same ``ugh.api`` projection the CLI and the VSCode extension consume.
"""
