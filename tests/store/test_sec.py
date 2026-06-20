"""Strong Eventual Consistency of materialization.

These are the guard the whole local-first design rests on: two replicas that have observed
the same set of effects must materialize identically, and a causal dependency must never be
replayed before the thing it depends on — regardless of the order entries were appended.
"""

from __future__ import annotations

from sgt.effects.model import Effect
from sgt.project import Project
from sgt.store.clock import VersionVector
from sgt.store.graph import Node, NodeKind
from sgt.store.oplog import LogEntry


def _vv(**kw):
    return VersionVector(dict(kw))


def _seed(proj, node_id, eid, effect, vv):
    """Inject a node + a single stamped log entry as if authored elsewhere."""
    if not proj.graph.has(node_id):
        proj.graph.add_node(Node(id=node_id, kind=NodeKind.CAPABILITY, intent=node_id))
    proj.log.append(LogEntry(eid=eid, node_id=node_id, effect=effect,
                             author=eid.split(":")[0], vv=vv))


def _build(append_order):
    """Fresh project; append the given (node, eid, effect, vv) records in `append_order`."""
    import tempfile
    proj = Project.init(tempfile.mkdtemp())
    for rec in append_order:
        _seed(proj, *rec)
    return proj


def test_concurrent_independent_effects_converge_regardless_of_append_order():
    a = ("A", "R1:0", Effect.add_def("a.py", "f", "def f():\n    return 1"), _vv(R1=1))
    b = ("B", "R2:0", Effect.add_def("b.py", "g", "def g():\n    return 2"), _vv(R2=1))
    p1 = _build([a, b])
    p2 = _build([b, a])
    assert p1.materialize() == p2.materialize()      # SEC: identical state
    assert p1.active_effects() == p2.active_effects()  # identical replay sequence


def test_causal_dependency_replays_after_its_dependency():
    # R2 observed R1's `base` before authoring `consumer` (vv dominates), so base must
    # replay first even when its entry is appended last.
    base = ("BASE", "R1:0", Effect.add_def("a.py", "base", "def base():\n    return 1"),
            _vv(R1=1))
    consumer = ("USE", "R2:0",
                Effect.add_def("a.py", "consumer", "def consumer():\n    return base()"),
                _vv(R1=1, R2=1))
    p = _build([consumer, base])  # appended out of causal order on purpose
    src = p.materialize()["a.py"]
    assert src.index("def base") < src.index("def consumer")
    # and it is a valid replay regardless of append order
    assert _build([base, consumer]).materialize() == p.materialize()


def test_merge_is_order_independent_for_three_replicas():
    recs = [
        ("A", "R1:0", Effect.add_def("a.py", "f", "def f():\n    return 1"), _vv(R1=1)),
        ("B", "R2:0", Effect.add_def("b.py", "g", "def g():\n    return 2"), _vv(R2=1)),
        ("C", "R3:0", Effect.add_import("a.py", "import os"), _vv(R3=1)),
    ]
    import itertools
    materializations = {
        tuple(sorted(_build(list(perm)).materialize().items()))
        for perm in itertools.permutations(recs)
    }
    assert len(materializations) == 1  # every append order converges to one state
