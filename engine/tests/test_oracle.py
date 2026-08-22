"""Golden fixtures for the oracle (PLAN 14).

The oracle is the product. A silent bug here means the tool reports "clean" and every
downstream tile is green for the wrong reason, so this suite pins down all three verdicts
and both failure directions:

  * false negatives -- a foreign canary in JSON, in prose with no id, several at once
  * false positives -- a CORRECT 403 whose body quotes the very ref it is refusing
  * silent failure  -- an adapter crash must be Verdict.error, never benign
"""

from __future__ import annotations

from pydantic import SecretStr

from baduser.models import (
    Channel,
    Credentials,
    GroundTruth,
    Invariant,
    Manifest,
    Persona,
    ProductType,
    Result,
    Step,
    Tenant,
    Verdict,
)
from baduser.oracle import check, excerpt, looks_like_denial, make_redactor

ACME = "BUACMEQX"  # tenant t_acme's canary
INIT = "BUINITKZ"  # tenant t_init's canary
ALICE_PW = "hunter2-alice-pw"
BOB_PW = "correct-horse-bob"


def manifest() -> Manifest:
    return Manifest(
        tenants=[
            Tenant(id="t_acme", name=f"Acme {ACME} Ltd", canary=ACME),
            Tenant(id="t_init", name=f"Initech {INIT} Inc", canary=INIT),
        ],
        personas=[
            Persona(
                id="p_alice",
                tenant_id="t_acme",
                role="admin",
                name=f"Alice {ACME}",
                email=f"alice+{ACME}@acme.test",
                credentials=Credentials(username="alice", secret=SecretStr(ALICE_PW)),
            ),
            Persona(
                id="p_bob",
                tenant_id="t_init",
                role="admin",
                name=f"Bob {INIT}",
                email=f"bob+{INIT}@init.test",
                credentials=Credentials(
                    username="bob", secret=SecretStr(BOB_PW), token=SecretStr("tok-bob-999999")
                ),
            ),
        ],
        canaries={ACME: "t_acme", INIT: "t_init"},
    )


def truth() -> GroundTruth:
    return GroundTruth(
        product_name="Invoicely",
        product_type=ProductType.b2b,
        invariants=[
            Invariant(
                id="tenant-isolation",
                name="Tenant isolation",
                rule="A user may only read rows belonging to their own tenant.",
                source="code",
                cite="target/app.py:214",
            )
        ],
    )


def step(persona_id: str = "p_bob", channel: Channel = Channel.api, action: str = "read") -> Step:
    return Step(id="s1", persona_id=persona_id, channel=channel, action=action)


# ---------- breaches (no false negatives) ----------


def test_cross_tenant_canary_in_json_is_breach():
    raw = f'{{"id":"inv_1","tenant_id":"t_acme","title":"Q3 hosting {ACME}"}}'
    f = check(truth(), manifest(), step(), Result(status=200, raw=raw), play_id="pl1")
    assert f is not None
    assert f.verdict is Verdict.breach
    assert f.play_id == "pl1"
    assert f.persona_id == "p_bob"
    assert f.channel is Channel.api


def test_cross_tenant_canary_in_prose_with_no_id_is_breach():
    """The chat case: a leak with no id anywhere in it. This is why canaries exist."""
    raw = (
        "Here is what the records say. 'Q3 hosting renewal for Acme "
        f"{ACME} Ltd': the balance outstanding is 84,300 EUR."
    )
    assert "inv_" not in raw and "doc_" not in raw  # nothing an id-matcher could latch onto
    f = check(truth(), manifest(), step(channel=Channel.chat), Result(status=200, raw=raw))
    assert f is not None and f.verdict is Verdict.breach
    assert f.channel is Channel.chat


def test_multiple_foreign_canaries_report_one_breach():
    m = manifest()
    m.canaries["BUTHIRDZZ"] = "t_third"
    raw = f"leaked {ACME} and also BUTHIRDZZ in the same body"
    f = check(truth(), m, step(), Result(status=200, raw=raw))
    assert f is not None and f.verdict is Verdict.breach
    assert ACME in f.evidence or "BUTHIRDZZ" in f.evidence


def test_canary_that_is_a_substring_of_another_canary_still_detects():
    m = manifest()
    m.canaries = {"BUZZ": "t_x", "BUZZTOP": "t_y"}
    long_hit = check(truth(), m, step(), Result(status=200, raw="row BUZZTOP here"))
    assert long_hit is not None and long_hit.verdict is Verdict.breach
    short_hit = check(truth(), m, step(), Result(status=200, raw="row BUZZ here"))
    assert short_hit is not None and short_hit.verdict is Verdict.breach


def test_foreign_canary_that_is_a_substring_of_your_own_canary_is_a_known_false_positive():
    """Documented limitation of substring matching, not a regression to fix here.

    Real canaries are fixed-length run-scoped random strings (PLAN 4), so one being a
    substring of another cannot happen in a real run. Pinned so the behaviour is a
    decision rather than a surprise.
    """
    m = manifest()
    m.canaries = {"OWNER": "t_other", "BUOWNERX": "t_init"}
    f = check(truth(), m, step("p_bob"), Result(status=200, raw="only my own BUOWNERX row"))
    assert f is not None and f.verdict is Verdict.breach  # <- the false positive


# ---------- benign (no false positives) ----------


