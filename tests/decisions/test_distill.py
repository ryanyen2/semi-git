"""U8 — rationale distillation: offline degrades to no-op; a (faked) client populates the sidecar."""

import json

from sgt.decisions.distill import distill_all
from sgt.decisions.store import build_decisions
from sgt.effects.model import Effect
from sgt.project import Project
from sgt.store.graph import Node, NodeKind


def _proj(tmp_path):
    proj = Project.init(tmp_path)
    proj.add_feature(
        Node(id="embedding", kind=NodeKind.CAPABILITY, intent="embedding model"),
        [Effect.add_def("embedding.py", "embed", "def embed(t):\n    return [t]")],
    )
    proj.log.stamp_committed()
    proj.save()
    return proj


class _FakeClient:
    """Stands in for the OpenAI client — records the prompt, returns canned rationale."""

    def __init__(self):
        self.seen_prompt = ""
        self.chat = self

    @property
    def completions(self):
        return self

    def create(self, *, model, messages, response_format):
        self.seen_prompt = messages[-1]["content"]
        payload = {
            "context": "no embedding existed yet",
            "consequence": "embed(t) returns a vector",
            "alternatives": [{"option": "bag-of-words", "why_rejected": "weak recall"}],
        }
        msg = type("M", (), {"content": json.dumps(payload)})
        return type("R", (), {"choices": [type("C", (), {"message": msg})]})


def test_offline_distill_is_a_noop(tmp_path, monkeypatch):
    import sgt.decisions.distill as d

    monkeypatch.setattr(d, "get_client", lambda repo: (_ for _ in ()).throw(RuntimeError("no key")))
    assert distill_all(_proj(tmp_path)) == 0  # degrades, never raises


def test_distilled_rationale_lands_in_the_sidecar_and_view(tmp_path):
    proj = _proj(tmp_path)
    fake = _FakeClient()
    n = distill_all(proj, client=fake)
    assert n == 1
    # the prompt is generic — it carries the intent + code, not the expected answer (no seeding)
    assert "embed" in fake.seen_prompt and "bag-of-words" not in fake.seen_prompt
    # rationale now flows through build_decisions (read from the sidecar)
    dec = {d.id: d for d in build_decisions(proj)}["embedding@1"]
    assert dec.intent.context == "no embedding existed yet"
    assert dec.intent.consequence == "embed(t) returns a vector"
    assert dec.alternatives[0].option == "bag-of-words"
    assert dec.alternatives[0].source == "distilled" and dec.alternatives[0].confidence == "low"


def test_distill_skips_already_authored(tmp_path):
    proj = _proj(tmp_path)
    distill_all(proj, client=_FakeClient())
    # second pass without --force distills nothing new (context already present)
    assert distill_all(proj, client=_FakeClient()) == 0
