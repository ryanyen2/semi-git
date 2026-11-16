"""Deterministic code-entity layer: the connected spine the time-aware map is built on.

`ugh` versions *features* (effect bundles). This package adds the orthogonal, read-only
*entity* view: functions/classes/methods parsed straight from the working tree with
tree-sitter, connected by containment + calls/imports. It never authors code; features paint
onto these entities as a colored overlay (see ``ugh/api.py``).
"""

from __future__ import annotations

from ugh.entities.extract import Entity, extract_codebase, extract_file

__all__ = ["Entity", "extract_file", "extract_codebase"]
