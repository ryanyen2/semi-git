"""The clustering / merge suggestion queue (plan U7).

A suggestion is a *proposal* -- content-addressed like a review record, but stored in a local
dismissable table rather than a committed G-Set. These tests pin the record's content-addressing,
the add/list/dismiss lifecycle, and `suggestion_view`'s projection.
"""

from __future__ import annotations

import pytest

from sgt.core import suggest


def test_add_load_and_dismiss_lifecycle(tmp_path):
    r = suggest.add(tmp_path, "conflict", ["F-a", "F-b"], ["op1", "op2"], "x claimed by two lanes")
    assert suggest.load(tmp_path, r.id) == r
    assert [s.id for s in suggest.all_records(tmp_path)] == [r.id]

    assert suggest.dismiss(tmp_path, r.id) is True
    assert suggest.all_records(tmp_path) == []
    assert suggest.dismiss(tmp_path, r.id) is False  # already gone -> no-op, returns False


def test_content_addressed_by_kind_and_op_set(tmp_path):
    """Re-adding the same (kind, op-set) is a no-op on identity (one record); a different kind over
    the same ops, or a different op-set, is a distinct record."""
    a = suggest.add(tmp_path, "merge", ["F-a", "F-b"], ["op1", "op2"], "couple")
    b = suggest.add(tmp_path, "merge", ["F-a", "F-b"], ["op2", "op1"], "again (reordered)")
    assert a.id == b.id and len(suggest.all_records(tmp_path)) == 1  # order-independent, deduped

    other_kind = suggest.add(tmp_path, "conflict", ["F-a", "F-b"], ["op1", "op2"], "same ops")
    assert other_kind.id != a.id  # kind is part of the key
    other_ops = suggest.add(tmp_path, "merge", ["F-a", "F-b"], ["op3"], "different ops")
    assert other_ops.id != a.id
    assert len(suggest.all_records(tmp_path)) == 3


def test_bad_kind_or_empty_op_set_refuses(tmp_path):
    with pytest.raises(ValueError):
        suggest.add(tmp_path, "nonsense", ["F-a"], ["op1"])
    with pytest.raises(ValueError):
        suggest.add(tmp_path, "merge", ["F-a"], [])


def test_suggestion_view_projects_the_open_queue(tmp_path):
    from sgt.api import suggestion_view

    assert suggestion_view(tmp_path) == {"count": 0, "suggestions": []}
    suggest.add(tmp_path, "conflict", ["F-a", "F-b"], ["op1"], "dual membership")
    v = suggestion_view(tmp_path)
    assert v["count"] == 1
    s = v["suggestions"][0]
    assert s["kind"] == "conflict" and s["features"] == ["F-a", "F-b"] and s["rationale"] == "dual membership"


def test_cli_list_and_dismiss(tmp_path, capsys, monkeypatch):
    from sgt.cli.suggestions import _suggestions

    r = suggest.add(tmp_path, "merge", ["F-a", "F-b"], ["op1"], "couple")
    monkeypatch.chdir(tmp_path)

    assert _suggestions(".", "list", [], as_json=False) == 0
    assert r.id in capsys.readouterr().out

    assert _suggestions(".", "dismiss", [r.id], as_json=False) == 0
    assert "dismissed 1" in capsys.readouterr().out
    assert suggest.all_records(tmp_path) == []
