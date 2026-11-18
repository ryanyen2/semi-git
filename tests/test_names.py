"""Names a person or agent gave (`ugh.names`), the address grammar (`ugh.address`), and the surfaces
that read them: `log`, `find`, `show` and `plan`. Small repos in `tmp_path`, through the CLI."""

from __future__ import annotations

import json
import os

import pytest

from ugh import names
from ugh.address import AddressError, Record, resolve
from ugh.cli import main
from ugh.core import sync
from ugh.core.lens import get
from ugh.store.gitbind import GitBinding
from tests.core.test_sync import _BASE, _push, _two_clones
from tests.laws import corpus


def _in(repo, argv):
    cwd = os.getcwd()
    os.chdir(repo)
    try:
        return main(argv)
    finally:
        os.chdir(cwd)


def _json(repo, argv, capsys):
    capsys.readouterr()
    rc = _in(repo, [*argv, "--json"])
    return rc, json.loads(capsys.readouterr().out)


CART = "def total(items):\n    return sum(items)\n"
WITH_TAX = CART + "\n\ndef tax(t):\n    return t * 0.2\n"
TAX_UP = CART + "\n\ndef tax(t):\n    return t * 0.25\n"
WITH_EXPORT = TAX_UP + "\n\ndef export(rows):\n    return list(rows)\n"


def _cart(tmp_path):
    repo = tmp_path / "cart"
    corpus._init(repo)
    corpus._write(repo, "cart.py", CART)
    corpus._commit(repo, "the cart", 1)
    get(repo)
    return repo


def _save(repo, capsys, text, message, *extra):
    (repo / "cart.py").write_text(text, encoding="utf-8")
    rc, out = _json(repo, ["save", "-m", message, *extra], capsys)
    assert rc == 0 and out["saved"], out
    return out


def _addresses(repo, token):
    rec = Record(repo)
    return sorted(rec.address(o) for o in resolve(repo, token, rec) if o in rec.units)


# -- names ----------------------------------------------------------------------------------------

def test_save_as_names_exactly_that_saves_steps(tmp_path, capsys):
    repo = _cart(tmp_path)
    out = _save(repo, capsys, WITH_TAX, "add the tax", "--as", "checkout")
    sha = out["commit"][:7]
    assert {s["name"] for s in out["steps"]} == {"checkout"}
    assert _addresses(repo, "checkout") == sorted(s["address"] for s in out["steps"])
    assert all(a.endswith(f"@{sha}") for a in _addresses(repo, "checkout"))


def test_a_later_save_to_a_named_definition_is_unnamed(tmp_path, capsys):
    repo = _cart(tmp_path)
    _save(repo, capsys, WITH_TAX, "add the tax", "--as", "checkout")
    later = _save(repo, capsys, TAX_UP, "raise the tax")
    assert [s["name"] for s in later["steps"]] == [None]
    assert f"cart.py::tax@{later['commit'][:7]}" not in _addresses(repo, "checkout")


def test_name_moves_steps_from_one_label_to_another(tmp_path, capsys):
    repo = _cart(tmp_path)
    out = _save(repo, capsys, WITH_TAX, "add the tax", "--as", "checkout")
    tax = f"cart.py::tax@{out['commit'][:7]}"
    rc, named = _json(repo, ["name", tax, "billing"], capsys)
    assert rc == 0 and [s["address"] for s in named["steps"]] == [tax]
    assert tax in _addresses(repo, "billing") and tax not in _addresses(repo, "checkout")


def test_rename_onto_an_existing_label_merges_the_two(tmp_path, capsys):
    repo = _cart(tmp_path)
    _save(repo, capsys, WITH_TAX, "add the tax", "--as", "checkout")
    _save(repo, capsys, TAX_UP, "raise the tax", "--as", "billing")
    rc, out = _json(repo, ["rename", "billing", "checkout"], capsys)
    assert rc == 0 and out["merged"] is True
    assert set(names.load(repo).values()) == {"checkout"}
    assert len(_addresses(repo, "checkout")) == 3


