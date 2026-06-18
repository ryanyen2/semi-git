"""The orchestration spine: prompt -> classify -> delegate -> gate -> land/quarantine."""

__all__ = ["Orchestrator", "Report"]


def __getattr__(name):
    # Lazy re-export so importing a sibling (e.g. sgt.orchestrate.constraint) does not
    # eagerly pull in loop.py — which imports sgt.agents.planner, which imports this
    # package (a circular import otherwise).
    if name in __all__:
        from sgt.orchestrate.loop import Orchestrator, Report
        return {"Orchestrator": Orchestrator, "Report": Report}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
