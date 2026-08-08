"""Every `sgt ...` command we ship in prose must actually dispatch.

Skills are instructions handed to an agent, and an agent follows them literally. When a verb is
re-homed (`fsck` -> `advanced fsck`) or folded into another (`checkpoint` -> `save`), any prose
naming the old spelling becomes a trap: the agent runs it, fails, and either thrashes on variations
or reports the task blocked. Prose drifts silently because nothing executes it -- when this test was
written, 11 of the 23 commands in `sgt-workflow/SKILL.md` no longer existed, and the guide still
described a `sgt pin` verb.

So this is the guard for the documentation layer, the same shape as
`tests/test_show.py`'s check that every suggested command dispatches: a rename that forgets the
skills fails here rather than reaching an agent.
"""

from __future__ import annotations

from scripts.check_docs_commands import check


def test_every_documented_sgt_command_dispatches():
    findings = check()
    assert not findings, (
        "documentation names commands that do not dispatch:\n  "
        + "\n  ".join(
            f"{path}:{line}  `{quoted}`"
            + (f"  ->  {replacement}" if replacement else "  ->  no such verb")
            for path, line, quoted, replacement in findings
        )
        + "\n\nRun `python -m scripts.check_docs_commands --fix` to rewrite the re-homed ones."
    )


def test_the_checker_catches_a_rehomed_verb_and_an_invented_one(tmp_path):
    """The guard needs its own guard: a checker that silently passes everything is worse than none,
    because it converts an unmaintained surface into a green check."""
    doc = tmp_path / "doc.md"
    doc.write_text(
        "Run `sgt fsck --tree` first, then `sgt bogusverb`, then `sgt log --map`.\n",
        encoding="utf-8",
    )

    findings = check([str(doc)])
    quoted = {q: r for _p, _l, q, r in findings}

    assert "sgt fsck" in quoted, "a re-homed verb must be reported"
    assert quoted["sgt fsck"] == "sgt advanced fsck", "and its replacement path computed"
    assert "sgt bogusverb" in quoted and quoted["sgt bogusverb"] is None
    assert "sgt log" not in quoted, "a verb that dispatches must not be reported"


def test_prose_about_a_removed_verb_is_not_flagged(tmp_path):
    """Docs legitimately *mention* old spellings when explaining a rename or an absence. Flagging
    those would push writers toward deleting the explanation, which is the opposite of useful."""
    doc = tmp_path / "doc.md"
    doc.write_text(
        "`sgt checkpoint` no longer exists; it folded into `sgt save`.\n"
        "\n"
        "There is a lower-level operation, `sgt pin`, that this wizard does not offer,\n"
        "because it has no command-line entry point of its own.\n",
        encoding="utf-8",
    )
    assert check([str(doc)]) == []


def test_the_checker_catches_a_flag_the_verb_does_not_accept(tmp_path):
    """Flags drift the same way verbs do, and are easier to get wrong because they exist on sibling
    verbs: `--no-color` is on `sgt now` and `sgt log` but not `sgt show`, so it reads as universal.
    Writing `sgt show --no-color` into a skill hands an agent a command that dies on an argparse
    error -- which is how this check came to exist."""
    doc = tmp_path / "doc.md"
    doc.write_text(
        "Show it plainly with `sgt show f-abc --no-color`, or `sgt now --no-color`.\n"
        "Read it with `sgt show f-abc --json` and `sgt log --map`.\n",
        encoding="utf-8",
    )

    reported = {(quoted, note) for _p, _l, quoted, note in check([str(doc)])}
    flagged = {q for q, note in reported if note and "not a flag" in note}

    assert any("show" in q for q in flagged), "`--no-color` on `sgt show` must be reported"
    assert not any("now" in q for q in flagged), "`--no-color` on `sgt now` is valid"
    assert not any("--json" in q for q in flagged), "`--json` is valid on both"
    assert not any("--map" in q for q in flagged), "`--map` is a real `log` mode"
