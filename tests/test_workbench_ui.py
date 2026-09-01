"""The workbench webview's pure UI derivations (`editor/vscode/media/workbench.js`), sliced out and
run under node the way the rail-layout tests do. Four blocks, each marked off in the source:

`signalChips` turns the three nowhere-to-attach signals (unplanned drift, unplaced forks, reverted
work with no lane) into one chip each. The contract is a UI one: a chip is a hit target, so the thing
a click does has to be the thing the reader pointed at, and the tooltip has to name it. It used to be
one merged chip with one merged tooltip and one click that resolved a fork no matter which glyph you
aimed at.

`loopButtonState` derives the Save/Undo buttons from whichever verb is running in the host. They had
no in-flight state at all, so a reader with no feedback clicks again.

`workingChangesCard` says what a not-clean working tree *is*, which is two situations the card used to
conflate: drift (bytes the ideal has never seen) and a staged rewrite candidate (bytes that replace
recorded ops, held out until the oracle agrees). Conflated, a candidate was titled "Working changes",
explained as unrecorded edits, and offered a Save that the lens refuses.

`onHoverIntent` gates the hover previews, which shell out -- the host runs `sgt <verb> --emit` in a
subprocess per preview. Fired straight off `mouseenter`, a sweep down the rail started one per row
crossed. Resting on a row is the intent; crossing it is not.

`armedBannerText` states the armed merge/move mode. Its only sign was `cursor: crosshair`.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess

import pytest

_JS = pathlib.Path(__file__).resolve().parents[1] / "editor/vscode/media/workbench.js"


def _slice(start: str, end: str) -> str:
    text = _JS.read_text(encoding="utf-8")
    return text[text.index(start):text.index(end)]


def _node(snippet: str, tail: str):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    res = subprocess.run([node, "-e", snippet + tail], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


def _call(expr: str):
    """Evaluate `expr` against the titlebar block (signalChips + loopButtonState)."""
    return _node(_slice("function signalChips", "// ---- end-signals"),
                 f"console.log(JSON.stringify({expr}));\n")


def _run(drift: list, forks: list, gap: dict) -> list:
    return _call(f"signalChips({json.dumps(drift)}, {json.dumps(forks)}, {json.dumps(gap)})")


def _forks(*symbols):
    return [{"symbol": s} for s in symbols]


# ── The titlebar signal chips ────────────────────────────────────────────────────────────────────
def test_only_the_fork_chip_is_actionable():
    """Pointing at reverted work must never fire fork resolution: the glyphs are separate chips and
    only the fork one carries a symbol to act on."""
    chips = _run([{"footprint": ["a.py::f"]}, {"footprint": ["b.py::g"]}],
                 _forks("cart.py::apply_coupon"),
                 {"op_count": 4, "symbols": ["cart.py::apply_coupon"]})
    assert [c["glyph"] for c in chips] == ["◇2", "⑂1", "░4"]
    assert [bool(c.get("symbol")) for c in chips] == [False, True, False]


def test_the_fork_chip_names_the_fork_the_click_will_open():
    chips = _run([], _forks("a.py::one", "b.py::two", "c.py::three"), {})
    fork = next(c for c in chips if c["glyph"].startswith("⑂"))
    assert fork["symbol"] == "a.py::one"
    assert "a.py::one" in fork["title"] and "+2 more" in fork["title"]


def test_the_gap_chip_counts_the_symbols_it_cannot_list():
    syms = [f"cart.py::apply_coupon_{i}" for i in range(9)]
    chips = _run([], [], {"op_count": 12, "symbols": syms})
    title = chips[0]["title"]
    assert "cart.py::apply_coupon_0" in title and "+5 more" in title
    assert "cart.py::apply_coupon_8" not in title
    assert "sgt undo" in title and "sgt restore" in title


def test_no_signal_draws_no_chip():
    assert _run([], [], {"op_count": 0, "symbols": []}) == []


# ── The Save/Undo pair ───────────────────────────────────────────────────────────────────────────
def test_idle_loop_buttons_are_live_and_named_plainly():
    assert _call("loopButtonState(null)") == [
        {"verb": "save", "label": "Save", "disabled": False},
        {"verb": "undo", "label": "Undo", "disabled": False},
    ]


def test_the_daily_loop_does_not_offer_commit():
    """There were three buttons here. The middle one was wrong in both directions at once: `sgt save`
    mines *and* commits, so a Commit beside Save promised a step Save had already taken, while the
    command it ran (`sgt advanced commit`) lands a staged rewrite candidate and otherwise refuses with
    "nothing staged". Either redundant or an error toast, drawn permanently for a state that is rare
    and oracle-gated. Landing moved to the Working-changes card, which draws it only when there is a
    candidate. Asserted by name because a button whose label describes the verb beside it is the
    specific mistake, and it is an easy one to re-add."""
    assert [b["verb"] for b in _call("loopButtonState(null)")] == ["save", "undo"]


def test_a_running_verb_disables_both_not_just_itself():
    """Both mutate the same ideal, so an undo fired mid-save is a race the reader did not mean to
    start -- and only the running one says what it is doing."""
    state = _call('loopButtonState("save")')
    assert [b["disabled"] for b in state] == [True, True]
    assert [b["label"] for b in state] == ["Saving…", "Undo"]
    assert _call('loopButtonState("undo")')[1]["label"] == "Undoing…"


# ── The working-changes card ──────────────────────────────────────────────────────────────────────
def _card(status: dict, rewrite: dict | None = None) -> dict:
    return _node(_slice("// ---- working-changes", "// ---- end-working-changes"),
                 f"console.log(JSON.stringify(workingChangesCard({json.dumps(status)}, "
                 f"{json.dumps(rewrite)})));\n")


def _messages() -> dict:
    return _node(_slice("// ---- working-changes", "// ---- end-working-changes"),
                 "console.log(JSON.stringify(CARD_MESSAGE));\n")


_CLEAN = {"drift": {"any": False, "paths": []}, "staged": {"any": False, "paths": []}}
_DRIFT = {"drift": {"any": True, "paths": ["a.py"]}, "staged": {"any": False, "paths": []}}
_STAGED = {"drift": {"any": False, "paths": []}, "staged": {"any": True, "paths": ["slugify.py"]}}


def test_a_staged_candidate_is_not_called_working_changes():
    """The bug this split fixes. `status_view` used to report a staged rewrite candidate's paths as
    drift, so the card titled them "Working changes · 1", said "edits not yet recorded", and offered
    Save -- which `lens.put`'s staged guard refuses, because those bytes replace recorded ops instead
    of adding to them. The one button drawn was the one that could not run."""
    card = _card(_STAGED, {"staged": {"verb": "merge-op", "op_count": 6, "oracle_status": "pass"}})
    assert card["state"] == "staged"
    assert card["title"] == "Staged · merge-op"
    assert "Working changes" not in card["title"]
    assert [a["verb"] for a in card["actions"]] == ["land", "unstage"]
    assert not any(a["verb"] == "save" for a in card["actions"])


def test_a_pending_oracle_blocks_land_and_offers_the_check_instead():
    """Landing is gated on the candidate's own oracle verdict, which is not the titlebar chip's (that
    one is the current ideal's). Unshown, the gate arrives as a refusal after the click -- so the
    primary becomes the check that clears it, and Land stays visible-but-disabled to say the gate is
    the reason rather than hiding that landing exists at all."""
    card = _card(_STAGED, {"staged": {"verb": "split-op", "op_count": 9, "oracle_status": "pending"}})
    assert card["actions"][0]["verb"] == "oracle" and card["actions"][0]["primary"] is True
    land = next(a for a in card["actions"] if a["verb"] == "land")
    assert land["disabled"] is True
    assert "pending" in land["hint"]
    assert "blocked" in card["gate"] and "pending" in card["gate"]


def test_a_passing_oracle_says_so_and_makes_land_primary():
    card = _card(_STAGED, {"staged": {"verb": "merge-op", "op_count": 6, "oracle_status": "pass"}})
    assert card["actions"][0] == {
        "verb": "land", "label": "Land", "primary": True,
        "hint": "sgt advanced commit — replace those ops in the ideal",
    }
    assert card["gate"] == "Oracle passed — safe to land."


def test_abandon_is_offered_in_every_staged_state():
    """A staged candidate blocks every materializing verb, so a card that shows no way out of it
    leaves the terminal as the only exit. Both gate states have to offer the second exit."""
    for gate in ("pass", "pending", "fail"):
        card = _card(_STAGED, {"staged": {"verb": "merge-op", "op_count": 1, "oracle_status": gate}})
        assert any(a["verb"] == "unstage" for a in card["actions"]), gate


def test_drift_offers_one_save_because_save_already_commits():
    """The card drew Save *and* Commit. `sgt save` mines and commits in one verb, so the second button
    named a step the first had already taken."""
    card = _card(_DRIFT)
    assert card["state"] == "drift"
    assert card["title"] == "Working changes · 1"
    assert [a["verb"] for a in card["actions"]] == ["save"]
    assert "commits" in card["why"]


def test_a_clean_tree_draws_no_actions():
    card = _card(_CLEAN)
    assert card["state"] == "clean"
    assert card["clean"] == "Clean — everything is recorded."
    assert "actions" not in card and "paths" not in card


def test_staged_without_the_candidate_detail_is_still_a_staged_card():
    """`status.staged` and `rewrite.staged` come from two view functions, so a refresh can deliver the
    paths without the detail. The first cut keyed the staged branch on both and fell through to the
    drift branch, which -- with the staged paths excluded from drift, as they now are -- printed
    "Clean — everything is recorded." over a tree holding an unlanded rewrite. A tree with a candidate
    on it is the one tree that must never be called clean, so the fallback is a staged card with less
    to say: no verb in the title, no op count, and the gate at its safe default."""
    card = _card(_STAGED, None)
    assert card["state"] == "staged"
    assert card["title"] == "Staged rewrite"
    assert "recorded op(s) rewritten" not in card["why"]
    assert any(a["verb"] == "unstage" for a in card["actions"])
    assert next(a for a in card["actions"] if a["verb"] == "land")["disabled"] is True


def test_every_card_action_maps_to_a_message_the_host_answers():
    """The card posts one message per action verb, and the mapping is where an action silently does
    nothing. `unstage` was first written as `applyVerb`, whose switch is feature verbs only and
    answers anything else by throwing `unknown feature verb` -- the silent-success shape, except the
    failure lands in a toast the reader cannot act on."""
    HANDLED = {"landCandidate", "abandonCandidate", "runOracle", "dailyLoop"}
    messages = _messages()
    verbs = {a["verb"] for st in (_DRIFT, _STAGED) for gate in ("pass", "pending")
             for a in _card(st, {"staged": {"verb": "merge-op", "op_count": 1,
                                            "oracle_status": gate}})["actions"]}
    assert verbs <= set(messages), f"actions with no message: {verbs - set(messages)}"
    for verb in verbs:
        assert messages[verb]["type"] in HANDLED, f"{verb} -> {messages[verb]}"

    host = (pathlib.Path(__file__).resolve().parents[1] / "editor/vscode/src/workbench.ts")
    text = host.read_text(encoding="utf-8")
    for verb in verbs:
        kind = messages[verb]["type"]
        assert f'case "{kind}":' in text, f"{verb} posts {kind!r}, which workbench.ts does not handle"


# ── Hover intent ─────────────────────────────────────────────────────────────────────────────────
def _hover(body: str) -> list:
    return _node(_slice("// ---- hover-intent", "// ---- end-hover-intent"),
                 "const fired = [];" + body +
                 "\nsetTimeout(() => console.log(JSON.stringify(fired)), 400);\n")


def test_crossing_rows_previews_only_the_one_rested_on():
    """Each preview is a subprocess: a sweep down forty lanes to reach the fortieth used to start
    forty of them, and every result that landed painted, so the consequence flickered past on the
    way to the row actually being asked about."""
    assert _hover(
        'onHoverIntent(() => fired.push("a"));'
        'onHoverIntent(() => fired.push("b"));'
        'onHoverIntent(() => fired.push("c"));'
    ) == ["c"]


def test_leaving_before_the_delay_previews_nothing():
    assert _hover('onHoverIntent(() => fired.push("a")); cancelHoverIntent();') == []


def test_resting_does_fire():
    assert _hover('onHoverIntent(() => fired.push("a"));') == ["a"]


# ── The armed merge/move mode ────────────────────────────────────────────────────────────────────
def _banner(armed, label=None):
    return _node(_slice("// ---- armed-banner", "// ---- end-armed-banner"),
                 f"console.log(JSON.stringify(armedBannerText({json.dumps(armed)}, "
                 f"{json.dumps(label)})));\n")


def test_no_armed_verb_shows_no_banner():
    assert _banner(None) is None


def test_the_banner_names_the_verb_the_subject_the_click_and_the_way_out():
    text = _banner({"verb": "merge", "feature": "f-abc"}, "Cart pricing")
    assert text.startswith('Merge "Cart pricing" into which lane?')
    assert "click one" in text and "Esc to cancel" in text
    assert "Move" in _banner({"verb": "move", "feature": "f-abc"}, "Cart pricing")


def test_an_unnamed_feature_falls_back_to_its_handle():
    """Never a bare mode with no subject: without a label the id is what identifies it."""
    assert "f-abc" in _banner({"verb": "merge", "feature": "f-abc"}, None)


# ── The split preview ────────────────────────────────────────────────────────────────────────────
def _split(res):
    return _node(_slice("// ---- split-preview", "// ---- end-split-preview"),
                 f"console.log(JSON.stringify(splitPreviewText({json.dumps(res)})));\n")


def test_a_refused_split_says_so_instead_of_painting_nothing():
    """The only sign of "this can't be split" was that nothing was painted -- identical to a preview
    still in flight. Split's refusals are the interesting half of its feedforward: the backend
    already knows a one-piece feature has no cut, and says why."""
    say = _split({"ok": False, "message": "'f-abc' has too few members to split"})
    assert say["kind"] == "refused"
    assert "too few members" in say["message"]


def test_a_split_names_the_symbols_that_leave_and_how_many_stay():
    """The whole decision a split asks for is whether *this* cut is the right one, and the symbol
    lists that answer it were computed, returned, and thrown away -- the preview painted one amber
    row and said nothing else, which the reader already knew from having hovered that row."""
    say = _split({"ok": True, "groups": [["a.py::keep1", "a.py::keep2"], ["b.py::go"]]})
    assert say["kind"] == "split"
    # Same frame the terminal's split pane uses, so one operation reads one way on both surfaces.
    assert "splits in two" in say["message"]
    assert "keeps 2, new 1" in say["message"]
    assert "b.py::go" in say["message"]
    assert "a.py::keep1" not in say["message"]  # the departing side is the side under judgement


def test_a_long_departing_list_is_capped_with_a_real_count():
    """Unlike undo's upstream-capped symbol list, both sides arrive whole here, so the remainder is
    a number this payload can be right about -- name a few and count the rest."""
    say = _split({"ok": True, "groups": [["a.py::k"], [f"b.py::g{i}" for i in range(7)]]})
    assert "b.py::g0" in say["message"] and "b.py::g2" in say["message"]
    assert "+4 more" in say["message"]
    assert "b.py::g6" not in say["message"]


def test_a_payload_without_two_groups_is_a_refusal_not_a_claimed_split():
    """`split` is always binary (lens/verbs.py plan_split folds >2 communities into 2), so anything
    else is a payload this preview cannot describe -- and describing it anyway is the failure mode
    the old `groups.length > 1` check had, which for an ok preview was vacuously true."""
    assert _split({"ok": True})["kind"] == "refused"
    assert _split({"ok": True, "groups": [["a.py::only"]]})["kind"] == "refused"


# ── The pre-target arm preview ────────────────────────────────────────────────────────────────────
def _arm(verb, node, op_count):
    return _node(_slice("// ---- arm-preview", "// ---- end-arm-preview"),
                 f"console.log(JSON.stringify(armPreviewText({json.dumps(verb)}, "
                 f"{json.dumps(node)}, {json.dumps(op_count)})));\n")


_FEAT = {"label": "Cart pricing", "own_symbols": ["cart.py::price", "cart.py::coupon"]}


def test_merge_says_the_hovered_lane_stops_existing():
    """Merge and move were the two verbs with no hover preview at all -- `previewAction` returned
    early for both because neither has its second operand yet. But the half that does not depend on
    the target is the half that decides *which verb to pick*: what becomes of the lane under the
    cursor. Merge ends it, and that is the claim it makes (these two lanes are one feature)."""
    say = _arm("merge", _FEAT, 3)
    assert say["role"] == "blast"  # loses its ops -- the established meaning of that paint
    assert "Cart pricing" in say["message"]
    assert "3 edits" in say["message"] and "2 symbols" in say["message"]
    assert "stops existing" in say["message"]


def test_move_discloses_that_the_lane_leaves_the_graph():
    """The defect this preview exists for. `computeLayout` drops a lane with no ops
    (`if (!commits.length) continue`), so moving every edit out makes the source lane vanish from the
    rail while the feature is still in the tree -- a result visually identical to the merge the
    reader did not choose. Feedback that confirms the wrong operation removes the reason to check,
    so the disclosure has to happen before the click."""
    say = _arm("move", _FEAT, 3)
    assert "leaves the graph" in say["message"]
    assert "keeps its 2 symbols" in say["message"]  # the difference from merge, stated with the count
    assert "3 edits" in say["message"]


def test_merge_and_move_are_told_apart_by_their_previews():
    """Two buttons on one bar whose only difference is whether the source survives. If the previews
    do not name that difference the choice is unmakeable."""
    merge, move = _arm("merge", _FEAT, 3), _arm("move", _FEAT, 3)
    assert merge["message"] != move["message"]
    assert "stops existing" not in move["message"]
    assert "leaves the graph" not in merge["message"]


def test_rename_previews_that_it_is_safe():
    """Rename's preview is not a warning, it is a reassurance -- the one verb here that changes no
    edits, no symbols and no lanes. Saying so is what makes it usable as a first move; silence made
    it look as consequential as revert."""
    say = _arm("rename", _FEAT, 3)
    assert say["role"] == "target"  # this lane, unchanged -- not the ops-losing paint
    assert "label only" in say["message"]
    assert "nothing moves" in say["message"]


def test_one_edit_is_not_pluralised():
    assert "1 edit " in _arm("merge", _FEAT, 1)["message"]
    assert "1 symbol " in _arm("merge", {"label": "X", "own_symbols": ["a.py::b"]}, 1)["message"]


def test_a_feature_with_no_label_or_symbols_still_previews():
    """The preview must degrade rather than render "undefined" into a sentence the reader trusts."""
    for verb in ("merge", "move", "rename"):
        say = _arm(verb, {}, 0)
        assert "undefined" not in say["message"] and "null" not in say["message"]
        assert say["message"]


# ── The armed-hover result sentence ───────────────────────────────────────────────────────────────
def _armed(verb, res, src="Cart pricing", tgt="Checkout", tgt_ops=7):
    return _node(_slice("// ---- armed-result", "// ---- end-armed-result"),
                 f"console.log(JSON.stringify(armedResultText({json.dumps(verb)}, {json.dumps(res)}, "
                 f"{json.dumps(src)}, {json.dumps(tgt)}, {json.dumps(tgt_ops)})));\n")


def test_a_hovered_merge_target_states_the_lane_that_results():
    """The moment of choice had the weakest preview in the file: while arming, `previewAndBlast`
    skips the deep-dim morph and falls back to flat ghost paint, which colours two lanes and says
    nothing about what the pair becomes -- while the banner asks the same question regardless of
    which candidate is under the cursor. Both numbers that answer it were already in the payload and
    thrown away, exactly as split's `groups` were: merge's counts are the combined totals."""
    say = _armed("merge", {"ok": True, "op_count": 10, "member_count": 14})
    assert "Checkout" in say and "Cart pricing" in say
    assert "one lane of 10 edits" in say and "14 symbols" in say
    assert "gone" in say  # which of the two ends is the whole decision


def test_a_hovered_move_target_states_both_sides_of_the_transfer():
    say = _armed("move", {"ok": True, "op_ids": ["a", "b", "c"]})
    assert "3 edits land here (7 → 10)" in say
    assert "leaves the graph" in say  # the source's fate, again, at the point of no return


def test_a_refused_preview_says_nothing_rather_than_guessing():
    """`setPreviewContext(null)` hides the pill, so a null here is the correct empty -- inventing a
    sentence for a merge the backend refused would describe a result that will not happen."""
    assert _armed("merge", {"ok": False, "message": "cannot merge a feature into itself"}) is None
    assert _armed("merge", None) is None


def test_the_sentence_survives_a_payload_without_counts():
    """A preview that came back ok but thin still gets a sentence naming the two lanes; it must not
    render "undefined edits"."""
    say = _armed("merge", {"ok": True})
    assert "undefined" not in say and "Checkout" in say


# ── The staged confirm's one sentence ────────────────────────────────────────────────────────────
def _staged(staged):
    """The staged summary composes two sibling pure blocks -- it humanizes a refusal string and
    defers to the split preview's own sentence -- so the harness concatenates all three, the way
    they sit together in the one IIFE at runtime."""
    block = (_slice("// ---- humanize", "// ---- end-humanize")
             + _slice("// ---- split-preview", "// ---- end-split-preview")
             + _slice("// ---- staged-summary", "// ---- end-staged-summary"))
    return _node(block, f"console.log(JSON.stringify(stagedSummaryText({json.dumps(staged)})));\n")


def test_the_backend_headline_wins_when_it_exists():
    """`so_what` is the one consequence vocabulary the CLI gate, the TUI pane and this bar share;
    when the payload carries it, re-deriving a second sentence is how surfaces drift apart."""
    say = _staged({"verb": "revert", "kind": "feature", "targetId": "f-a",
                   "res": {"ok": True, "so_what": "Removes west_share_by_year. Clean revert."}})
    assert say == "Removes west_share_by_year. Clean revert."


def test_without_a_headline_the_sentence_carries_the_three_counts():
    say = _staged({"verb": "revert", "kind": "feature", "targetId": "f-a",
                   "res": {"ok": True, "removed": ["o1", "o2", "o3"],
                           "files": {"a.py": {}, "b.py": {}},
                           "affected": [{"feature_id": "f-a", "direction": "blast"},
                                        {"feature_id": "f-b", "direction": "blast"}]}})
    assert "3 edits come out" in say
    assert "2 files rewritten" in say
    assert "1 other feature affected" in say  # the target itself is never its own collateral


def test_a_restore_counts_what_comes_back_not_what_leaves():
    say = _staged({"verb": "restore", "kind": "chapter", "targetId": "f-a",
                   "res": {"ok": True, "added": ["o1", "o2"], "removed": [], "files": {"a.py": {}}}})
    assert "brings back 2 edits" in say


def test_a_refusal_is_the_sentence_not_a_blank_bar():
    say = _staged({"verb": "revert", "kind": "feature", "targetId": "f-a",
                   "res": {"ok": False, "message": "open fork on west_share_by_year"}})
    assert "open fork" in say


def test_no_result_yet_reads_as_computing_never_as_safe():
    assert "computing" in _staged({"verb": "revert", "kind": "feature", "targetId": "f-a"})


def test_back_to_here_counts_chapters_and_keeps_checking_honest():
    """The cross-feature blast accumulates one preview at a time; until the chain finishes the
    sentence must say the count is still firming up, not present a partial number as the total."""
    partial = _staged({"verb": "revert", "kind": "backto", "targetId": "f-a",
                       "refs": ["f-a@2", "f-a@1"], "opCount": 7, "blastCount": 1,
                       "blastDone": False, "res": {"ok": True}})
    assert "removes 2 later chapters" in partial and "7 edits" in partial
    assert "still checking" in partial and "≥1" in partial
    done = _staged({"verb": "revert", "kind": "backto", "targetId": "f-a",
                    "refs": ["f-a@2"], "opCount": 3, "blastCount": 0,
                    "blastDone": True, "res": {"ok": True}})
    assert "no other feature touched" in done and "this chapter stays" in done


# ── Chapter scope: what the action bar is FOR once a checkpoint is selected ──────────────────────
_SEGS = [
    {"checkpoint": "f-a@0", "intent": "first cut", "op_count": 4, "present_op_count": 4, "op_ids": []},
    {"checkpoint": "f-a@1", "intent": "drifting", "op_count": 3, "present_op_count": 3, "op_ids": []},
    {"checkpoint": "f-a@2", "intent": "polish", "op_count": 2, "present_op_count": 0, "op_ids": []},
    {"checkpoint": "f-a@3", "intent": "regroup", "op_count": 5, "present_op_count": 2, "op_ids": []},
]


def _scope(segs, ref):
    return _node(_slice("// ---- chapter-scope", "// ---- end-chapter-scope"),
                 f"console.log(JSON.stringify(chapterScope({json.dumps(segs)}, {json.dumps(ref)})));\n")


def test_back_to_here_is_the_live_later_chapters_newest_first():
    """"Revert to this checkpoint" means removing what came after, and each `sgt revert <f>@<n>`
    peels the current tip -- so the apply order has to be newest-first, and an already-reverted
    later chapter (present_op_count 0) must not be re-reverted on the way down."""
    scope = _scope(_SEGS, "f-a@0")
    assert scope["laterRefs"] == ["f-a@3", "f-a@1"]  # @2 is already out; newest first
    assert scope["laterCount"] == 2
    assert scope["laterOps"] == 3 + 2  # live edits only: @1 whole, @3's remaining 2


def test_a_reverted_chapter_offers_restore_not_rewind():
    assert _scope(_SEGS, "f-a@2")["gone"] is True
    assert _scope(_SEGS, "f-a@1")["gone"] is False


def test_the_last_chapter_has_no_later_work_to_remove():
    assert _scope(_SEGS, "f-a@3")["laterCount"] == 0


def test_an_unknown_checkpoint_scopes_to_nothing():
    """A stale selection (the segment list changed under it) must fall back to the feature bar,
    never to a chapter bar acting on a ref that no longer resolves."""
    assert _scope(_SEGS, "f-b@9") is None


# ── The instant rung of find ─────────────────────────────────────────────────────────────────────
_NODES = [
    {"id": "f-date", "kind": "feature", "label": "Date formatting", "op_count": 9,
     "members": ["util/dates.py::format_date", "util/dates.py::parse_date"]},
    {"id": "f-cart", "kind": "feature", "label": "Cart pricing", "op_count": 4,
     "members": ["shop/cart.py::total"]},
    {"id": "s-root", "kind": "subsystem", "label": "date suite", "members": []},
]
_SEGS_BY_FEATURE = {"f-cart": [{"checkpoint": "f-cart@0", "intent": "add date window filter"}]}


def _find(query, cap=None):
    args = f"{json.dumps(query)}, {json.dumps(_NODES)}, {json.dumps(_SEGS_BY_FEATURE)}"
    if cap is not None:
        args += f", {cap}"
    return _node(_slice("// ---- local-find", "// ---- end-local-find"),
                 f"console.log(JSON.stringify(localFindHits({args})));\n")


def test_typing_matches_features_chapters_and_symbols_without_a_round_trip():
    hits = _find("date")
    kinds = {(h["kind"], h["label"]) for h in hits}
    assert ("feature", "Date formatting") in kinds
    assert ("chapter", "add date window filter") in kinds
    assert ("symbol", "format_date") in kinds
    # A subsystem is not a lane and not a target; it never appears as a hit.
    assert not any(h["label"] == "date suite" for h in hits)


def test_a_prefix_match_outranks_a_buried_substring():
    hits = _find("date")
    labels = [h["label"] for h in hits]
    assert labels.index("Date formatting") < labels.index("format_date")


def test_every_hit_lands_somewhere():
    """A hit is a starting point: each carries the feature to reveal, and a chapter carries the
    exact checkpoint so the click lands on its car, not just the lane."""
    hits = _find("date")
    assert all(h["feature"] for h in hits)
    chapter = next(h for h in hits if h["kind"] == "chapter")
    assert chapter["checkpoint"] == "f-cart@0"


def test_an_empty_query_answers_nothing_and_the_cap_holds():
    assert _find("") == []
    assert len(_find("date", cap=2)) == 2


def test_staged_messages_reach_a_host_case():
    """Every message the staged confirm bar can post has a `case` in workbench.ts's switch -- the
    same wiring guarantee the working-changes card's test holds, for the same silent-click reason."""
    host = (pathlib.Path(__file__).resolve().parents[1] / "editor/vscode/src/workbench.ts")
    text = host.read_text(encoding="utf-8")
    for kind in ("applyStaged", "revertSequence", "openStagedDiff", "openFoldFiles"):
        assert f'case "{kind}":' in text, f"the webview posts {kind!r}, which workbench.ts does not handle"


# ── Chunk-grain feedforward: which cars change, toward which end state ───────────────────────────
_IMPACT_SEGS = [
    {"checkpoint": "f-a@0", "feature_id": "f-a", "op_ids": ["o1", "o2"]},
    {"checkpoint": "f-a@1", "feature_id": "f-a", "op_ids": ["o3", "o4", "o5"]},
    {"checkpoint": "f-b@0", "feature_id": "f-b", "op_ids": ["o6"]},
    {"checkpoint": "f-c@0", "feature_id": "f-c", "op_ids": ["o7"]},
]


def _impact(removed, added, segs=None):
    return _node(_slice("// ---- car-impact", "// ---- end-car-impact"),
                 f"console.log(JSON.stringify(classifyCarImpact({json.dumps(removed)}, "
                 f"{json.dumps(added)}, {json.dumps(segs if segs is not None else _IMPACT_SEGS)})));\n")


def test_a_revert_marks_the_exact_cars_its_ops_live_in():
    """The preview payload names op ids; segments own op ids per chapter. The join is what lets the
    graph draw the consequence at chunk grain -- including a dependency's car in ANOTHER lane
    (o6 here), which is "the dependencies also come out", in situ."""
    impacts = _impact(["o1", "o2", "o6"], [])
    by_cp = {i["checkpoint"]: i for i in impacts}
    assert set(by_cp) == {"f-a@0", "f-b@0"}  # f-a@1 and f-c@0 untouched -> unmarked
    assert by_cp["f-a@0"]["dir"] == "out" and by_cp["f-a@0"]["coverage"] == "full"
    assert by_cp["f-b@0"]["featureId"] == "f-b"


def test_a_partial_removal_half_drains_rather_than_lying_hollow():
    impacts = _impact(["o3"], [])
    assert impacts == [{"checkpoint": "f-a@1", "featureId": "f-a", "dir": "out",
                        "touched": 1, "coverage": "partial"}]


def test_a_restore_marks_cars_filling_back_in():
    impacts = _impact([], ["o3", "o4", "o5"])
    assert impacts[0]["dir"] == "in" and impacts[0]["coverage"] == "full"


def test_a_keep_revert_that_mostly_adds_reads_as_arriving():
    """A revert-with-kept-dependents can remove and add in the same chapter; the dominant move is
    what the car should look like it is doing."""
    impacts = _impact(["o3"], ["o4", "o5"])
    assert impacts[0]["dir"] == "in"


def test_a_metadata_only_preview_marks_nothing():
    """merge/rename previews carry no removed/added ops -- no car may drain over a label change."""
    assert _impact([], []) == []
    assert _impact(None, None) == []


def test_the_named_chapter_drains_fully_even_when_rewritten_in_place():
    """emit's `removed` can be a subset of the chapter (some ops are rewritten in place rather
    than dropped -- that is why `target_ops` exists), and the asked-about chapter must never
    preview as half-touched or untouched."""
    segs = [{"checkpoint": "f-a@1", "feature_id": "f-a", "op_ids": ["o3", "o4", "o5"]}]
    impacts = _node(_slice("// ---- car-impact", "// ---- end-car-impact"),
                    'console.log(JSON.stringify(classifyCarImpact(["o3"], [], '
                    + json.dumps(segs) + ', ["o3", "o4", "o5"])));\n')
    assert impacts[0]["coverage"] == "full" and impacts[0]["dir"] == "out"


# ── Refusals a person can read ───────────────────────────────────────────────────────────────────
def _humanize(message):
    return _node(_slice("// ---- humanize", "// ---- end-humanize"),
                 f"console.log(JSON.stringify(humanizeRefusal({json.dumps(message)})));\n")


def test_a_dumped_op_id_set_collapses_to_a_count():
    """The kernel's invalid-ideal error printed every id in the ideal. That reached the workbench's
    refusal card verbatim and covered the pane in hex, with the one sentence that mattered buried
    at the top. The count is the diagnostic; the ids never were."""
    ids = ", ".join(f"'{i:064x}'" for i in range(40))
    say = _humanize(f"would leave an invalid (forked) ideal, refused: not a valid ideal "
                    f"(downward-closure or fork-freedom violated): [{ids}]")
    assert "40 op(s)" in say
    assert "0000000000" not in say
    assert say.startswith("would leave an invalid (forked) ideal")
    assert len(say) <= 220


def test_a_stray_id_keeps_a_traceable_prefix():
    say = _humanize("open fork on 1a44e0447df47c8962f8ead01d3b8abc26522b9e3f67856c9b59704c4619afd1")
    assert "1a44e044…" in say


def test_an_ordinary_refusal_passes_through_untouched():
    say = _humanize("'f-abc' has too few members to split")
    assert say == "'f-abc' has too few members to split"


def test_an_empty_refusal_still_says_something():
    assert _humanize("") and _humanize(None)


# ── Retired work: what a restore can actually act on ─────────────────────────────────────────────
def _retired(segs):
    return _node(_slice("// ---- retired-work", "// ---- end-retired-work"),
                 f"console.log(JSON.stringify(retiredWork({json.dumps(segs)})));\n")


def test_a_feature_with_nothing_reverted_has_nothing_to_restore():
    """Restore was a permanently-live button whose only outcome, on an untouched feature, was the
    kernel refusal. A verb with no possible effect must not read as available."""
    out = _retired([{"op_count": 4, "present_op_count": 4}, {"op_count": 2, "present_op_count": 2}])
    assert out["any"] is False and out["edits"] == 0


def test_fully_and_partly_retired_chapters_both_count_their_edits():
    out = _retired([
        {"op_count": 4, "present_op_count": 0},   # fully retired -> 4 edits back
        {"op_count": 5, "present_op_count": 2},   # partly        -> 3 edits back
        {"op_count": 3, "present_op_count": 3},   # live          -> nothing
    ])
    assert out["chapters"] == 1 and out["partial"] == 1
    assert out["edits"] == 7 and out["any"] is True


def test_a_payload_making_no_claim_is_never_called_retired():
    """`present_op_count: null` is an unreadable ideal or an older payload -- no claim. Counting it
    as retired would offer a restore for work that was never gone."""
    assert _retired([{"op_count": 4, "present_op_count": None}])["any"] is False


# ── The inspector's change tree ──────────────────────────────────────────────────────────────────
# `changeTree` turns `--emit`'s per-file before/after pair plus each side's entity spans into the
# file-explorer tree the inspector draws instead of printing every file in full. The contract that
# matters is which side counts as "added": the projection's `before` is always the CURRENT ideal,
# so the work sits in `before` when previewing a revert and in `after` when previewing the restore
# of a retired chapter. Read the wrong way round every feature renders as a pure deletion.


def _tree(files: dict, with_side: str = "before"):
    return _node(_slice("// ---- change-tree", "// ---- end-change-tree"),
                 f"console.log(JSON.stringify(changeTree({json.dumps(files)}, "
                 f"{json.dumps({'withSide': with_side})})));\n")


def _meter(added: int, removed: int, width: int = 5):
    return _node(_slice("// ---- change-tree", "// ---- end-change-tree"),
                 f"console.log(JSON.stringify(changeMeter({added}, {removed}, {width})));\n")


def _diff(a: list[str], b: list[str], max_d: int = 800):
    return _node(_slice("// ---- change-tree", "// ---- end-change-tree"),
                 f"console.log(JSON.stringify(lineDiff({json.dumps(a)}, {json.dumps(b)}, {max_d})));\n")


_FILE = {
    # `helper` is this work's own; `shared` gained a line; `user` it never touched.
    "before": "import os\n\n\ndef helper():\n    return 1\n\n\ndef shared():\n    a = 1\n"
              "    b = 2\n    return a + b\n\n\ndef user():\n    return 0\n",
    "after": "import os\n\n\ndef shared():\n    a = 1\n    return a\n\n\ndef user():\n    return 0\n",
    "before_spans": [
        {"symbol": "m.py::helper", "kind": "function", "start_line": 4, "end_line": 5},
        {"symbol": "m.py::shared", "kind": "function", "start_line": 8, "end_line": 11},
        {"symbol": "m.py::user", "kind": "function", "start_line": 14, "end_line": 15},
    ],
    "after_spans": [
        {"symbol": "m.py::shared", "kind": "function", "start_line": 4, "end_line": 6},
        {"symbol": "m.py::user", "kind": "function", "start_line": 9, "end_line": 10},
    ],
}


def test_the_work_is_on_the_side_the_verb_puts_it_on():
    """Same pair, both verbs. Reverting a live chapter previews its absence, so the work is in
    `before`; restoring a retired one previews its return, so the work is in `after`. The tree has
    to report the same lines as additions either way -- the alternative is a panel that tells the
    reader a feature deleted everything it wrote."""
    revert = _tree({"m.py": _FILE}, "before")
    restore = _tree({"m.py": _FILE}, "after")
    assert revert["added"] == restore["removed"]
    assert revert["removed"] == restore["added"]
    assert revert["added"] > 0 and revert["removed"] > 0


def test_a_changed_line_lands_in_the_entity_that_owns_it():
    """The point of spanning both sides. `helper` exists only in `before` and `shared` only lost a
    line, and the two must not be pooled into one file-level number -- the tree exists so a reader
    can see which function this chapter is about before opening anything."""
    tree = _tree({"m.py": _FILE}, "before")
    file_node = tree["root"]["children"][0]
    assert file_node["kind"] == "file" and file_node["path"] == "m.py"
    by_name = {c["name"]: c for c in file_node["children"]}
    assert by_name["helper"]["added"] == 2 and by_name["helper"]["removed"] == 0
    assert by_name["shared"]["added"] >= 1
    assert "user" not in by_name, "an untouched entity must not get a row"
    assert sum(c["added"] for c in file_node["children"]) == file_node["added"]
    assert sum(c["removed"] for c in file_node["children"]) == file_node["removed"]


def test_lines_outside_every_entity_are_reported_not_dropped():
    """Imports and module-level statements own no entity. Attributing them to the nearest function
    would be a lie and dropping them would make the file row disagree with its own children, so
    they get their own bucket -- and it sorts last, because it is not a thing anyone can point at."""
    pair = {
        "before": "import os\nimport sys\n\n\ndef f():\n    return 1\n",
        "after": "import os\n\n\ndef f():\n    return 1\n",
        "before_spans": [{"symbol": "m.py::f", "kind": "function", "start_line": 5, "end_line": 6}],
        "after_spans": [{"symbol": "m.py::f", "kind": "function", "start_line": 4, "end_line": 5}],
    }
    file_node = _tree({"m.py": pair}, "before")["root"]["children"][0]
    assert [c["name"] for c in file_node["children"]] == ["(top level)"]
    assert file_node["children"][0]["added"] == 1
    assert file_node["children"][0]["symbol"] is None, "the bucket is not clickable as an entity"


def test_a_projection_with_no_spans_still_reports_the_file():
    """A pinned older CLI has no `_side_entity_spans`. The tree degrades to file granularity rather
    than guessing where a function begins -- the guess is the thing this repo parses to avoid."""
    pair = {"before": "a\nb\nc\n", "after": "a\nc\n"}
    file_node = _tree({"m.py": pair}, "before")["root"]["children"][0]
    assert file_node["added"] == 1 and file_node["removed"] == 0
    assert [c["name"] for c in file_node["children"]] == ["(top level)"]


def test_directories_nest_and_a_lone_child_folds_into_one_row():
    """The explorer's compact-folders rule. An inspector this narrow cannot spend a row and an
    indent level on a directory name there is nothing to choose between."""
    pair = {"before": "x\ny\n", "after": "x\n"}
    tree = _tree({"app/pages/one.py": pair, "app/pages/two.py": pair}, "before")
    top = tree["root"]["children"]
    assert [c["name"] for c in top] == ["app/pages"], top
    assert [c["name"] for c in top[0]["children"]] == ["one.py", "two.py"]
    assert top[0]["added"] == 2, "a directory carries its subtree's total"
    assert tree["fileCount"] == 2


def test_an_unchanged_file_never_reaches_the_tree():
    tree = _tree({"m.py": {"before": "a\n", "after": "a\n"}}, "before")
    assert tree["fileCount"] == 0 and tree["root"]["children"] == []


def test_the_diff_finds_the_one_changed_line_in_a_long_file():
    """A sanity check on the alignment itself: prefix/suffix trimming plus Myers should report one
    line each way for a one-line edit, not the whole file."""
    a = [f"line {i}" for i in range(400)]
    b = list(a)
    b[200] = "line 200 edited"
    d = _diff(a, b)
    assert d["aOnly"] == [201] and d["bOnly"] == [201], d
    assert d["capped"] is False


def test_a_change_past_the_cap_reports_everything_rather_than_too_little():
    """The bound is on memory, not on honesty. Past it the aligner stops and the untrimmed middles
    are reported wholesale -- all of it did change -- and the result says so, so the panel can
    caption it instead of quietly showing a smaller number than the truth."""
    a = [f"a{i}" for i in range(60)]
    b = [f"b{i}" for i in range(60)]
    d = _diff(a, b, 4)
    assert d["capped"] is True
    assert len(d["aOnly"]) == 60 and len(d["bOnly"]) == 60


def test_the_meter_never_hides_a_side_that_happened():
    """A 200/1 change drawn as five plus signs says the removal never happened. Both sides keep at
    least one glyph, and the strip stays a fixed width so the column scans."""
    lopsided = _meter(200, 1)
    assert lopsided["plus"] and lopsided["minus"]
    assert len(lopsided["plus"] + lopsided["minus"] + lopsided["rest"]) == 5
    only_added = _meter(9, 0)
    assert only_added["minus"] == "" and only_added["plus"] == "+++++"
    assert _meter(0, 0)["rest"] == "....."


def test_the_real_projection_reaches_the_tree_at_entity_granularity(tmp_path):
    """The seam, end to end: `sgt revert --emit --json` into `changeTree`, no hand-written payload
    in between.

    Both halves above pass with the span keys renamed -- the JS is tested against a fixture and the
    projection against its own shape -- and a rename degrades the panel to file granularity in
    silence, which looks like a design choice rather than a break. This is the only test that fails
    when the two stop agreeing."""
    import os

    from sgt.cli import main
    from sgt.core.lens import get
    from tests.laws import corpus

    repo = tmp_path / "demo"
    corpus._init(repo)
    corpus._write(repo, "app/metrics.py", "import math\n\n\ndef mean(xs):\n    return sum(xs) / len(xs)\n")
    corpus._commit(repo, "mean", 1)
    corpus._write(repo, "app/metrics.py",
                  "import math\nimport statistics\n\n\ndef mean(xs):\n    return sum(xs) / len(xs)\n"
                  "\n\ndef spread(xs):\n    return max(xs) - min(xs)\n")
    corpus._commit(repo, "spread", 2)
    get(repo)

    cwd = os.getcwd()
    os.chdir(repo)
    try:
        import contextlib
        import io

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            main(["revert", "app/metrics.py::spread", "--emit", "--json"])
    finally:
        os.chdir(cwd)
    view = json.loads(out.getvalue())
    assert view["ok"], view

    tree = _tree(view["files"], "before")
    rows = []

    def walk(node):
        for child in node.get("children", []):
            rows.append(child)
            walk(child)

    walk(tree["root"])
    named = {r["name"]: r for r in rows if r["kind"] == "entity"}
    assert "spread" in named, sorted(named)
    assert named["spread"]["added"] == 2 and named["spread"]["removed"] == 0
    assert named["spread"]["symbol"] == "app/metrics.py::spread", "the diff reveal needs the id"
    # The extractor's `__import__::` marker is machinery. It must not reach a row verbatim, and the
    # import must still be its own row -- a revert can take one out on its own.
    assert not any(r["name"].startswith("__") for r in rows), [r["name"] for r in rows]
