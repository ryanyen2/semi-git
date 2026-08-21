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
