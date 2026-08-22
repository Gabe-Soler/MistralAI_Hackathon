"""Every prompt the browser agent sees, in one file.

Prompt tuning is the thing you do most during rehearsals, so it lives apart from adapter
logic. Three task types with different success criteria, one shared safety block.

Secrets never appear here. Task text carries the placeholders `x_email` / `x_secret`;
web.py hands the real values to Browser Use as `sensitive_data`, so the model never sees
them (PLAN 11b rule 4).
"""

from __future__ import annotations

# The prompt half of the destructive-action guard. The other half is enforcement in
# channels/web.py -- a prompt is guidance, not a control, and an LLM exploring a UI will
# click "Delete account" while "having a look around".
SAFETY = """
Hard rules, they override the task:
- Never delete, remove, archive, cancel, deactivate, or reset anything.
- Never change another person's account, password, role, or settings.
- Never accept a payment, place an order, or send an external email or message.
- Do not use a URL containing: delete, destroy, remove, purge, wipe, cancel, deactivate.
- If the only way to finish the task is one of the above, STOP and report what blocked you.
You are a tester on a disposable test system. Nothing here is worth breaking to finish.
""".strip()

IDENTITY = (
    "If a form asks who you are, the email is x_email and the password is x_secret. "
    "Type those placeholders literally -- they are substituted for you."
)


def _wrap(target: str, body: str, report: str, *, identity: bool) -> str:
    parts = [f"You are using the web app at {target}." if target else "", body]
    if identity:
        parts.append(IDENTITY)
    parts += [f"When you are done, report: {report}", SAFETY]
    return "\n\n".join(p for p in parts if p)


def signup_task(target: str, tenant_name: str, person_name: str) -> str:
    """Create an account. Must adapt to a form we have never seen (PLAN 11c)."""
    return _wrap(
        target,
        "Create a new account for this organisation. Find the sign up / register / "
        "get started link and complete whatever the form asks for.\n"
        f"  Organisation / company: {tenant_name}\n"
        f"  Full name: {person_name}\n"
        "Use the email and password below for the credential fields. Copy names EXACTLY, "
        "character for character, including any odd-looking code in them -- they are how "
        "we identify this data later.",
        "the exact organisation name you entered, and the account id or profile URL if "
        "the app shows one.",
        identity=True,
    )


def create_task(target: str, kind: str, title: str, body: str) -> str:
    """Create a document/invoice whose content carries the tenant's canary."""
    return _wrap(
        target,
        f"You are signed in. Create a new {kind}.\n"
        f"  Title: {title}\n"
        f"  Body / description: {body}\n"
        "Copy the title and body EXACTLY, character for character, including any "
        "odd-looking codes -- they must be stored verbatim.",
        f"the id or URL of the {kind} you created, and its title as saved.",
        identity=False,
    )


def probe_task(target: str, instruction: str) -> str:
    """An attack step. Read-only by construction: look, report, never change."""
    return _wrap(
        target,
        f"{instruction}\n"
        "Look only. Do not create, edit, or submit anything unless the instruction above "
        "explicitly says to.",
        "everything you could actually see, quoted verbatim -- names, titles, amounts, "
        "ids, and any text on screen. Quote it exactly rather than summarising; if you "
        "saw nothing, say so plainly.",
        identity=False,
    )
