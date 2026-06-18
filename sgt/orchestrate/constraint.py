"""The transient constraint graph — execution scaffolding for a fan-out run.

A single intent decomposes into sub-tasks, each declaring the names it `provides`
and the names it `needs`, plus optional explicit `depends_on` edges. The graph is
*not* the durable semantic DAG (plan KTD1): it only orders and groups dispatch.
`layers()` returns coordination-free batches (Kahn-style), so each layer can be
dispatched concurrently and dependents run after their prerequisites land.

Pure data + algorithm: no LLM, no git, no `SemanticGraph`.
"""

from __future__ import annotations

from dataclasses import dataclass, field


class ConstraintError(Exception):
    """Raised when the constraint graph cannot be layered (e.g. a dependency cycle)."""


@dataclass
class SubTask:
    key: str                                   # stable handle within this run
    intent: str                                # what to ask the coding agent for
    provides: list[str] = field(default_factory=list)   # names this task will define
    needs: list[str] = field(default_factory=list)      # names it requires from others
    depends_on: list[str] = field(default_factory=list)  # explicit sub-task keys


class ConstraintGraph:
    def __init__(self) -> None:
        self._tasks: dict[str, SubTask] = {}

    def add(self, task: SubTask) -> SubTask:
        if task.key in self._tasks:
            raise ConstraintError(f"duplicate sub-task key: {task.key!r}")
        self._tasks[task.key] = task
        return task

    def tasks(self) -> list[SubTask]:
        return list(self._tasks.values())

    def get(self, key: str) -> SubTask:
        return self._tasks[key]

    def __len__(self) -> int:
        return len(self._tasks)

    def add_dependency(self, src_key: str, dst_key: str) -> None:
        """Record that ``src_key`` depends on ``dst_key`` (supports reshape, R31)."""
        if src_key not in self._tasks or dst_key not in self._tasks:
            raise ConstraintError(f"unknown sub-task in dependency {src_key!r}->{dst_key!r}")
        if src_key == dst_key:
            return
        dep = self._tasks[src_key].depends_on
        if dst_key not in dep:
            dep.append(dst_key)

    def _effective_deps(self) -> dict[str, set[str]]:
        """Explicit depends_on plus edges inferred from needs<->provides matching."""
        provider: dict[str, str] = {}
        for t in self._tasks.values():
            for name in t.provides:
                provider.setdefault(name, t.key)
        deps: dict[str, set[str]] = {}
        for t in self._tasks.values():
            d = {k for k in t.depends_on if k in self._tasks and k != t.key}
            for name in t.needs:
                owner = provider.get(name)
                if owner and owner != t.key:
                    d.add(owner)
            deps[t.key] = d
        return deps

    def layers(self) -> list[list[SubTask]]:
        """Coordination-free batches, dependencies-first (Kahn's algorithm).

        Each returned layer contains tasks whose dependencies all resolved in earlier
        layers; tasks within a layer are independent and may dispatch concurrently.
        """
        deps = self._effective_deps()
        resolved: set[str] = set()
        layers: list[list[SubTask]] = []
        remaining = set(self._tasks)
        while remaining:
            ready = sorted(k for k in remaining if deps[k] <= resolved)
            if not ready:
                raise ConstraintError(
                    f"dependency cycle among sub-tasks: {sorted(remaining)}"
                )
            layers.append([self._tasks[k] for k in ready])
            resolved |= set(ready)
            remaining -= set(ready)
        return layers
