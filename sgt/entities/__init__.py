"""Deterministic code-entity layer: the connected spine the time-aware map is built on.

`sgt` versions *features* (effect bundles). This package adds the orthogonal, read-only
*entity* view: functions/classes/methods parsed straight from the working tree with
tree-sitter, connected by containment + calls/imports. It never authors code and never
touches the effect log — features paint onto these entities as a colored overlay (see
``sgt/effects/attribute.py`` + ``sgt/api.py``).
"""

from __future__ import annotations

from sgt.entities.extract import Entity, extract_codebase, extract_file

__all__ = ["Entity", "extract_file", "extract_codebase"]
