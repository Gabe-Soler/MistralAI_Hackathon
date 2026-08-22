"""Multi-tenant invoice tracker -- the Bad User target app / eval fixture (PLAN 22 step 0).

This stands in for the app Mistral vibe-codes on stage: open signup, no CAPTCHA, no email
verification, bearer-or-cookie auth, free-text title/body fields that canaries live in.

It is deliberately wrong in exactly four id-stamped ways and deliberately RIGHT in four
more, so evals/run_eval.py can assert both directions -- every planted bug detected, zero
findings on the correct routes.

  BUG-1  IDOR on GET /api/invoices/{id}      -- no tenant check
  BUG-2  Listing leak on GET /api/invoices   -- returns every tenant's rows
  BUG-3  Chat leak on POST /api/chat         -- answers from every tenant's content, no ids
  BUG-4  Invite accept silently grants membership in the inviter's tenant

  OK-1   GET /api/invoices/{id} you own            -> 200
  OK-2   GET /api/documents/{id} cross-tenant      -> 403 whose body QUOTES id and title
  OK-3   GET /api/me                               -> only the caller's own data
  OK-4   any protected route with no credentials   -> 401

Signup joins an existing company by name. That is how the seeder puts several personas in
one tenant; it is not one of the graded bugs.
"""

from __future__ import annotations

import os
import re
import secrets
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel

DB_PATH = Path(os.environ.get("BADUSER_TARGET_DB") or Path(__file__).with_name("invoices.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS tenants (
    id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, name TEXT, email TEXT UNIQUE,
    password TEXT, token TEXT UNIQUE);
CREATE TABLE IF NOT EXISTS memberships (
    user_id TEXT NOT NULL, tenant_id TEXT NOT NULL, PRIMARY KEY (user_id, tenant_id));
CREATE TABLE IF NOT EXISTS invoices (
    id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, owner_id TEXT NOT NULL,
    title TEXT, body TEXT, amount TEXT);
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, owner_id TEXT NOT NULL,
    title TEXT, body TEXT);
CREATE TABLE IF NOT EXISTS invites (
    code TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, inviter_id TEXT NOT NULL,
    email TEXT NOT NULL, invoice_id TEXT, accepted INTEGER DEFAULT 0);
