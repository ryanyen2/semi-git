"""Semantic blame: rendered lines map back to the feature node that authored them."""

from sgt.effects.attribute import attribute
from sgt.effects.model import Effect
from sgt.project import Project
from sgt.store.graph import Node, NodeKind


def _spans(proj, file):
    return {(s.start, s.end): s.node_id for s in attribute(proj)[file]}


def test_two_defs_attribute_to_their_nodes(tmp_path):
    proj = Project.init(tmp_path)
    proj.add_feature(
        Node(id="alpha", kind=NodeKind.CAPABILITY, intent="alpha"),
        [Effect.add_def("m.py", "alpha", "def alpha():\n    return 1\n    # body")],
    )
    proj.add_feature(
        Node(id="beta", kind=NodeKind.CAPABILITY, intent="beta"),
        [Effect.add_def("m.py", "beta", "def beta():\n    return 2")],
    )
    src = proj.materialize()["m.py"]
    spans = attribute(proj)["m.py"]
    # both nodes own lines; only blank separators between units may be unattributed
    owners = {s.node_id for s in spans}
    assert {"alpha", "beta"} <= owners
    assert owners <= {"alpha", "beta", None}
    # the line carrying `alpha`'s def header belongs to alpha
    lines = src.splitlines()
    alpha_line = next(i for i, l in enumerate(lines, 1) if "def alpha" in l)
    beta_line = next(i for i, l in enumerate(lines, 1) if "def beta" in l)
    owner_of = {}
    for s in spans:
        for ln in range(s.start, s.end + 1):
            owner_of[ln] = s.node_id
    assert owner_of[alpha_line] == "alpha"
    assert owner_of[beta_line] == "beta"


def test_import_and_const_attribution(tmp_path):
    proj = Project.init(tmp_path)
    proj.add_feature(
        Node(id="cfg", kind=NodeKind.CAPABILITY, intent="config"),
        [
            Effect.add_def("m.py", "LIMIT_HOLDER", "def LIMIT_HOLDER():\n    LIMIT = 10\n    return LIMIT"),
            Effect.add_import("m.py", "import os"),
        ],
    )
    src = proj.materialize()["m.py"]
    spans = attribute(proj)["m.py"]
    owner_of = {}
    for s in spans:
        for ln in range(s.start, s.end + 1):
            owner_of[ln] = s.node_id
    import_line = next(i for i, l in enumerate(src.splitlines(), 1) if l.strip() == "import os")
    assert owner_of[import_line] == "cfg"


def test_module_binding_attribution(tmp_path):
    """A module-level `_RE = re.compile(...)` line blames to the node that authored it."""
    proj = Project.init(tmp_path)
    proj.add_feature(
        Node(id="re_owner", kind=NodeKind.CAPABILITY, intent="regex"),
        [
            Effect.add_import("m.py", "import re"),
            Effect.add_assign("m.py", "_RE", "_RE = re.compile('x')"),
            Effect.add_def("m.py", "use", "def use(s):\n    return _RE.match(s)"),
        ],
    )
    src = proj.materialize()["m.py"]
    owner_of = {}
    for s in attribute(proj)["m.py"]:
        for ln in range(s.start, s.end + 1):
            owner_of[ln] = s.node_id
    assign_line = next(i for i, l in enumerate(src.splitlines(), 1) if l.startswith("_RE = "))
    assert owner_of[assign_line] == "re_owner"


def test_attribution_is_stable_across_calls(tmp_path):
    proj = Project.init(tmp_path)
    proj.add_feature(
        Node(id="x", kind=NodeKind.CAPABILITY, intent="x"),
        [Effect.add_def("m.py", "x", "def x():\n    return 1")],
    )
    assert _spans(proj, "m.py") == _spans(proj, "m.py")


def test_reverted_node_drops_out_of_blame(tmp_path):
    proj = Project.init(tmp_path)
    proj.add_feature(
        Node(id="keep", kind=NodeKind.CAPABILITY, intent="keep"),
        [Effect.add_def("m.py", "keep", "def keep():\n    return 1")],
    )
    proj.add_feature(
        Node(id="gone", kind=NodeKind.CAPABILITY, intent="gone"),
        [Effect.add_def("g.py", "gone", "def gone():\n    return 2")],
    )
    proj.remove_nodes({"gone"})
    out = attribute(proj)
    assert "g.py" not in out
    assert {s.node_id for s in out["m.py"]} == {"keep"}
