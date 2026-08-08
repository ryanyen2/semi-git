#!/usr/bin/env python3
"""Check every `sgt ...` command quoted in the skills and docs against the live CLI surface.

Skills are instructions we ship to agents, and an agent follows them literally. When a verb gets
re-homed (`fsck` -> `advanced fsck`) or renamed to a `log` mode (`status` -> `log --summary`), the
prose that names the old spelling becomes a trap: the agent runs it, gets an error, and either
retries variations or reports the task blocked. At the time this script was written, 11 of the 23
commands in `sgt-workflow/SKILL.md` no longer existed.

Prose drifts silently because nothing executes it. This does: it reads the argparse tree that
actually dispatches and reports any quoted command that would not run, with the replacement path
where one can be computed.

    python -m scripts.check_docs_commands              # skills + docs/guide + README
    python -m scripts.check_docs_commands --fix        # rewrite the re-homed/renamed ones in place
    python -m scripts.check_docs_commands path/to.md   # just these files

Exit status is 1 when anything is stale, so it works as a test or a pre-commit hook.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent

# Where we ship prose that tells someone (or something) to run a command.
DEFAULT_TARGETS = (".claude/skills", "docs/guide", "README.md", "CLAUDE.md")

# A backticked sgt command: `sgt log --map`, `sgt feature regroup merge <a> <b>`. Stops at the first
# token that isn't a plain word/flag, so placeholders (`<ref>`, `"text"`) end the verb path cleanly.
_CMD = re.compile(r"`sgt((?:\s+[a-z][a-z0-9-]*)+)")

# The same command *including* its long flags, so they can be checked against the verb's own parser.
# `--no-color` on a verb that doesn't take it fails exactly as loudly as a renamed verb, and is
# easier to get wrong: the flag exists on sibling verbs, so it reads as universal when it isn't.
_CMD_WITH_FLAGS = re.compile(r"`sgt((?:\s+(?:[a-z][a-z0-9-]*|--[a-z][a-z0-9-]*))+)")

# Spellings that are intentionally *about* the old name rather than an instruction to run it: a
# migration note, or this script's own docstring. A line containing one of these is skipped.
_EXEMPT_LINE_MARKERS = (
    "no longer exists",
    "formerly",
    "was renamed",
    "used to be",
    "stale",
    "re-homed",
    "moved to",
    "->",
    # Prose that names an internal operation while stating it has no CLI entry point. That is
    # honest documentation of an absence, not an instruction to run something -- the guide's note
    # about `sgt pin` is the case this exists for.
    "no command-line entry point",
    "has no cli entry point",
)


def _surface():
    """(top-level verbs, {verb: grouping}, {verb: replacement command}) from the live CLI."""
    sys.path.insert(0, str(REPO))
    import sgt.cli as cli

    # `help` and `git` dispatch in `main()` *before* the `_VERBS` gate, so they are real entry
    # points that simply aren't in that set. Without them here, every `sgt help` in the README
    # reads as stale.
    return cli._VERBS | {"help", "git"}, cli._ROUTING, cli._RENAMED


def _replacement(verb: str, routing: dict, renamed: dict) -> str | None:
    if verb in renamed:
        return renamed[verb]
    group = routing.get(verb)
    if group == "regroup":
        return f"sgt feature regroup {verb}"
    if group is not None:
        return f"sgt {group} {verb}"
    return None


def _display(path: pathlib.Path) -> pathlib.Path:
    """Repo-relative when the file lives in the repo (the normal case, and the readable one), else
    the path as given -- an explicit target elsewhere is legitimate and must not raise."""
    try:
        return path.relative_to(REPO)
    except ValueError:
        return path


def _flag_index():
    """{(verb path tuple): {allowed long flags}} for every registered subparser.

    Built from the real parser tree, so it stays correct through re-homing: `sgt advanced fsck`'s
    flags are looked up under `("advanced", "fsck")` without this file knowing that fsck moved."""
    sys.path.insert(0, str(REPO))
    import sgt.cli as cli

    index: dict[tuple[str, ...], set[str]] = {}
    # A subparser can be reachable by more than one path (the families share a `parents=[parent]`
    # parser, and the router hands the same subparsers action to several groupings), so an
    # unguarded walk recurses forever. Track the actions already expanded.
    seen: set[int] = set()

    def walk(action, prefix=()):
        if id(action) in seen:
            return
        seen.add(id(action))
        choices = getattr(action, "choices", None)
        if not isinstance(choices, dict):
            return  # a plain argument with a `choices=[...]` list, not a subparsers action
        for name, sub in choices.items():
            path = (*prefix, name)
            index[path] = {
                opt for a in sub._actions for opt in a.option_strings if opt.startswith("--")
            }
            nested = next((a for a in sub._actions
                           if isinstance(getattr(a, "choices", None), dict) and a.choices), None)
            if nested is not None:
                walk(nested, path)

    parser = cli._build_parser()
    top = next(a for a in parser._actions if hasattr(a, "choices") and a.choices)
    walk(top)
    return index


def _bad_flags(tokens, flag_index) -> list[str]:
    """Long flags in `tokens` that the deepest matching verb path does not accept."""
    verbs = [t for t in tokens if not t.startswith("--")]
    flags = [t for t in tokens if t.startswith("--")]
    if not flags:
        return []
    # Walk to the deepest registered path these verb tokens name (later tokens may be arguments).
    path: tuple[str, ...] = ()
    for token in verbs:
        if (*path, token) in flag_index:
            path = (*path, token)
        else:
            break
    if not path:
        return []
    allowed = flag_index[path]
    # `--help` is available on every parser; argparse adds it implicitly.
    return [f for f in flags if f not in allowed and f != "--help"]


def _iter_files(targets):
    for target in targets:
        path = REPO / target if not pathlib.Path(target).is_absolute() else pathlib.Path(target)
        if path.is_file():
            yield path
        elif path.is_dir():
            yield from sorted(path.rglob("*.md"))


def check(targets=DEFAULT_TARGETS, *, fix: bool = False):
    """Returns a list of `(path, line_no, quoted, replacement_or_None)` for each stale command."""
    verbs, routing, renamed = _surface()
    flag_index = _flag_index()
    findings = []

    for path in _iter_files(targets):
        original = path.read_text(encoding="utf-8")
        lines = original.splitlines(keepends=True)
        changed = False

        for i, line in enumerate(lines, start=1):
            # Markdown prose wraps, so an exemption phrase ("...has no command-line entry point")
            # routinely lands on the line after the command it qualifies. Test the sentence-ish
            # window around the match rather than the single line.
            window = "".join(lines[max(0, i - 2):i + 1]).lower()
            if any(marker in window for marker in _EXEMPT_LINE_MARKERS):
                continue
            for match in _CMD.finditer(line):
                tokens = match.group(1).split()
                verb = tokens[0]
                if verb in verbs:
                    continue  # dispatches at the top level; argparse owns the rest
                replacement = _replacement(verb, routing, renamed)
                quoted = f"sgt {' '.join(tokens)}"
                findings.append((_display(path), i, quoted, replacement))
                if fix and replacement is not None:
                    # Replace just the verb, keeping whatever arguments followed it.
                    rest = " ".join(tokens[1:])
                    fixed = f"`{replacement}" + (f" {rest}" if rest else "")
                    lines[i - 1] = lines[i - 1].replace(f"`sgt {' '.join(tokens)}", fixed, 1)
                    changed = True

            # Flags, checked against the verb they were written for. Not auto-fixable: the right
            # correction is usually to drop the flag or move it to a verb that has it, which needs
            # a human reading the sentence.
            for match in _CMD_WITH_FLAGS.finditer(line):
                tokens = match.group(1).split()
                if tokens[0] not in verbs:
                    continue  # the verb itself is already reported above
                for flag in _bad_flags(tokens, flag_index):
                    findings.append((_display(path), i, f"sgt {' '.join(tokens)}",
                                     f"{flag} is not a flag of that verb"))

        if changed:
            path.write_text("".join(lines), encoding="utf-8")

    return findings


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("targets", nargs="*", default=None,
                        help="files or directories to check (default: skills, docs/guide, README)")
    parser.add_argument("--fix", action="store_true",
                        help="rewrite re-homed/renamed commands in place")
    args = parser.parse_args(argv)

    findings = check(args.targets or DEFAULT_TARGETS, fix=args.fix)
    if not findings:
        print("✓ every quoted sgt command dispatches")
        return 0

    verb = "rewrote" if args.fix else "found"
    print(f"✗ {verb} {len(findings)} command(s) that do not dispatch:\n")
    for path, line, quoted, replacement in findings:
        fix_note = f"  ->  {replacement}" if replacement else "  ->  no such verb"
        print(f"  {path}:{line}  `{quoted}`{fix_note}")
    if not args.fix:
        print("\nRe-run with --fix to rewrite the ones that have a computable replacement.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
