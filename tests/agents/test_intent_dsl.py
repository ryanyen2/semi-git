"""The intent DSL parser/renderer — deterministic, offline (no API key)."""

from sgt.agents import intent_dsl
from sgt.agents.intent_dsl import parse, render


# -- ADD --------------------------------------------------------------------
def test_add_parses_names_using_and_because():
    p = parse("ADD validate_email, normalize_email USING re, smtplib BECAUSE regex was brittle")
    assert p is not None and p.verb == "ADD"
    assert p.provides == ["validate_email", "normalize_email"]
    assert p.needs == ["re", "smtplib"]
    assert p.context == "regex was brittle"
    assert p.alternative is None          # BECAUSE on ADD is context, not a rejected option
    assert not p.is_revise
    assert p.canonical == "ADD validate_email, normalize_email USING re, smtplib BECAUSE regex was brittle"


def test_add_and_separator_and_minimal():
    assert parse("ADD shorten and expand").provides == ["shorten", "expand"]
    p = parse("ADD shorten")
    assert p.provides == ["shorten"] and p.needs == [] and p.context is None


# -- EXTEND / REPLACE / REMOVE (revise verbs) -------------------------------
def test_extend_targets_a_lane():
    p = parse("EXTEND auth TO support API keys")
    assert p.verb == "EXTEND" and p.is_revise and p.target == "auth"
    assert p.provides == [] and "support API keys" in p.intent


def test_replace_records_alternative():
    p = parse("REPLACE bubble_sort WITH quicksort BECAUSE O(n^2) too slow")
    assert p.verb == "REPLACE" and p.is_revise and p.target == "bubble_sort"
    assert p.alternative == ("bubble_sort", "O(n^2) too slow")
    assert p.canonical == "REPLACE bubble_sort WITH quicksort BECAUSE O(n^2) too slow"


def test_remove_with_and_without_from():
    p = parse("REMOVE legacy_login, legacy_logout FROM auth")
    assert p.verb == "REMOVE" and p.target == "auth"
    assert "legacy_login, legacy_logout" in p.intent
    assert parse("REMOVE dead_helper").target is None


# -- freeform is left for the LLM (None), uppercase verb is the opt-in ------
def test_lowercase_and_prose_is_freeform():
    assert parse("add email validation to the form") is None   # lowercase -> not canonical
    assert parse("please refactor the parser") is None
    assert parse("ADD") is None                                  # verb alone is not a statement


def test_malformed_revise_forms_are_freeform():
    assert parse("REPLACE bubble_sort") is None                  # no WITH
    assert parse("EXTEND auth") is None                          # no TO/WITH behavior


# -- render round-trips a node back to canonical ----------------------------
def test_render_new_and_revise():
    assert render(provides=["a", "b"], needs=["c"], intent="x") == "ADD a, b USING c"
    assert render(provides=[], needs=[], intent="support API keys", lane="auth").startswith("EXTEND auth TO")


def test_parse_then_render_is_stable_for_add():
    p = parse("ADD a, b USING c")
    assert render(provides=p.provides, needs=p.needs, intent=p.intent) == p.canonical


# -- normalize degrades to [] offline (no client failure leaks) -------------
def test_normalize_returns_empty_on_client_failure():
    class _Boom:
        class chat:
            class completions:
                @staticmethod
                def create(**_):
                    raise RuntimeError("no key")

    assert intent_dsl.normalize("do a thing", {}, client=_Boom(), model="gpt-4o") == []
