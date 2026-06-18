"""U3 tests: constraint-graph layering, cycle detection, and reshape."""

import pytest

from sgt.orchestrate.constraint import ConstraintError, ConstraintGraph, SubTask


def test_explicit_chain_layers_in_dependency_order():
    g = ConstraintGraph()
    g.add(SubTask("a", "make a"))
    g.add(SubTask("b", "make b", depends_on=["a"]))
    g.add(SubTask("c", "make c", depends_on=["b"]))
    layers = g.layers()
    assert [[t.key for t in layer] for layer in layers] == [["a"], ["b"], ["c"]]


def test_independent_tasks_share_a_layer():
    g = ConstraintGraph()
    g.add(SubTask("a", "make a"))
    g.add(SubTask("b", "make b"))
    layers = g.layers()
    assert len(layers) == 1
    assert {t.key for t in layers[0]} == {"a", "b"}


def test_needs_provides_inference_creates_an_edge():
    g = ConstraintGraph()
    g.add(SubTask("provider", "defines shorten", provides=["shorten"]))
    g.add(SubTask("consumer", "uses shorten", needs=["shorten"]))
    layers = g.layers()
    assert [t.key for t in layers[0]] == ["provider"]
    assert [t.key for t in layers[1]] == ["consumer"]


def test_single_task_is_one_layer_of_one():
    g = ConstraintGraph()
    g.add(SubTask("only", "atomic intent"))
    layers = g.layers()
    assert len(layers) == 1 and len(layers[0]) == 1


def test_cycle_is_detected():
    g = ConstraintGraph()
    g.add(SubTask("a", "a", depends_on=["b"]))
    g.add(SubTask("b", "b", depends_on=["a"]))
    with pytest.raises(ConstraintError):
        g.layers()


def test_add_dependency_relayers():
    g = ConstraintGraph()
    g.add(SubTask("a", "a"))
    g.add(SubTask("b", "b"))
    assert len(g.layers()) == 1  # independent
    g.add_dependency("b", "a")   # reshape: b now depends on a
    layers = g.layers()
    assert [t.key for t in layers[0]] == ["a"]
    assert [t.key for t in layers[1]] == ["b"]


def test_unknown_dependency_rejected():
    g = ConstraintGraph()
    g.add(SubTask("a", "a"))
    with pytest.raises(ConstraintError):
        g.add_dependency("a", "missing")


def test_duplicate_key_rejected():
    g = ConstraintGraph()
    g.add(SubTask("a", "a"))
    with pytest.raises(ConstraintError):
        g.add(SubTask("a", "again"))