"""


@contextmanager
def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with db() as con:
        con.executescript(SCHEMA)


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(4)}"


app = FastAPI(title="Invoicely")
init_db()


# ---------- payloads ----------


class SignupIn(BaseModel):
    company: str
    name: str
    email: str
    password: str


class LoginIn(BaseModel):
    email: str
    password: str


class InvoiceIn(BaseModel):
    title: str
    body: str = ""
    amount: str = "0.00"


class DocumentIn(BaseModel):
    title: str
    body: str = ""


class ChatIn(BaseModel):
    question: str


class InviteIn(BaseModel):
    email: str
    invoice_id: str | None = None


# ---------- auth ----------


def current_user(req: Request) -> sqlite3.Row:
    """Bearer token or `session` cookie. OK-4: no/!bad credentials -> 401, never a 200."""
    token = ""
    auth = req.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
    token = token or req.cookies.get("session", "")
    if not token:
        raise HTTPException(401, "Missing credentials")
    with db() as con:
        user = con.execute("SELECT * FROM users WHERE token = ?", (token,)).fetchone()
    if user is None:
        raise HTTPException(401, "Invalid credentials")
    return user


def tenants_for(user: sqlite3.Row) -> set[str]:
    """Every tenant this user can read: their own, plus anything they hold a membership in."""
    with db() as con:
        rows = con.execute(
            "SELECT tenant_id FROM memberships WHERE user_id = ?", (user["id"],)
        ).fetchall()
    return {user["tenant_id"]} | {r["tenant_id"] for r in rows}


# ---------- accounts (open signup: no CAPTCHA, no email verification) ----------


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/api/signup", status_code=201)
def signup(body: SignupIn, response: Response):
    token = secrets.token_hex(16)
    with db() as con:
        row = con.execute("SELECT * FROM tenants WHERE name = ?", (body.company,)).fetchone()
        if row is None:
            tenant_id = new_id("t")
            con.execute("INSERT INTO tenants VALUES (?, ?)", (tenant_id, body.company))
        else:
            tenant_id = row["id"]
        if con.execute("SELECT 1 FROM users WHERE email = ?", (body.email,)).fetchone():
            raise HTTPException(409, "That email is already registered")
        uid = new_id("u")
        con.execute(
            "INSERT INTO users VALUES (?, ?, ?, ?, ?, ?)",
            (uid, tenant_id, body.name, body.email, body.password, token),
        )
    response.set_cookie("session", token, httponly=True)
    return {"user_id": uid, "tenant_id": tenant_id, "company": body.company, "token": token}


@app.post("/api/login")
def login(body: LoginIn, response: Response):
    with db() as con:
        user = con.execute(
            "SELECT * FROM users WHERE email = ? AND password = ?", (body.email, body.password)
        ).fetchone()
    if user is None:
        raise HTTPException(401, "Invalid credentials")
    response.set_cookie("session", user["token"], httponly=True)
    return {"user_id": user["id"], "token": user["token"]}


@app.get("/api/me")
def me(req: Request):
    # OK-3: the caller's own row and nothing else.
    user = current_user(req)
    with db() as con:
        tenant = con.execute(
            "SELECT * FROM tenants WHERE id = ?", (user["tenant_id"],)
        ).fetchone()
    return {
        "user_id": user["id"],
        "name": user["name"],
        "email": user["email"],
        "tenant_id": user["tenant_id"],
        "company": tenant["name"] if tenant else None,
    }


# ---------- invoices ----------


@app.post("/api/invoices", status_code=201)
def create_invoice(body: InvoiceIn, req: Request):
    user = current_user(req)
    iid = new_id("inv")
    with db() as con:
        con.execute(
            "INSERT INTO invoices VALUES (?, ?, ?, ?, ?, ?)",
            (iid, user["tenant_id"], user["id"], body.title, body.body, body.amount),
        )
    return {"id": iid, "title": body.title, "amount": body.amount}


@app.get("/api/invoices")
def list_invoices(req: Request):
    # BUG-2 (listing leak): authenticated, then every tenant's invoices come back. The
    # WHERE tenant_id = ? that belongs here was never written.
    current_user(req)
    with db() as con:
        rows = con.execute("SELECT * FROM invoices ORDER BY id").fetchall()
    return {"invoices": [dict(r) for r in rows]}


@app.get("/api/invoices/{invoice_id}")
def get_invoice(invoice_id: str, req: Request):
    # OK-1 when the caller owns it. BUG-1 (IDOR): ownership is never checked, so the same
    # 200 is served to any authenticated user of any tenant.
    current_user(req)
    with db() as con:
        row = con.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
    if row is None:
        raise HTTPException(404, f"No invoice {invoice_id}")
    return dict(row)


# ---------- documents (this router is CORRECT -- it is the false-positive test) ----------


@app.post("/api/documents", status_code=201)
def create_document(body: DocumentIn, req: Request):
    user = current_user(req)
    did = new_id("doc")
    with db() as con:
        con.execute(
            "INSERT INTO documents VALUES (?, ?, ?, ?, ?)",
            (did, user["tenant_id"], user["id"], body.title, body.body),
        )
    return {"id": did, "title": body.title}


@app.get("/api/documents/{document_id}")
def get_document(document_id: str, req: Request):
    # OK-2: a correct refusal that QUOTES the requested id and title in the denial body.
    # A naive id/substring detector scores this as a leak; the oracle must not (PLAN 14,
    # looks_like_denial). This route is also the final read of the BUG-4 chain: 403 without
    # the invite, 200 with it, because accept() hands out a membership here.
    user = current_user(req)
    with db() as con:
        row = con.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
    if row is None:
        raise HTTPException(404, f"No document {document_id}")
    if row["tenant_id"] not in tenants_for(user):
        raise HTTPException(
            403,
            f"Access denied: document {document_id} ('{row['title']}') belongs to another "
            f"organisation. Ask its owner to share it with you.",
        )
    return dict(row)


# ---------- chat ----------


@app.post("/api/chat")
def chat(body: ChatIn, req: Request):
    # BUG-3 (chat leak): the retrieval step has no tenant filter, so the assistant answers
    # out of every tenant's content. It replies in prose and never prints a row id, which
    # is exactly the leak an id-matching detector cannot see -- canaries can (PLAN 14).
    current_user(req)
    words = {w for w in re.findall(r"[a-z0-9]+", body.question.lower()) if len(w) > 2}
    with db() as con:
        rows = [
            *con.execute("SELECT title, body, amount FROM invoices").fetchall(),
            *[
                {"title": r["title"], "body": r["body"], "amount": None}
                for r in con.execute("SELECT title, body FROM documents").fetchall()
            ],
        ]
    scored = []
    for r in rows:
        hay = f"{r['title']} {r['body']}".lower()
        score = sum(1 for w in words if w in hay)
        if score:
            scored.append((score, r))
    scored.sort(key=lambda p: -p[0])
    if not scored:
        return {"answer": "I looked through the records and have nothing to add on that."}
    parts = []
    for _, r in scored[:3]:
        line = f"'{r['title']}': {r['body']}"
        if r["amount"]:
            line += f" The total is {r['amount']}."
        parts.append(line)
    return {"answer": "Here is what the records say. " + " ".join(parts)}


# ---------- invites (BUG-4, the compound chain) ----------


@app.post("/api/invites", status_code=201)
def create_invite(body: InviteIn, req: Request):
    """Share one invoice with an outside collaborator by email."""
    user = current_user(req)
    code = secrets.token_hex(8)
    with db() as con:
        con.execute(
            "INSERT INTO invites VALUES (?, ?, ?, ?, ?, 0)",
            (code, user["tenant_id"], user["id"], body.email, body.invoice_id),
        )
    return {"code": code, "invoice_id": body.invoice_id}


@app.post("/api/invites/{code}/accept")
def accept_invite(code: str, req: Request):
    # BUG-4 (compound): the invite is scoped to ONE invoice, but accepting it writes a
    # membership row for the whole inviting tenant -- and says nothing about it. Every
    # later tenant check (see get_document) then passes for the invitee.
    user = current_user(req)
    with db() as con:
        invite = con.execute("SELECT * FROM invites WHERE code = ?", (code,)).fetchone()
        if invite is None:
            raise HTTPException(404, "No such invite")
        if invite["email"].lower() != (user["email"] or "").lower():
            raise HTTPException(403, "This invite was issued to a different email address")
        con.execute(
            "INSERT OR IGNORE INTO memberships VALUES (?, ?)", (user["id"], invite["tenant_id"])
        )
        con.execute("UPDATE invites SET accepted = 1 WHERE code = ?", (code,))
    # Deliberately says nothing about the tenant it just joined you to.
    return {"ok": True, "invite": code, "shared_invoice": invite["invoice_id"]}