def _shared_op(repo, symbol):
    rec = Record(repo)
    return next(o for o, us in rec.units.items() if symbol in us)


def test_sync_takes_a_rename_made_on_one_side(tmp_path):
    a, b = _two_clones(tmp_path, _BASE)
    foo = _shared_op(a, "main.py::foo")
    names.name(a, [foo], "first")
    _push(a)
    sync.sync(b, remote="origin", branch="main")
    names.rename(b, "first", "second")
    _push(b)
    sync.sync(a, remote="origin", branch="main")
    assert names.load(a) == names.load(b) == {foo: "second"}


def test_sync_keeps_ours_when_both_sides_rename_an_op_and_reports_it(tmp_path, capsys):
    a, b = _two_clones(tmp_path, _BASE)
    foo = _shared_op(a, "main.py::foo")
    names.name(a, [foo], "first")
    _push(a)
    sync.sync(b, remote="origin", branch="main")
    names.rename(a, "first", "theirs")
    _push(a)
    names.rename(b, "first", "ours")
    rc, out = _json(b, ["sync", "origin", "main"], capsys)
    assert names.load(b) == {foo: "ours"}
    assert out["name_conflicts"] == [{"address": Record(b).address(foo), "ours": "ours",
                                      "theirs": "theirs"}]


# -- addresses ------------------------------------------------------------------------------------

@pytest.fixture
def cart3(tmp_path, capsys):
    """Three saves after the cart: the tax (named checkout), the tax raised, an export; the export
    then taken out."""
    repo = _cart(tmp_path)
    s1 = _save(repo, capsys, WITH_TAX, "add the tax", "--as", "checkout")["commit"]
    s2 = _save(repo, capsys, TAX_UP, "raise the tax")["commit"]
    s3 = _save(repo, capsys, WITH_EXPORT, "add the export")["commit"]
    assert _in(repo, ["revert", f"cart.py::export@{s3[:7]}", "--yes"]) == 0
    return repo, s1[:7], s2[:7], s3[:7]


def test_every_grammar_form_resolves_to_its_steps(cart3):
    repo, s1, s2, s3 = cart3
    assert _addresses(repo, "checkout") == sorted([f"cart.py::tax@{s1}", f"cart.py::total@{s1}"])
    assert _addresses(repo, f"checkout@{s1}") == _addresses(repo, "checkout")
    assert _addresses(repo, f"checkout@{s2}") == []
    # A save that adds a definition after `tax` changes the gap after it, which is `tax`'s too.
    assert {f"cart.py::tax@{s1}", f"cart.py::tax@{s2}", f"cart.py::tax@{s3}"} <= set(_addresses(repo, "cart.py::tax"))
    assert _addresses(repo, f"cart.py::tax@{s2}") == [f"cart.py::tax@{s2}"]
    assert _addresses(repo, s2) == [f"cart.py::tax@{s2}"]
    assert f"cart.py::export@{s3}" in _addresses(repo, f"{s1}..")
    assert f"cart.py::tax@{s1}" not in _addresses(repo, f"{s1}..")
    assert _addresses(repo, f"{s1}..{s2}") == [f"cart.py::tax@{s2}"]
    assert any(a.startswith("cart.py::export@") for a in _addresses(repo, "cart.py"))


def test_an_address_reaches_steps_that_are_out(cart3):
    repo, _s1, _s2, s3 = cart3
    rec = Record(repo)
    (op,) = resolve(repo, f"cart.py::export@{s3}", rec)
    assert rec.step(op)["out"] is True


def test_a_token_that_is_a_label_and_a_rev_is_refused_naming_both(cart3):
    repo, s1, _s2, _s3 = cart3
    subprocess_run = __import__("subprocess").run
    subprocess_run(["git", "-C", str(repo), "branch", "checkout"], check=True)
    with pytest.raises(AddressError) as e:
        resolve(repo, "checkout")
    assert "the name 'checkout'" in str(e.value) and "the save" in str(e.value)