def test_same_tenant_canary_is_benign():
    raw = f'{{"id":"inv_9","title":"Initech {INIT} retainer"}}'
    assert check(truth(), manifest(), step("p_bob"), Result(status=200, raw=raw)) is None


def test_no_canary_at_all_is_benign():
    raw = '{"invoices": [], "next": null}'
    assert check(truth(), manifest(), step(), Result(status=200, raw=raw)) is None


def test_empty_raw_is_benign():
    assert check(truth(), manifest(), step(), Result(status=200, raw="")) is None
    assert check(truth(), manifest(), step(), Result(status=None, raw="")) is None


def test_correct_403_quoting_the_canary_is_benign_by_status():
    """OK-2, the single most important correctness case: the refusal quotes what it refuses."""
    raw = (
        f'{{"detail":"Access denied: document doc_7 (\'Q3 hosting {ACME}\') belongs to '
        f'another organisation."}}'
    )
    assert ACME in raw  # a naive detector flags this
    assert check(truth(), manifest(), step(), Result(status=403, raw=raw)) is None


def test_correct_denial_text_quoting_the_canary_is_benign_without_a_status():
    """Same case over a channel that has no HTTP status -- chat, web text, a transcript."""
    raw = f"You are not authorized to view 'Q3 hosting {ACME}'."
    assert check(truth(), manifest(), step(channel=Channel.chat), Result(raw=raw)) is None


def test_401_and_404_are_benign():
    for status in (401, 404):
        raw = f'{{"detail":"nope {ACME}"}}'
        assert check(truth(), manifest(), step(), Result(status=status, raw=raw)) is None


# ---------- anonymous persona ----------


def test_anonymous_persona_does_not_crash_and_matches_no_tenant():
    """persona_id absent from the manifest -> owns nothing, so any canary is foreign."""
    f = check(truth(), manifest(), step("anon-probe"), Result(status=200, raw=f"leak {INIT}"))
    assert f is not None and f.verdict is Verdict.breach
    assert f.persona_id == "anon-probe"


def test_anonymous_persona_with_no_canary_is_benign():
    assert check(truth(), manifest(), step("anon-probe"), Result(status=200, raw="{}")) is None


# ---------- errors must never render green ----------


def test_adapter_error_is_error_not_benign():
    f = check(truth(), manifest(), step(), Result(error="ConnectError: refused", raw=""))
    assert f is not None
    assert f.verdict is Verdict.error
    assert f.verdict is not Verdict.benign
    assert "ConnectError" in f.evidence


def test_error_beats_a_denial_status():
    """A crashed adapter that happened to record a 403 is still an error, not a pass."""
    f = check(truth(), manifest(), step(), Result(error="timeout after 120s", status=403))
    assert f is not None and f.verdict is Verdict.error


# ---------- evidence: redaction and windowing ----------


def test_evidence_is_redacted():
    raw = f'{{"password":"{ALICE_PW}","title":"Acme {ACME} payroll"}}'
    f = check(truth(), manifest(), step(), Result(status=200, raw=raw))
    assert f is not None and f.verdict is Verdict.breach
    assert ALICE_PW not in f.evidence
    assert "[REDACTED]" in f.evidence


def test_error_evidence_is_redacted_too():
    f = check(truth(), manifest(), step(), Result(error=f"login failed for {BOB_PW}"))
    assert f is not None and f.verdict is Verdict.error
    assert BOB_PW not in f.evidence


def test_make_redactor_leaves_short_strings_alone():
    m = manifest()
    m.personas[0].credentials.secret = SecretStr("abc")  # < 6 chars: too generic to scrub
    redact = make_redactor(m)
    assert redact("abcdef") == "abcdef"
    assert redact(BOB_PW) == "[REDACTED]"
    assert redact("") == ""


def test_excerpt_centres_on_the_match_and_truncates():
    raw = "x" * 500 + ACME + "y" * 500
    out = excerpt(raw, around=ACME, window=10)
    assert ACME in out
    assert out.startswith("...") and out.endswith("...")
    assert len(out) < 60


def test_excerpt_falls_back_to_the_head_when_the_match_is_absent():
    raw = "z" * 500
    out = excerpt(raw, around="nope", window=10)
    assert out.startswith("z") and out.endswith("...")
    assert len(out) == 23  # window * 2 + the ellipsis
    assert excerpt("") == ""


def test_finding_carries_the_invariant_id_and_cite():
    f = check(truth(), manifest(), step(), Result(status=200, raw=f"leak {ACME}"))
    assert f is not None
    assert f.invariant_id == "tenant-isolation"
    assert f.cite == "target/app.py:214"


def test_finding_without_ground_truth_invariants_has_no_cite():
    gt = GroundTruth(product_name="Invoicely", product_type=ProductType.b2b)
    f = check(gt, manifest(), step(), Result(status=200, raw=f"leak {ACME}"))
    assert f is not None and f.verdict is Verdict.breach
    assert f.invariant_id is None and f.cite is None


def test_looks_like_denial():
    assert looks_like_denial("", 403)
    assert looks_like_denial("", 401)
    assert looks_like_denial("", 404)
    assert looks_like_denial("Access denied", None)
    assert looks_like_denial("Permission denied for that record", 200)
    assert not looks_like_denial('{"invoices":[]}', 200)
    assert not looks_like_denial("", None)
    # only the head of the body is inspected, so a leak cannot hide behind a late "forbidden"
    assert not looks_like_denial("a" * 500 + " forbidden", 200)
