"""The replayed conversation the study testbeds ship with (`scripts/study/capture-prompts.json`).

Two failures are possible here and neither shows up anywhere else. A fixture keyed on a commit
subject the testbed no longer has ships a bundle with the words missing from exactly the work the
task is about -- every stage still passes, so nothing else would say so. And a fixture that gives
one project richer words than the other makes the two arms differ in something that is not the
tool: a participant on `footfall` reading four asks per chapter and one on `bikecount` reading one
is a confound, not a testbed. A third failure would be worse than both and is checked hardest: an
ask that does not correspond to the brief the code was actually built from, which would make every
surface quoting it a fiction.

Offline and instant: this reads the fixture and the answer key, never a repository.
"""

from __future__ import annotations

import json
from pathlib import Path

from sgt.intent.gist import ask_gist

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = json.loads((ROOT / "scripts/study/capture-prompts.json").read_text(encoding="utf-8"))
KEY = json.loads((ROOT / "docs/study/answer-key.json").read_text(encoding="utf-8"))
PROJECTS = ("bikecount", "footfall")


def _briefs(project: str) -> dict[str, str]:
    """The real prompts the testbed's history was harvested from, `{name: prompt}`."""
    path = ROOT / f"scripts/study/harvest/roles-{project}.json"
    return {e["name"]: e["prompt"] for e in json.loads(path.read_text(encoding="utf-8"))}


def _episodes(project: str) -> list[dict]:
    return KEY["episodes"][project]


def test_every_prompt_belongs_to_a_commit_the_testbed_still_has():
    """The join is the commit subject, because shas move when a testbed is rebuilt and subjects do
    not. A subject that has been reworded silently drops that episode's words."""
    for project in PROJECTS:
        subjects = {e["subject"] for e in _episodes(project)}
        unknown = set(FIXTURE[project]) - subjects
        assert not unknown, f"{project}: {sorted(unknown)} match no commit in the answer key"


def test_every_replayed_ask_renders_a_brief_the_history_was_actually_built_from():
    """The words on screen have to be the words that produced the code. Each entry names the
    harvested brief it re-types (`brief`), the answer key says which commit that brief's session
    produced, and the two have to agree -- otherwise a surface quoting an ask is quoting a fiction,
    and every reading the study takes from it is a reading of that fiction."""
    for project in PROJECTS:
        briefs = _briefs(project)
        by_session = {e["session"]: e["subject"] for e in _episodes(project) if e["session"]}
        used = []
        for subject, entry in FIXTURE[project].items():
            brief = entry.get("brief")
            assert brief in briefs, f"{project}/{subject}: no harvested brief called {brief!r}"
            assert by_session.get(brief) == subject, (
                f"{project}: brief {brief!r} produced {by_session.get(brief)!r}, "
                f"but the fixture files it under {subject!r}")
            used.append(brief)
        # ...and none left behind: an unrendered brief is a save whose ask the arm cannot show.
        assert sorted(used) == sorted(briefs), f"{project}: {set(briefs) - set(used)} unrendered"


def test_the_two_testbeds_carry_comparable_conversations():
    """Isomorphic in what it costs a participant to read, not word-for-word: the same number of
    asks, and neither project's prompts more than half again as long as the other's."""
    counts, lengths = {}, {}
    for project in PROJECTS:
        prompts = [p for e in FIXTURE[project].values() for p in e["prompts"]]
        counts[project] = len(prompts)
        lengths[project] = sum(len(p["text"]) for p in prompts) / len(prompts)

    assert counts["bikecount"] == counts["footfall"], counts
    ratio = max(lengths.values()) / min(lengths.values())
    assert ratio < 1.5, f"one testbed's prompts are {ratio:.2f}x the other's: {lengths}"


def test_both_testbeds_carry_the_cases_the_derivation_is_meant_to_handle():
    """A testbed of one-ask-one-save windows exercises none of what the join exists for. Each
    project carries a question that produced no code (which must own nothing), a correction chain
    (where both asks claim their share), and at least one prompt long enough that showing it whole
    would be the wrong answer."""
    for project in PROJECTS:
        windows = list(FIXTURE[project].values())
        assert any(p.get("question") for w in windows for p in w["prompts"]), project
        assert any(len([p for p in w["prompts"] if not p.get("question")]) > 1
                   for w in windows), f"{project} has no correction chain"
        assert any(len(p["text"]) > 500 for w in windows for p in w["prompts"]), project
        # ...and a session that spans several saves, which is how the ownership rule is exercised.
        sessions = [w["session"] for w in windows]
        assert len(sessions) > len(set(sessions)), f"{project} has no session spanning two saves"


def test_one_save_per_testbed_carries_no_words_at_all():
    """A real history has work nobody typed a prompt for, and the surfaces have to be seen saying
    nothing rather than guessing -- a testbed where every save has words never shows a participant
    that state. Here it is the initial dashboard commit, which is the truth rather than a staged
    gap: the harvest has no brief for it."""
    for project in PROJECTS:
        silent = [e["subject"] for e in _episodes(project) if e["subject"] not in FIXTURE[project]]
        # Exactly the initial dashboard commit, which the harvest has no brief for. Every other
        # save was driven by one, and inventing "nobody asked for this" about work somebody did
        # ask for would be staging the very absence the surfaces are meant to report honestly.
        assert len(silent) == 1, f"{project}: {len(silent)} unprompted saves ({silent})"
        assert [e["session"] for e in _episodes(project)
                if e["subject"] in silent] == [None], project


def test_every_prompt_yields_an_ask_worth_putting_on_a_line():
    """The fixture is deliberately badly written -- typos, no capitals, the request buried after
    the reasoning -- so this is where the excerpt rule meets the words it was built for. An empty
    or throat-clearing excerpt would name a chapter after nothing."""
    openers = ("so ", "i think", "ok ", "hey", "we shoudl", "the reason", "right,")
    for project in PROJECTS:
        for subject, entry in FIXTURE[project].items():
            for prompt in entry["prompts"]:
                if prompt.get("question"):
                    continue  # a question owns no ops and names nothing
                gist = ask_gist(prompt["text"], 60)
                assert len(gist) >= 12, f"{project}/{subject}: excerpt too thin: {gist!r}"
                assert not gist.casefold().startswith(openers), \
                    f"{project}/{subject}: excerpt still opens with throat-clearing: {gist!r}"
