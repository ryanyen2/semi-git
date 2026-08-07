"""Unknown / moved first-token dispatch.

KTD2's re-homing (`fsck` -> `advanced fsck`) and U14's rename-to-`log`-mode (`status` ->
`log --summary`) both shipped without an alias layer, and the old dispatch answered *every*
unrecognized token by printing the full help and returning 0. That is the silent-no-op failure
class: a script, an agent, or a user following older output reads exit 0 as "it ran", when in fact
nothing happened. The contract these tests pin: an unrecognized verb always exits non-zero, and
whenever sgt can compute the replacement command it names it.
"""

from sgt.cli import main


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
    """The core regression: `sgt bogusverb` must not look like success."""
    rc, out = _run(capsys, ["bogusverb"])
    assert rc == 2
    assert "unknown verb" in out.err and "bogusverb" in out.err
    # The full help must NOT be dumped to stdout -- that is what made the old failure invisible.
    assert "the daily spine" not in out.out


def test_rehomed_verb_names_its_new_path(capsys):
    """A verb that moved under a grouping reports the exact runnable command, not generic help."""
    for verb, expected in (("fsck", "sgt advanced fsck"),
                           ("merge-op", "sgt advanced merge-op"),
                           ("why", "sgt feature why"),
                           ("merge", "sgt feature regroup merge")):
        rc, out = _run(capsys, [verb])
        assert rc == 2, verb
        assert expected in out.err, (verb, out.err)


def test_renamed_verb_names_its_log_mode(capsys):
    """U14 turned four verbs into `log` render modes; each must route to its mode, not to help."""
    for verb, expected in (("status", "sgt log --summary"),
                           ("map", "sgt log --map"),
                           ("episodes", "sgt log --rail"),
                           ("checkpoint", "sgt save")):
        rc, out = _run(capsys, [verb])
        assert rc == 2, verb
        assert expected in out.err, (verb, out.err)


def test_typo_suggests_the_closest_real_command(capsys):
    """A near-miss gets a did-you-mean, and the suggestion is a *runnable* path (not a bare name
    for a verb that only exists under a grouping)."""
    rc, out = _run(capsys, ["sve"])
    assert rc == 2
    assert "sgt save" in out.err

    rc, out = _run(capsys, ["blam"])  # `blame` lives under `advanced`
    assert rc == 2
    assert "sgt advanced blame" in out.err


def test_leading_flag_explains_flag_position(capsys):
    """`sgt --json log` is a plausible mistake (flags are per-subparser); say why it fails."""
    rc, out = _run(capsys, ["--json", "log"])
    assert rc == 2
    assert "after the verb" in out.err
