"""The whole violation detector. See PLAN.md section 14.

Detection is a canary scan: every tenant's strings carry a unique tag, so any response
that exposes one tenant's data to another necessarily contains that tag. One check,
identical across API JSON, page text, chat prose, and a voice transcript.

The critical property is that this FAILS VISIBLY. An adapter crash becomes Verdict.error,
never a green tile -- the previous design's "return True when we can't judge" meant every
bug in the system rendered as a passing app.
"""

from __future__ import annotations

import re
import uuid

from .models import Finding, GroundTruth, Manifest, Result, Step, Verdict

# A denial that quotes the thing it is denying is CORRECT behaviour, not a leak.
# "You are not authorized to view BU7Q4KX2" must never be scored as a breach.
_DENIAL = re.compile(
    r"\b(not\s+authori[sz]ed|unauthori[sz]ed|forbidden|access\s+denied|permission\s+denied"
    r"|you\s+do\s+not\s+have|no\s+access|not\s+allowed|404|not\s+found)\b",
    re.IGNORECASE,
)


def looks_like_denial(raw: str, status: int | None) -> bool:
    """True when the app is correctly refusing. Checked before any canary match."""
    if status is not None and status in (401, 403, 404):
        return True
    return bool(_DENIAL.search(raw[:400]))


def excerpt(raw: str, around: str | None = None, window: int = 120) -> str:
    """Evidence window. `around` centres it on the match; otherwise take the head.

    One function, one signature -- the plan briefly had two incompatible `excerpt`s.
    """
    if not raw:
        return ""
    if around and around in raw:
        i = raw.index(around)
        lo = max(0, i - window)
        hi = min(len(raw), i + len(around) + window)
        return ("..." if lo else "") + raw[lo:hi] + ("..." if hi < len(raw) else "")
    return raw[: window * 2] + ("..." if len(raw) > window * 2 else "")


def make_redactor(manifest: Manifest):
    """Scrubber built FROM the manifest -- we generated every secret, so it is a lookup.

    Regex tiers are deliberately deferred (PLAN 11b): pattern-matching for secrets we did
    not create is a different job from hiding the ones we did.
    """
    secrets = sorted({s for s in manifest.secrets() if len(s) >= 6}, key=len, reverse=True)

    def redact(text: str) -> str:
        if not text:
            return text
        for s in secrets:
            text = text.replace(s, "[REDACTED]")
        return text

    return redact


def check(
    gt: GroundTruth,
    m: Manifest,
    step: Step,
    result: Result,
    play_id: str = "",
    redact=None,
) -> Finding | None:
    """Return a Finding, or None when the step was benign.

    Order matters:
      1. adapter/target failure  -> error   (never green)
      2. app correctly refused   -> benign  (a denial may legitimately quote the ref)
      3. foreign canary present  -> breach
    """
    redact = redact or make_redactor(m)

    if result.error:
        return Finding(
            id=str(uuid.uuid4())[:8],
            play_id=play_id,
            persona_id=step.persona_id,
            channel=step.channel,
            action=step.action,
            verdict=Verdict.error,
            evidence=redact(result.error)[:400],
        )

    if looks_like_denial(result.raw, result.status):
        return None

    actor = m.persona(step.persona_id)
    mine = actor.tenant_id if actor else None  # None == anonymous; matches no tenant

    for canary, owner_tenant in m.canaries.items():
        if canary in result.raw and owner_tenant != mine:
            inv = gt.tenant_isolation()
            return Finding(
                id=str(uuid.uuid4())[:8],
                play_id=play_id,
                persona_id=step.persona_id,
                channel=step.channel,
                action=step.action,
                verdict=Verdict.breach,
                invariant_id=inv.id if inv else None,
                cite=inv.cite if inv else None,
                evidence=redact(excerpt(result.raw, around=canary)),
            )

    return None
