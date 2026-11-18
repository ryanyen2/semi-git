"""What the project's check observed on one commit's tree: a save's second footprint
(docs/design/2026-11-16-removal-sets.md §1).

An observation is the check's exit code, its output (stdout then stderr) and, when it ran Python 3.12
or later, the lines of the repository's files it executed. The check runs with a directory first on
`PYTHONPATH` whose `sitecustomize.py` every Python process the check starts loads: it records
`sys.monitoring` line events in files under the repository root, writes them at exit to its own file
in the directory `UGH_OBSERVE_LINES` names, then loads the project's own `sitecustomize`, if any. A
line that runs when a function or class is defined (its decorators and signature, a class body
outside its methods) does not count as running it. A check that ran no such Python records no lines.

One local file per commit, `.sgt/local/observations/<sha>.json`; it does not travel. Recording takes
as long as the check, and in return the observation comes from the real environment.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from collections import Counter
from pathlib import Path

from ugh import state
from ugh.config import load_oracle_config
from ugh.core import tiers

_SITECUSTOMIZE = '''\
import atexit
import os
import sys


def _record():
    mon = getattr(sys, "monitoring", None)
    root, out = os.environ.get("UGH_OBSERVE_ROOT"), os.environ.get("UGH_OBSERVE_LINES")
    if mon is None or not root or not out:
        return
    tool = 3  # an id Python names for no tool (debugger 0, coverage 1, profiler 2, optimizer 5)
    try:
        mon.use_tool_id(tool, "ugh")
    except ValueError:
        return
    lines, where = {}, {}

    def line(code, n):
        f = code.co_filename
        if f not in where:
            real = os.path.realpath(f)
            where[f] = os.path.relpath(real, root) if real.startswith(root + os.sep) else None
        if where[f] is not None:
            lines.setdefault(where[f], set()).add(n)
        return mon.DISABLE

    def write():
        import json
        import tempfile

        mon.set_events(tool, 0)
        try:
            fd, part = tempfile.mkstemp(dir=out, suffix=".part")
            with os.fdopen(fd, "w") as fh:
                json.dump({f: sorted(ns) for f, ns in lines.items()}, fh)
            os.replace(part, part[:-len(".part")] + ".json")
        except OSError:
            pass

    mon.register_callback(tool, mon.events.LINE, line)
    mon.set_events(tool, mon.events.LINE)
    atexit.register(write)


def _theirs():
    import importlib.machinery
    import importlib.util

    here = os.path.realpath(os.path.dirname(__file__))
    rest = [p for p in sys.path if os.path.realpath(p or os.curdir) != here]
    spec = importlib.machinery.PathFinder.find_spec("sitecustomize", rest)
    if spec is not None:
        module = importlib.util.module_from_spec(spec)
        sys.modules[__name__] = module
        spec.loader.exec_module(module)


_record()
_theirs()
'''


def configured(repo) -> bool:
    """Whether the project configures a check."""
    cfg = load_oracle_config(repo)
    return cfg is not None and bool(cfg.tiers)


def check(repo) -> dict | None:
    """Run the configured check on the working tree as it is, its tiers in order up to the first
    failure, recording the lines it runs: `{command, exit, output, lines}`, where `lines` is
    `{path: [[first, last], ...]}`, or None when no Python recorded any. None when no check is
    configured."""
    if not configured(repo):
        return None
    cfg = load_oracle_config(repo)
    repo = Path(repo)
    with tempfile.TemporaryDirectory(prefix="ugh-observe-") as tmp:
        site, out = Path(tmp, "site"), Path(tmp, "lines")
        site.mkdir()
        out.mkdir()
        (site / "sitecustomize.py").write_text(_SITECUSTOMIZE, encoding="utf-8")
        path = os.pathsep.join(p for p in (str(site), os.environ.get("PYTHONPATH")) if p)
        env = {**os.environ, "PYTHONPATH": path, "PYTHONDONTWRITEBYTECODE": "1",
               "UGH_OBSERVE_ROOT": os.path.realpath(repo), "UGH_OBSERVE_LINES": str(out)}
        output, code = [], 0
        for t in cfg.tiers:
            try:
                proc = subprocess.run(t.command, shell=True, cwd=repo, env=env, capture_output=True,
                                      text=True, errors="replace")
            except OSError as e:
                output.append(str(e))
                code = -1
                break
            output.append(proc.stdout + proc.stderr)
            code = proc.returncode
            if code:
                break
        runs = [json.loads(p.read_text(encoding="utf-8")) for p in out.glob("*.json")]
    return {"command": " && ".join(t.command for t in cfg.tiers), "exit": code,
            "output": "".join(output), "lines": _lines(repo, runs) if runs else None}


def _lines(repo: Path, runs: list[dict]) -> dict[str, list[list[int]]]:
    """Every process's lines together, on the files ugh mines that are still there, without the
    lines that define rather than run, as ranges."""
    cfg = tiers.load_tiers(repo)
    union: dict[str, set[int]] = {}
    for run in runs:
        for path, ns in run.items():
            union.setdefault(path, set()).update(ns)
    lines = {}
    for path, ns in sorted(union.items()):
        full = repo / path
        if tiers.resolve_tier(path, cfg) == "ignored" or not full.is_file():
            continue
        ran = sorted(ns - _defining(full.read_bytes()))
        if ran:
            lines[path] = list(_ranges(ran))
    return lines


def _defining(raw: bytes) -> set[int]:
    """The 1-based lines that run when a function or class is defined: its decorators and
    signature, and a class body outside its methods' bodies."""
    from tree_sitter import Parser

    from ugh.entities.extract import _language

    defined, bodies = set(), set()
    stack = [Parser(_language("python")).parse(raw).root_node]
    while stack:
        node = stack.pop()
        stack.extend(node.children)
        if node.type in ("function_definition", "class_definition"):
            top = node.parent if node.parent.type == "decorated_definition" else node
            defined.update(range(top.start_point[0] + 1, node.end_point[0] + 2))
            if node.type == "function_definition":
                body = node.child_by_field_name("body")
                bodies.update(range(body.start_point[0] + 1, body.end_point[0] + 2))
    return defined - bodies


