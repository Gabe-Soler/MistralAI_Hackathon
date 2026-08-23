"""`/baduser` entry (Typer). See PLAN.md sections 7, 16, 17.

Server + pipeline run in ONE process on ONE loop (PLAN 16 "Process model"):
`asyncio.gather(uvicorn.Server(...).serve(), pipeline(...))`. A two-process design gives
/stream a different Bus instance and it emits nothing, forever.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as _dt
import os
import socket
import webbrowser
from pathlib import Path
from urllib.parse import urlparse

import typer
import uvicorn

from .bus import Bus
from .campaign import build_ctx
from .campaign import run as campaign_run
from .channels import build_adapters, close_adapters
from .discover import probe_openapi
from .models import (
    Channel,
    GroundTruth,
    Invariant,
    PhaseEvent,
    ProductType,
    SessionConfig,
    StepFinished,
    StepStarted,
    TruthUpdated,
    Verdict,
)
from .server import EngineState, create_app
from .store import Store
from .world import SeedError, seed, synth_world

app = typer.Typer(add_completion=False, help="Bad User -- point it at a product; it attacks.")

_LOOPBACK = {"localhost", "127.0.0.1", "::1", "0.0.0.0", ""}


def parse_channels(s: str) -> list[Channel]:
    return [Channel(c.strip()) for c in s.split(",") if c.strip()]


def is_loopback(target: str) -> bool:
    if not target:
        return True
    host = urlparse(target).hostname or target
    return host in _LOOPBACK


def guard_target(target: str, *, confirm=typer.confirm) -> None:
    """Default-deny (PLAN 24): an autonomous agent must not be pointable at prod by a paste.
    A non-loopback target requires explicit confirmation or the run aborts."""
    if is_loopback(target):
        return
    ok = confirm(
        f"Target {target!r} is NOT loopback. Bad User writes REAL data and attacks it. "
        "Continue?",
        default=False,
    )
    if not ok:
        raise typer.Abort()


def _load_env() -> None:
    """Read MISTRAL_API_KEY from .env files so `bad-user` works from any directory.

    Order: real environment wins, then ./.env, then ~/.vibe/.env (which Vibe already
    uses, so a machine set up for Vibe needs no extra configuration).
    """
    for path in (Path.cwd() / ".env", Path.home() / ".vibe" / ".env"):
        try:
            for line in path.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip("'\""))
        except OSError:
            continue


def _make_llm():
    """Real Mistral when a key is present AND the extra is installed, else None.

    Never raises. `mistralai` is an optional dependency, and a missing optional dependency
    must not kill a run -- the rest of the pipeline (seeding, canary scan, control run) is
    fully deterministic without it. Crashing here made *having* a key worse than not
    having one.
    """
    key = os.environ.get("MISTRAL_API_KEY", "").strip()
    if not key:
        return None
    try:
        from .llm import MistralLLM

        return MistralLLM(key)
    except Exception as e:  # noqa: BLE001 - optional dep or bad key; degrade, never die
        typer.secho(f"  LLM unavailable ({e}); continuing without the repo read", fg="yellow")
        return None


def fallback_ground_truth(target: str) -> GroundTruth:
    """Used when there is no MISTRAL_API_KEY.

    Reading the repo is the only step that genuinely needs an LLM. Everything downstream --
    seeding, the canary scan, the control run -- is deterministic, so a run without a key
    is still a real run against a real deployment; it just cannot infer product-specific
    rules. `tenant-isolation` is the invariant the oracle enforces and it holds for every
    multi-tenant product, so hard-coding it loses nothing the canary scan would have used.
    """
    return GroundTruth(
        product_name=urlparse(target).netloc or "target",
        product_type=ProductType.b2b,
        roles=["admin", "member"],
        invariants=[
            Invariant(
                id="tenant-isolation",
                name="Tenant isolation",
                rule="A user may only read records belonging to their own tenant.",
                source="dev",
                cite="(assumed: no MISTRAL_API_KEY, repo not read)",
            )
        ],
    )


def _fail(state: EngineState, bus: Bus, detail: str) -> None:
    """End the run as `failed`, not `done`.

    Both callers previously emitted `done` with no findings, which made a run that proved
    NOTHING indistinguishable from a clean one -- green in the dashboard and, worse, exit 0
    under --ci. That is the false clean this tool exists to prevent (oracle.py, PLAN 14).
    """
    state.phase = "failed"
    state.phase_detail = detail
    bus.emit(PhaseEvent(phase="failed", detail=detail))


async def pipeline(state: EngineState) -> None:
    """The real run (PLAN 17): read -> seed -> attack -> report.

    Each phase emits its PhaseEvent before doing the work, so the dashboard shows what is
    happening rather than what already happened.
    """
    bus, cfg = state.bus, state.config

    # ---- 1-2. rules -----------------------------------------------------------
    state.phase = "reading"
    bus.emit(PhaseEvent(phase="reading"))
    llm = _make_llm()
    if llm is not None and cfg.repo:
        from .truth import build_ground_truth

        gt = await build_ground_truth(cfg.repo, llm)
    else:
        gt = fallback_ground_truth(cfg.target)
        if not cfg.repo:
            typer.echo("  no --repo: using the assumed tenant-isolation invariant")
        elif llm is None:
            typer.echo("  no usable LLM: skipping repo read, assuming tenant isolation")
    state.ground_truth = gt
    for inv in gt.invariants:
        bus.emit(TruthUpdated(invariant=inv))
    if state.store:
        state.store.save_ground_truth(gt)

    # ---- 3. build and seed the world -----------------------------------------
    state.phase = "seeding"
    bus.emit(PhaseEvent(phase="seeding"))
    manifest = synth_world(gt, cfg.run_id)
    state.manifest = manifest
    # store.dir.parent, not store.dir: shots_dir() appends run_id itself, so passing the
    # run directory nested it twice and the /shots route never found a frame.
    runs_root = str(state.store.dir.parent) if state.store else None
    adapters = build_adapters(cfg, manifest, runs_dir=runs_root)

    # Discover BEFORE seeding: a generated FastAPI app serves /openapi.json, which gives
    # exact paths and body shapes for free. Seeding then knows which collections exist and
    # can create one artifact per kind -- without this we only ever seed one kind and every
    # route for the others returns an empty list, which reads as a clean app.
    api = adapters.get(Channel.api)
    if api is not None and manifest.personas:
        found = await probe_openapi(api, manifest.personas[0].id)
        if found:
            known = set(gt.endpoints)
            gt.endpoints = list(gt.endpoints) + [e.key() for e in found if e.key() not in known]
            gt.endpoint_bodies = {e.key(): e.body_keys for e in found if e.body_keys}
            typer.echo(f"  /openapi.json: {len(found)} routes discovered")
            if state.store:
                state.store.save_ground_truth(gt)  # re-save: discovery ran after the read

    try:
        manifest = await seed(manifest, adapters, bus=bus, store=state.store, gt=gt)
    except SeedError as e:
        # Fewer than two tenants means every cross-tenant check passes vacuously and the
        # run would report a false clean. Abort loudly instead (PLAN 11c).
        typer.secho(f"  seeding failed: {e}", fg="red")
        _fail(state, bus, f"seeding failed: {e}")
        return
    state.manifest = manifest
    typer.echo(
        f"  seeded {len(manifest.tenants)} tenants, "
        f"{len(manifest.personas)} personas, {len(manifest.artifacts)} artifacts"
    )

    # ---- 4-5. attack + check --------------------------------------------------
    state.phase = "attacking"
    bus.emit(PhaseEvent(phase="attacking"))
    # UI flows before the attack: they answer "can a person use this at all", which is a
    # different question from "does anything leak" and the one a human notices first. An
    # app can pass every leak check while being completely unusable.
    if Channel.web in cfg.channels and adapters.get(Channel.web) is not None:
        from .uiflows import FLOWS, UiUnavailable, run_flows

        def _ui_emit(flow: object, state: str, reason: str,
                     shot: str | None = None) -> None:
            if state == "started":
                bus.emit(StepStarted(play_id="ui-flows", persona_id="ui",
                                     channel=Channel.web, action=flow.title))
                return
            bus.emit(StepFinished(
                play_id="ui-flows", persona_id="ui", channel=Channel.web,
                action=flow.title, detail=reason,
                verdict=Verdict.benign if state == "passed" else Verdict.broken,
                invariant_id=flow.id, shot=shot))

        typer.echo("  checking the UI flows...")
        try:
            ui = await run_flows(adapters[Channel.web], manifest, cfg.target, emit=_ui_emit)
        except UiUnavailable as e:
            # Never a finding about the target. The UI was not tested at all, and saying
            # so is the only honest report -- five BROKEN flows against an app whose pages
            # were never opened would be worse than no check.
            ui = []
            typer.secho(f"  UI flows NOT CHECKED -- the browser could not start: {e}",
                        fg="red")
            typer.secho("  install the browser extra:  "
                        "uv tool install --force '<repo>/engine[web]'", fg="red")
        else:
            for f in ui:
                typer.secho(f"  BROKEN: {f.action} -- {f.rationale}", fg="yellow")
            n = len(FLOWS)
            typer.secho(f"  UI flows: {n - len(ui)}/{n} passed",
                        fg="green" if not ui else "yellow")
        state.findings.extend(ui)

    ctx = build_ctx(gt, manifest, bus, adapters, min_interval=1.0 / max(cfg.rps, 0.1))

    # With an LLM the run widens beyond the canary scan: generated plays probe the other
    # invariants the repo read found, and a judge grades whatever the canary scan cleared.
    # Both produce `suspected`, never `breach` -- inference is reported separately from the
    # lookup, and only the lookup gates --ci.
    if llm is not None:
        ctx.llm = llm
        from .planner import plan as plan_plays

        ctx.plays = await plan_plays(llm, gt, manifest, cfg.channels)
        if ctx.plays:
            typer.echo(f"  planned {len(ctx.plays)} extra probes from "
                       f"{len(gt.invariants)} rules")
    try:
        findings = await campaign_run(cfg, ctx)
    finally:
        await close_adapters(adapters)
    state.findings.extend(findings)
    if state.store:
        state.store.save_findings(state.findings)

    # Never report clean when nothing actually reached the app. An all-404 run and a
    # genuinely secure app produce the identical dashboard, and that is the failure mode
    # this whole tool exists to avoid -- so make it loud instead of green.
    landed = sum(1 for r in ctx.reached if r)
    if ctx.reached and not landed:
        typer.secho(
            f"  NO STEP REACHED THE TARGET -- {len(ctx.reached)} attempts, none got a 2xx.\n"
            "  The plays do not match this app's API. This is NOT a clean result.",
            fg="red", bold=True,
        )
        _fail(
            state, bus,
            f"no step reached the target: {len(ctx.reached)} attempts, none got a 2xx",
        )
        return

    breaches = sum(1 for f in state.findings if f.verdict == Verdict.breach)
    typer.secho(f"  {breaches} BREACH" + ("" if breaches == 1 else "ES"),
                fg="red" if breaches else "green")
    broken = sum(1 for f in state.findings if f.verdict == Verdict.broken)
    if broken:
        typer.secho(f"  {broken} BROKEN UI FLOW" + ("" if broken == 1 else "S"), fg="yellow")
    suspected = sum(1 for f in state.findings if f.verdict == Verdict.suspected)
    if suspected:
        # Deliberately a separate line and a different colour: these are judged, not
        # proven, and must never be read as part of the breach count.
        typer.secho(f"  {suspected} suspected (judged against the rules, not proven)",
                    fg="yellow")

    state.phase = "done"
    bus.emit(PhaseEvent(phase="done"))


def _port_free(host: str, port: int) -> bool:
    with socket.socket() as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
        except OSError:
            return False
    return True


async def _run(
    state: EngineState,
    host: str,
    port: int,
    ci: bool,
    *,
    mock: bool = False,
    speed: float = 1.0,
    open_browser: bool = False,
) -> int:
    # A busy port is not a failure -- just take the next one. Erroring out meant a
    # leftover dashboard from an earlier run blocked every subsequent run, and an agent
    # driving this saw a message none of its success/failure patterns matched, so it sat
    # waiting on a process that had already exited.
    if not _port_free(host, port):
        for candidate in range(port + 1, port + 21):
            if _port_free(host, candidate):
                typer.secho(f"  port {port} busy -- using {candidate}", fg="yellow")
                port = candidate
                break
        else:
            typer.secho(f"  no free port in {port}-{port + 20}. FAILED, nothing ran.", fg="red")
            return 2

    config = uvicorn.Config(create_app(state), host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)

    async def drive() -> None:
        if mock:
            from .mock import run as run_mock

            await run_mock(state.bus, state, speed=speed)
        else:
            await pipeline(state)

    # The run's own page, not the landing page: /baduser should land you on what it just
    # started. The SPA reads the id straight out of the path.
    url = f"http://{host}:{port}/{state.config.run_id}"
    typer.secho(f"\n  dashboard -> {url}" + ("   [mock]" if mock else ""), fg="cyan", bold=True)
    typer.echo("  ctrl-c to stop\n")
    if open_browser and not ci:
        # after a beat, so the server is accepting by the time the tab loads
        async def _open() -> None:
            await asyncio.sleep(0.7)
            with contextlib.suppress(Exception):
                webbrowser.open(url)

        asyncio.create_task(_open())  # noqa: RUF006 - fire and forget; run outlives it
    if ci:
        # --ci: quit when the pipeline finishes; exit non-zero on any breach (PLAN 16).
        server_task = asyncio.create_task(server.serve())
        await drive()
        server.should_exit = True
        await server_task
        if state.phase == "failed":
            return 2  # distinct from 1 (breach found): the run itself did not prove anything
        return 1 if any(f.verdict == Verdict.breach for f in state.findings) else 0
    # demo mode: server stays up after the pipeline finishes so the dashboard stays live.
    await asyncio.gather(server.serve(), drive())
    return 0


@app.command()
def main(
    repo: str = typer.Option("", "--repo", help="local source to read (intent)"),
    target: str = typer.Option("", "--target", help="deployment URL to test"),
    channels: str = typer.Option("api,chat", "--channels", help="channels this run"),
    support_phone: str | None = typer.Option(None, "--support-phone", help="voice line you own"),
    replay: str | None = typer.Option(None, "--replay", help="drive from a recorded events.jsonl"),
    ci: bool = typer.Option(False, "--ci", help="exit non-zero on breach and quit"),
    mock: bool = typer.Option(False, "--mock", help="scripted run: no target, no API keys"),
    speed: float = typer.Option(1.0, "--speed", help="mock pacing (0.2 = fast rehearsal)"),
    rps: float = typer.Option(5.0, "--rps", help="max requests/sec against the target"),
    headless: bool = typer.Option(
        True, "--headless/--show-browser",
        help="run the web channel headless (screenshots are captured either way)"),
    open_browser: bool = typer.Option(True, "--open/--no-open", help="open the dashboard"),
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8787, "--port"),
) -> None:
    _load_env()
    if not mock:
        guard_target(target)
    run_id = _dt.datetime.now(tz=_dt.UTC).strftime("%Y%m%d-%H%M%S")
    cfg = SessionConfig(
        run_id=run_id,
        repo=repo,
        target=target,
        channels=parse_channels(channels),
        support_phone=support_phone,
        replay=replay,
        ci=ci,
        rps=rps,
        headless=headless,
    )
    store = Store(run_id)
    store.save_config(cfg)
    bus = Bus(on_emit=store.append_event)  # persist every event to events.jsonl
    state = EngineState(cfg, bus, store)
    code = asyncio.run(
        _run(state, host, port, ci, mock=mock, speed=speed, open_browser=open_browser)
    )
    raise typer.Exit(code=code)


if __name__ == "__main__":
    app()
