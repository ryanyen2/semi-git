"""The project scripts: each evolves a real-ish codebase through ~10 mixed lifecycle moves.

Each function receives a `Driver` and issues moves (`plan` / `revert` / `suspend` / `restore`),
each carrying a one-line **expectation** of what the resulting graph should look like — the thing we
compare reality against. Selectors for lifecycle moves are substrings of a decision's slug/intent.
"""

from __future__ import annotations


def data_analysis(d) -> None:
    """A small tabular-analytics toolkit (data analysis domain)."""
    d.plan("load a CSV file into a list of row dicts, and compute summary statistics "
           "(mean and median) for a given numeric column",
           expect="~2 lanes: a csv loader, a stats function that builds-on the loader")
    d.plan("add a function to filter rows by a column/value predicate, and a function to "
           "group rows by a column and aggregate (count, sum) per group",
           expect="2 new lanes (filter, groupby), each builds-on the loader")
    d.plan("enhance the summary statistics to also report standard deviation and the 25/50/75 "
           "percentiles for the column",
           expect="FOLDS into the stats lane as a revise (same provides) — not a new lane")
    d.suspend("group", expect="the groupby lane suspends; loader/stats/filter unaffected")
    d.plan("add a function that writes the summary statistics out to a JSON report file",
           expect="1 new lane that builds-on stats")
    d.revert("filter", expect="filter lane plugged out by dependency closure")
    d.restore("group", expect="groupby lane restored to the frontier")


def web_api(d) -> None:
    """A small task-tracker HTTP API (web/API domain)."""
    d.plan("create an in-memory Task store with add, get, and list operations",
           expect="1 lane: the store capability")
    d.plan("add HTTP route handlers to create a task and to list tasks, using the task store",
           expect="1 lane (routes) that builds-on the store")
    d.plan("add request validation that rejects a task with an empty title, used by the create route",
           expect="1 lane (validation) the routes build-on")
    d.plan("add a function to serialize a task to a JSON-safe dict for responses",
           expect="1 lane, used by the routes")
    d.suspend("validation", expect="validation suspended; routes/store remain")
    d.plan("add update and delete operations to the task store",
           expect="FOLDS into the store lane as a revise (provides extend the store)")
    d.revert("serialize", expect="serializer plugged out")
    d.restore("validation", expect="validation restored")


def ml_pipeline(d) -> None:
    """A tiny ML training/eval pipeline (ML domain)."""
    d.plan("load a dataset of (features, label) rows and split it into train and test sets",
           expect="1 lane: data loading/splitting")
    d.plan("implement a logistic-regression model with fit and predict methods, trained on the split",
           expect="1 lane (model) that builds-on the loader/split")
    d.plan("add an evaluation function computing accuracy and F1 from predictions and labels",
           expect="1 lane (eval) that builds-on the model")
    d.plan("add a train_and_evaluate entry point that wires loading, fitting, and evaluation together",
           expect="1 lane (pipeline) building on loader+model+eval — the integration head")
    d.suspend("eval", expect="eval suspends; pipeline that needs it should reflect the gap")
    d.plan("swap the model to a decision tree with the same fit/predict interface",
           expect="FOLDS into the model lane as a revise (same provides)")
    d.restore("eval", expect="eval restored")
    d.revert("decision tree", expect="the model revision reverts; lane falls back to prior model")


def benchmark(d) -> None:
    """A micro-benchmark harness (benchmark domain)."""
    d.plan("implement a timer utility that measures the wall-clock runtime of a callable over N runs",
           expect="1 lane: the timer")
    d.plan("add two sort implementations to benchmark: a bubble sort and a quicksort",
           expect="1 lane (or 2) for the sort implementations under test")
    d.plan("add a runner that benchmarks each sort with the timer and collects results per algorithm",
           expect="1 lane (runner) building on timer + sorts")
    d.plan("add a reporter that formats the collected results into a comparison table",
           expect="1 lane (reporter) building on the runner")
    d.suspend("bubble", expect="if bubble is its own lane it suspends; else surfaces granularity")
    d.plan("add a memory-usage measurement alongside wall-clock time in the timer",
           expect="FOLDS into the timer lane as a revise")
    d.revert("reporter", expect="reporter plugged out")
    d.restore("bubble", expect="restored")


def cli_tool(d) -> None:
    """A note-taking CLI (developer-tool domain)."""
    d.plan("implement a notes store backed by a JSON file with add_note and all_notes functions",
           expect="1 lane: the store")
    d.plan("add an argparse-based CLI with `add` and `list` subcommands that call the notes store",
           expect="1 lane (cli) building on the store")
    d.plan("add full-text search over notes, exposed as a `search` subcommand",
           expect="1 lane (search) building on store; cli may revise to add the subcommand")
    d.plan("add tagging: notes can carry tags and be filtered by tag",
           expect="likely FOLDS into the store lane (revise) + touches cli")
    d.suspend("search", expect="search suspended")
    d.plan("add a `delete` subcommand and a delete_note store function",
           expect="touches store (revise) and cli (revise)")
    d.revert("tag", expect="tagging plugged out")
    d.restore("search", expect="search restored")


PROJECTS = {
    "data_analysis": data_analysis,
    "web_api": web_api,
    "ml_pipeline": ml_pipeline,
    "benchmark": benchmark,
    "cli_tool": cli_tool,
}