def test_an_unknown_token_is_refused_with_find_hits(cart3):
    repo, *_ = cart3
    with pytest.raises(AddressError) as e:
        resolve(repo, "the tax thing")
    assert "no such address" in str(e.value) and "cart.py::tax" in e.value.hits


@pytest.mark.parametrize("verb", ["revert", "restore", "name", "show"])
def test_verbs_refuse_free_text(cart3, capsys, verb):
    repo, *_ = cart3
    argv = [verb, "remove the tax please"] + (["label"] if verb == "name" else [])
    rc, out = _json(repo, argv, capsys)
    assert rc == 2 and out["ok"] is False and "no such address" in out["message"]


# -- log, find, show ------------------------------------------------------------------------------

def test_log_lists_names_unnamed_and_out_per_save(cart3, capsys):
    repo, s1, s2, s3 = cart3
    capsys.readouterr()
    assert _in(repo, ["log"]) == 0
    lines = {line.split()[0]: line for line in capsys.readouterr().out.splitlines()}
    assert "checkout ×2" in lines[s1] and "unnamed" not in lines[s1]
    assert "1 unnamed" in lines[s2]
    assert "1 out" in lines[s3]


def test_log_json_has_the_saves_shape(cart3, capsys):
    repo, s1, _s2, s3 = cart3
    rc, view = _json(repo, ["log"], capsys)
    assert rc == 0 and set(view) == {"saves"}
    newest = view["saves"][0]
    assert set(newest) == {"sha", "subject", "date", "steps"}
    assert newest["sha"].startswith(s3) and newest["subject"] == "add the export"
    assert {"address": f"cart.py::export@{s3}", "kind": "create", "name": None, "out": True} in newest["steps"]
    assert all(set(st) == {"address", "kind", "name", "out"} for s in view["saves"] for st in s["steps"])


def test_find_returns_addresses_for_a_label_a_subject_and_a_definition(cart3, capsys):
    repo, _s1, s2, _s3 = cart3
    hits = lambda q: {(h["address"], h["matched"]) for h in _json(repo, ["find", q], capsys)[1]["hits"]}  # noqa: E731
    assert ("checkout", "name") in hits("checkout")
    assert (s2, "subject") in hits("raise")
    assert ("cart.py::export", "definition") in hits("export")


def test_show_of_a_name_includes_the_planners_answer(cart3, capsys):
    repo, s1, _s2, _s3 = cart3
    rc, view = _json(repo, ["show", "checkout"], capsys)
    assert rc == 0 and view["ok"]
    assert [s["sha"][:7] for s in view["saves"]] == [s1]
    assert view["saves"][0]["ask"]["text"] == "add the tax"
    plan = view["plan"]
    assert plan["verb"] == "revert" and plan["removal_set"]["steps"] >= 1
    assert all("address" in row for row in plan["undone"] + plan["held"])


# -- plan -----------------------------------------------------------------------------------------

def test_plan_reports_built_missing_and_unnamed_and_done_clears(tmp_path, capsys):
    repo = _cart(tmp_path)
    assert _in(repo, ["plan", "tax", "export"]) == 0
    _save(repo, capsys, WITH_TAX, "add the tax", "--as", "tax")
    unnamed = _save(repo, capsys, TAX_UP, "raise the tax")
    rc, out = _json(repo, ["plan"], capsys)
    plan = out["plan"]
    assert plan["baseline"] == GitBinding(repo).rev_parse("HEAD~2")
    built = {row["name"]: [s["subject"] for s in row["saves"]] for row in plan["names"]}
    assert built == {"tax": ["add the tax"], "export": []}
    assert [st["address"] for st in plan["other"]] == [f"cart.py::tax@{unnamed['commit'][:7]}"]
    assert _in(repo, ["plan", "--done"]) == 0
    assert _json(repo, ["plan"], capsys)[1]["plan"] is None
