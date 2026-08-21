"""Every `sgt ...` command the CLI *prints* must actually dispatch.

`tests/test_docs_commands.py` guards the prose we ship; this guards the prose we emit. They drift
the same way and for the same reason -- nothing executes a string literal -- but a printed next-step
is the worse failure of the two, because it arrives at the moment the user has stopped knowing what
to do and has no reason to doubt it. When this test was written, 18 such strings named a command
that had moved: `sgt advanced split-op` told you to run `sgt fulfill`, `sgt advanced fulfill` told
you to run `sgt oracle run` then `sgt commit`, and every `usage:` line in the rewrite CLI named a
spelling that had been re-homed under `advanced`. Three dead commands in a row, each printed by the
step before it.

Detection is deliberately narrow: a command counts as an instruction only when it is backticked,
or follows `usage: ` or a `: ` hand-off. Bare mid-sentence prose ("since sgt last recorded this
branch", "initialized sgt kernel in") is talking *about* sgt rather than telling anyone to run
something, and flagging it would push the fix toward deleting the sentence. The cost of the narrow
rule is that the indented command list in `sgt --help` is not covered here; the docs checker's
markdown pass is what holds the equivalent prose.
"""

from __future__ import annotations

import ast
import pathlib
import re

from scripts.check_docs_commands import unrunnable

REPO = pathlib.Path(__file__).resolve().parent.parent

# `sgt` plus its verb path, stopping at the first placeholder (`<draft-id>`) or quoted argument --
# the same shape as the docs checker's regex, which is why `unrunnable` can be handed the result.
_CMD = re.compile(r"(?:(?<=`)|(?<=usage: )|(?<=: ))sgt((?:\s+(?:[a-z][a-z0-9-]*|--[a-z][a-z0-9-]*))+)")


def commands_in(source: str):
    """Every (line, command) in one module's `print(...)` and `*Error(...)` string literals."""
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None) or ""
        if name != "print" and not name.endswith("Error"):
            continue
        for sub in ast.walk(node):
            if not (isinstance(sub, ast.Constant) and isinstance(sub.value, str)):
                continue
            for match in _CMD.finditer(sub.value):
                # `sgt path:` is a labelled field in `init`'s output, not a command; a colon
                # immediately after the token is the tell that nobody is meant to run it.
                if sub.value[match.end():match.end() + 1] == ":":
                    continue
                yield node.lineno, ("sgt" + match.group(1)).rstrip()


def _printed_commands():
    """Every (path, line, command) a user could be shown, across the package."""
    for path in sorted((REPO / "sgt").rglob("*.py")):
        for line, command in commands_in(path.read_text(encoding="utf-8")):
            yield path.relative_to(REPO), line, command


def test_every_command_the_cli_prints_dispatches():
    stale = [
        (path, line, command, unrunnable(command))
        for path, line, command in _printed_commands()
        if unrunnable(command) is not None
    ]
    assert not stale, (
        "the CLI prints commands that do not dispatch:\n  "
        + "\n  ".join(f"{p}:{l}  {c!r}  ->  {why}" for p, l, c, why in stale)
    )


def test_the_scan_finds_an_instruction_in_each_form_and_ignores_prose():
    """The guard needs its own guard. A scan that silently matches nothing is worse than no scan,
    because it turns an unchecked surface into a green check -- and this one's regex carries three
    separate lookbehinds, any of which could rot without the test above ever going red. Fed a
    synthetic module rather than the repo, so rewording a real message cannot break it."""
    found = {c for _l, c in commands_in(
        'print("usage: sgt merge-op <a> <b>")\n'
        'print("    edit the tree, then: sgt fulfill <id> --from-tree")\n'
        'raise RewriteError("nothing staged -- run `sgt oracle run` first")\n'
        'print("initialized sgt kernel in " + path)\n'
        'print("since sgt last recorded this branch")\n'
        'print("    sgt path: " + str(p))\n'
    )}
    assert found == {"sgt merge-op", "sgt fulfill", "sgt oracle run"}, (
        "each instruction form must be found, and prose about sgt left alone: " + repr(found))