def _ranges(ns: list[int]):
    start = prev = ns[0]
    for n in ns[1:]:
        if n != prev + 1:
            yield [start, prev]
            start = n
        prev = n
    yield [start, prev]


def _path(repo, sha: str) -> Path:
    return Path(repo, state.SGT_DIR, "local", "observations", f"{sha}.json")


def record(repo, sha: str, obs: dict) -> None:
    """Write `obs` as the observation of commit `sha`."""
    state._atomic_write_text(_path(repo, sha), json.dumps(obs))


def load(repo, sha: str | None) -> dict | None:
    """The observation of commit `sha`, or None."""
    path = _path(repo, sha) if sha else None
    return json.loads(path.read_text(encoding="utf-8")) if path and path.is_file() else None


def summary(obs: dict | None) -> dict | None:
    """`{verdict, lines_recorded}`, or None without an observation."""
    if obs is None:
        return None
    return {"verdict": "pass" if obs["exit"] == 0 else "fail", "lines_recorded": obs["lines"] is not None}


def delta(before: dict, after: dict) -> Counter:
    """The output lines `after` has more (positive) or fewer (negative) of than `before`. The
    order of the lines is not compared."""
    d = Counter(after["output"].splitlines())
    d.subtract(before["output"].splitlines())
    return d


def counts(d: Counter) -> dict:
    """`{added, removed}`: how many output lines a delta adds and removes."""
    return {"added": sum(n for n in d.values() if n > 0), "removed": sum(-n for n in d.values() if n < 0)}


def saves_delta(repo, saves: list[tuple[str | None, str]], known: dict) -> Counter | None:
    """What `saves` (`(parent, save)` pairs) changed in the output together, each save's
    observation against its parent's; None when one of those observations is missing. `known` maps
    shas to observations not recorded yet."""
    total = Counter()
    for parent, sha in saves:
        before, after = (known.get(s) or load(repo, s) for s in (parent, sha))
        if before is None or after is None:
            return None
        total.update(delta(before, after))
    return total


def reverses(d: Counter, own: Counter | None) -> bool | None:
    """Whether output delta `d` is the reverse of `own`, the saves' own delta; None when `own` is
    unknown or changed nothing, since then the check has nothing to confirm."""
    if own is None or not any(own.values()):
        return None
    total = Counter(d)
    total.update(own)
    return not any(total.values())


def of_commit(repo, sha: str, parent: str | None) -> dict | None:
    """`{verdict, lines_recorded, delta}` for commit `sha`, `delta` being its output against its
    parent's observation (None without one); None when `sha` has no observation."""
    obs = load(repo, sha)
    if obs is None:
        return None
    before = load(repo, parent)
    return {**summary(obs), "delta": None if before is None else counts(delta(before, obs))}


def ran(obs: dict | None, path: str, lines: range | None) -> bool | None:
    """Whether `obs` executed one of `lines` of `path`. None when there is no observation, it
    recorded no lines, `path` is not Python, or `lines` is empty (the unit is not in that tree)."""
    from ugh.entities.extract import _language_for

    if obs is None or obs["lines"] is None or not lines or _language_for(path) != "python":
        return None
    return any(a < lines.stop and b >= lines.start for a, b in obs["lines"].get(path, ()))
