"""Unknown first-token dispatch.

The old dispatch answered *every* unrecognized token by printing the full help and returning 0.
That is the silent-no-op failure class: a script, an agent, or a user following older output reads
exit 0 as "it ran", when in fact nothing happened. The contract these tests pin: an unrecognized
verb always exits non-zero, and names the closest real verbs.
"""

from ugh.cli import main


def _run(capsys, argv):
    rc = main(argv)
    return rc, capsys.readouterr()


def test_bare_and_help_still_succeed(capsys):
    """The two ways of *asking* for help keep exiting 0 -- only unrecognized verbs became errors."""
    for argv in ([], ["help"], ["--help"], ["-h"]):
        rc, out = _run(capsys, argv)
        assert rc == 0, argv
        assert "the daily spine" in out.out


def test_unknown_verb_exits_nonzero_and_says_so(capsys):
    """The core regression: `ugh bogusverb` must not look like success."""
    rc, out = _run(capsys, ["bogusverb"])
    assert rc == 2
    assert "unknown verb" in out.err and "bogusverb" in out.err
    # The full help must NOT be dumped to stdout -- that is what made the old failure invisible.
    assert "the daily spine" not in out.out


def test_typo_suggests_the_closest_real_command(capsys):
    rc, out = _run(capsys, ["sve"])
    assert rc == 2
    assert "ugh save" in out.err


def test_leading_flag_explains_flag_position(capsys):
    """`ugh --json log` is a plausible mistake (flags are per-subparser); say why it fails."""
    rc, out = _run(capsys, ["--json", "log"])
    assert rc == 2
    assert "after the verb" in out.err


def test_the_spellings_a_git_user_reaches_for_actually_dispatch(capsys):
    """`status` is the first word a git user types; it is a thin alias onto `log --summary`'s
    handler. `show` is where you point at a thing to see it."""
    import ugh.cli as cli

    for verb in ("status", "show"):
        assert verb in cli._VERBS, f"`ugh {verb}` must dispatch"

    # `ugh status` takes no required argument, so it runs; `ugh show` with nothing prints usage.
    assert main(["status"]) == 0
    assert main(["show"]) == 2
