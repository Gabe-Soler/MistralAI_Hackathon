"""truth.py tests. Offline with FakeLLM: no network, no keys."""

from __future__ import annotations

from baduser.llm import FakeLLM
from baduser.models import GroundTruth, Invariant
from baduser.truth import accept_clarifications, build_ground_truth, clarify


def _write_repo(tmp_path):
    (tmp_path / "app.py").write_text(
        "from flask import Flask\n"
        "app = Flask(__name__)\n\n"
        "@app.get('/doc/<id>')\n"
        "def get_doc(id):\n"
        "    return db.doc(id)  # missing tenant check\n"
    )
    (tmp_path / "models.py").write_text(
        "class Doc(db.Model):\n"
        "    id = Column(Integer)\n"
        "    tenant_id = Column(Integer, ForeignKey('tenant.id'))\n"
    )
    return str(tmp_path)


async def test_build_drops_uncited_invariants_and_keeps_tenant_isolation(tmp_path):
    repo = _write_repo(tmp_path)
    llm = FakeLLM(json={
        "product_name": "DocApp",
        "product_type": "b2b",
        "domain": "docs",
        "roles": ["admin", "member"],
        "invariants": [
            {"id": "tenant-isolation", "name": "Tenant isolation",
             "rule": "no cross-tenant doc reads", "cite": "app.py:4"},
            {"id": "cited-rule", "name": "Owner only",
             "rule": "only owner edits", "cite": "app.py:5"},
            {"id": "hallucinated", "name": "No cite",
             "rule": "made up rule", "cite": None},          # must be dropped
            {"id": "also-uncited", "name": "Empty cite",
             "rule": "another", "cite": ""},                 # must be dropped
        ],
        "endpoints": ["/doc/<id>"],
        "signup_hint": "POST /register",
    })

    gt = await build_ground_truth(repo, llm)

    ids = {i.id for i in gt.invariants}
    assert "cited-rule" in ids
    assert "hallucinated" not in ids       # uncited dropped (would be a false breach)
    assert "also-uncited" not in ids
    assert "tenant-isolation" in ids       # oracle.tenant_isolation() needs this exact id
    assert gt.tenant_isolation() is not None
    assert gt.tenant_isolation().cite      # tenant-isolation carries a cite
    assert gt.signup_hint == "POST /register"
    assert gt.endpoints == ["/doc/<id>"]


async def test_tenant_isolation_synthesized_when_model_omits_it(tmp_path):
    repo = _write_repo(tmp_path)
    llm = FakeLLM(json={
        "product_name": "DocApp",
        "product_type": "b2c",
        "invariants": [
            {"id": "x", "name": "x", "rule": "x", "cite": "app.py:2"},
        ],
    })
    gt = await build_ground_truth(repo, llm)
    ti = gt.tenant_isolation()
    assert ti is not None and ti.id == "tenant-isolation"
    assert ti.cite  # synthesized with a real cite from a selected file


async def test_clarify_caps_questions_at_three(tmp_path):
    llm = FakeLLM(json={"questions": [
        {"id": f"q{i}", "text": f"question {i}", "rule": f"rule {i}", "cite": f"app.py:{i}"}
        for i in range(6)
    ]})
    gt = GroundTruth(product_name="DocApp", product_type="b2b",
                     invariants=[Invariant(id="tenant-isolation", name="ti", rule="r",
                                           source="code", cite="app.py:1")])
    clars = await clarify(gt, llm)
    assert len(clars) == 3  # hard cap
    assert all(c.invariant.source == "dev" for c in clars)


async def test_accept_clarifications_only_writes_for_yes_answers(tmp_path):
    llm = FakeLLM(json={"questions": [
        {"id": "q1", "text": "enforce A?", "rule": "rule A", "cite": "app.py:1"},
        {"id": "q2", "text": "enforce B?", "rule": "rule B", "cite": "app.py:2"},
        {"id": "q3", "text": "enforce C?", "rule": "rule C", "cite": "app.py:3"},
    ]})
    gt = GroundTruth(product_name="DocApp", product_type="b2b")
    clars = await clarify(gt, llm)

    # q1 answered "y", q2 answered "n", q3 unanswered
    invs = accept_clarifications(clars, {"q1": "y", "q2": "n"})
    ids = {i.id for i in invs}
    assert ids == {"q1"}                         # only the "y" produced an invariant
    assert all(i.source == "dev" for i in invs)
    # an UNANSWERED question must NOT create an invariant
    assert "q3" not in ids
