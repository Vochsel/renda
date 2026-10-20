"""`renda` command line. Every command takes --json for agents: one JSON object on stdout.

Success: {"status": "ok", ..., "next"?: [...]}. Errors: {"status": "error", "reason", "message", "hint"?, "next"?,
"error": {code, message, hint}}. Exit codes (shared with the Node CLI, @rendahq/cli): 0 ok, 1 error, 2 usage,
3 auth needed, 4 payment required."""

from __future__ import annotations

import functools
import json
import sys
import time
from pathlib import Path
from typing import Annotated, Any, Callable, Optional

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.table import Table

from . import __version__

app = typer.Typer(
    name="renda",
    help="Render any 3D file (.blend .usd .glb .fbx .obj .stl .ply three.js JSON …) locally or in the Renda cloud.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
)
console = Console(stderr=True)
out = Console()

JsonOpt = Annotated[bool, typer.Option("--json", help="Machine-readable JSON output (for agents).")]


EXIT_OK, EXIT_ERROR, EXIT_USAGE, EXIT_AUTH, EXIT_PAYMENT = 0, 1, 2, 3, 4
NODE_CLI = "@rendahq/cli"

AUTH_NEXT = ["renda trial   # no signup, 5 free render-minutes", "renda login   # browser sign-in",
             "renda signup <email>   # emailed 6-digit code"]


class CliError(Exception):
    def __init__(self, code: str, message: str, hint: str | None = None, next: list[str] | None = None,
                 exit_code: int = EXIT_ERROR):
        super().__init__(message)
        self.code, self.message, self.hint, self.next, self.exit_code = code, message, hint, next or [], exit_code


def _exit_code(e: Exception, payload: dict) -> int:
    if isinstance(e, CliError):
        return e.exit_code
    status = getattr(e, "status", None)
    if status == 402 or payload["code"] == "payment_required":
        return EXIT_PAYMENT
    if status == 401 or payload["code"] in ("unauthenticated", "unauthorized", "trial_expired"):
        return EXIT_AUTH
    return EXIT_ERROR


def _next_for(code: int) -> list[str]:
    if code == EXIT_AUTH:
        return AUTH_NEXT
    if code == EXIT_PAYMENT:
        return ["renda billing   # prints a link for the human to add a card"]
    return []


def _redact(s: str) -> str:
    import re

    return re.sub(r"rnd_live_[A-Za-z0-9_-]{8,}", "rnd_live_…", s)


def _err_payload(e: Exception) -> dict:
    from .assets import AssetError
    from .cloud import CloudError
    from .formats import UnknownFormat

    if isinstance(e, (CliError, CloudError)):
        d = {"code": e.code, "message": e.message}
        if e.hint:
            d["hint"] = e.hint
        return d
    if isinstance(e, ValidationError):
        issues = [{"path": ".".join(str(p) for p in err["loc"]), "message": err["msg"]} for err in e.errors()]
        return {"code": "invalid_package", "message": f"{len(issues)} problem(s) in package", "issues": issues,
                "hint": "fix the listed fields; schema: https://renda.dev/schema/v1.json"}
    from .layers import LayerError

    if isinstance(e, LayerError):
        return {"code": e.code, "message": str(e), "layer": e.layer, **({"field": e.field} if e.field else {}),
                "hint": "fix that field in that layer; `renda flatten <pkg> --provenance` shows how layers combine"}
    if isinstance(e, UnknownFormat):
        return {"code": "unknown_format", "message": str(e), "hint": "run `renda info <file>` or set assets.<id>.format"}
    if isinstance(e, AssetError):
        return {"code": "asset_error", "message": str(e), "hint": "check the asset uri/path in the package"}
    if isinstance(e, (FileNotFoundError, IsADirectoryError)):
        return {"code": "not_found", "message": str(e)}
    return {"code": "error", "message": str(e) or e.__class__.__name__}


def command(fn: Callable) -> Callable:
    """Register a command with uniform error handling + --json support."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        as_json = kwargs.get("json_out", False)
        try:
            return fn(*args, **kwargs)
        except typer.Exit:
            raise
        except KeyboardInterrupt:
            raise typer.Exit(130)
        except Exception as e:  # noqa: BLE001 - CLI boundary
            payload = _err_payload(e)
            code = _exit_code(e, payload)
            nxt = [_redact(n) for n in (getattr(e, "next", None) or _next_for(code))]
            if as_json:
                body = {"status": "error", "reason": payload["code"], "message": _redact(payload["message"])}
                if payload.get("hint"):
                    body["hint"] = _redact(payload["hint"])
                if nxt:
                    body["next"] = nxt
                print(json.dumps({**body, "error": payload}))
            else:
                console.print(f"[bold red]error[/]: {payload['message']}")
                for issue in payload.get("issues", []):
                    console.print(f"  • [yellow]{issue['path']}[/]: {issue['message']}")
                if payload.get("hint"):
                    console.print(f"[dim]hint: {payload['hint']}[/]")
                for n in nxt:
                    console.print(f"[dim]next: {n}[/]")
                if payload["code"] == "error" and "--debug" in sys.argv:
                    console.print_exception()
            raise typer.Exit(code)

    return app.command()(wrapper)


def emit(as_json: bool, data: Any, human: Callable[[], None] | None = None, next: list[str] | None = None) -> None:
    nxt = [_redact(n) for n in (next or [])]
    if as_json:
        if isinstance(data, dict):
            data = {"status": "ok", **data, **({"next": nxt} if nxt else {})}
        print(json.dumps(data, default=str))
        return
    if human:
        human()
    else:
        out.print_json(data=data)
    for n in nxt:
        console.print(f"[dim]next: {n}[/]")


# -- package loading --------------------------------------------------------------


def load_input(path: Path) -> tuple[dict, Path]:
    """A .renda.json package (flattened: `extends` and package references resolved), or a bare scene
    file wrapped in the default package. Returns (raw dict, base dir)."""
    path = Path(path)
    if not path.exists():
        raise CliError("not_found", f"{path} does not exist")
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text())
        is_pkg = path.name.lower().endswith(".renda.json") or (
            isinstance(data, dict) and ("scene" in data or "extends" in data) and "metadata" not in data)
        if is_pkg:
            from .layers import flatten_file

            return flatten_file(path).package, path.parent.resolve()
    return {"assets": {"scene": {"uri": path.name}}, "scene": {"root": "scene"}}, path.parent.resolve()


_TEX_KEYS = {"material", "object", "texture", "slot", "scale", "offset", "rotation", "colorspace", "cs",
             "strength", "loop", "start", "speed", "frame"}


def parse_texture_flag(value: str) -> dict:
    """`--texture "material=/Wood.*/i,slot=baseColor,scale=4:./oak.jpg"` → a textureOverrides entry.

    Before the first top-level `:` come comma-separated key=value pairs (material, object, texture,
    slot, scale=4 or 2x3, offset=0.1x0.2, rotation=deg, colorSpace=srgb|linear, strength, loop,
    start=sec, speed, frame); after it, the image/video path or URL. No `=` → the whole value is the
    source, applied to every material's base colour.
    """
    import re

    s = value.strip()
    pos, spec = 0, {}
    while True:
        m = re.compile(r"([A-Za-z]+)=").match(s, pos)
        if not m or m.group(1).lower() not in _TEX_KEYS:
            break
        key, pos = m.group(1).lower(), m.end()
        if s.startswith("/", pos):  # /regex/flags may contain , and :
            mm = re.compile(r"/(?:\\.|[^/\\])*/[imsx]*").match(s, pos)
            if not mm:
                raise CliError("bad_option", f"--texture: unterminated /regex/ in {value!r}")
        else:
            mm = re.compile(r"[^,:]*").match(s, pos)
        spec[key], pos = mm.group(0), mm.end()
        if s.startswith(",", pos):
            pos += 1
            continue
        if s.startswith(":", pos):
            pos += 1
            break
        raise CliError("bad_option", f"--texture: expected ',' or ':' after {key}= in {value!r}",
                       'format: --texture "material=Wood*,slot=baseColor:./oak.jpg"')
    source = s[pos:] if spec else s
    if not source:
        raise CliError("bad_option", f"--texture: missing image/video after ':' in {value!r}")
    if "://" not in source and source not in ("",):
        p = Path(source).expanduser()
        if p.exists():
            source = str(p.resolve())  # CLI paths are relative to the shell, not the package

    def pair(v: str) -> list[float]:
        a, _, b = v.lower().partition("x")
        return [float(a), float(b or a)]

    entry: dict = {"source": source}
    match = {k: spec[k] for k in ("material", "object", "texture", "slot") if k in spec}
    if match:
        entry["match"] = match
    uv = {}
    if "scale" in spec:
        sx, sy = pair(spec["scale"])
        uv["scale"] = sx if sx == sy else [sx, sy]
    if "offset" in spec:
        uv["offset"] = pair(spec["offset"])
    if "rotation" in spec:
        uv["rotationDeg"] = float(spec["rotation"])
    if uv:
        entry["uv"] = uv
    video = {}
    if "loop" in spec:
        video["loop"] = spec["loop"].lower() not in ("0", "false", "no", "off")
    if "start" in spec:
        video["startSec"] = float(spec["start"])
    if "speed" in spec:
        video["speed"] = float(spec["speed"])
    if "frame" in spec:
        video["frame"] = int(spec["frame"])
    if video:
        entry["video"] = video
    cs = spec.get("colorspace") or spec.get("cs")
    if cs:
        entry["colorSpace"] = cs.lower()
    if "strength" in spec:
        entry["strength"] = float(spec["strength"])
    return entry


def apply_overrides(raw: dict, *, engine=None, res=None, quality=None, samples=None, frames=None, fmt=None,
                    transparent=None, turntable=False, fps=None, textures=None) -> dict:
    if textures:
        raw.setdefault("scene", {}).setdefault("textureOverrides", []).extend(parse_texture_flag(t) for t in textures)
    r = raw.setdefault("render", {}) if any(v is not None for v in (engine, res, quality, samples)) else raw.get("render")
    if engine:
        r["engine"] = engine
    if res:
        try:
            w, h = (int(x) for x in res.lower().split("x"))
        except ValueError:
            raise CliError("bad_option", f"--res must look like 1920x1080, got {res!r}") from None
        r["resolution"] = [w, h]
    if quality:
        r["quality"] = quality
    if samples:
        r["samples"] = samples
    if fmt or transparent is not None:
        o = raw.setdefault("output", {})
        if fmt:
            o["format"] = fmt
        if transparent is not None:
            o["transparent"] = transparent
    if frames or turntable or fps:
        a = raw.setdefault("animation", {})
        if frames:
            try:
                lo, _, hi = frames.partition("-")
                a["frames"] = [int(lo), int(hi or lo)]
            except ValueError:
                raise CliError("bad_option", f"--frames must look like 1-120, got {frames!r}") from None
        a.setdefault("frames", [1, 120])
        if fps:
            a["fps"] = fps
        if turntable:
            a["turntable"] = {"revolutions": 1}
    return raw


def _client():
    from .cloud import RendaClient

    return RendaClient()


# -- local commands ---------------------------------------------------------------


@command
def render(
    input: Annotated[Path, typer.Argument(help="Scene file or .renda.json package")],
    output: Annotated[Path, typer.Option("-o", "--output", help="Output directory")] = Path("renders"),
    engine: Annotated[Optional[str], typer.Option(help="auto|blender-cycles|blender-eevee|blender-workbench|mitsuba|threejs")] = None,
    res: Annotated[Optional[str], typer.Option(help="Resolution WxH, e.g. 1920x1080")] = None,
    quality: Annotated[Optional[str], typer.Option(help="draft|standard|final")] = None,
    samples: Annotated[Optional[int], typer.Option(help="Override samples")] = None,
    frames: Annotated[Optional[str], typer.Option(help="Frame range a-b")] = None,
    fmt: Annotated[Optional[str], typer.Option("--format", help="png|jpg|webp|exr|mp4|webm|gif|mov")] = None,
    transparent: Annotated[Optional[bool], typer.Option("--transparent/--opaque", help="Alpha background")] = None,
    turntable: Annotated[bool, typer.Option(help="Orbit the camera 360° (defaults to frames 1-120)")] = False,
    texture: Annotated[Optional[list[str]], typer.Option(
        "--texture", help='Override a texture (repeatable): "material=/Wood.*/i,slot=baseColor,scale=4:./oak.jpg". '
                          "Any image or video; matches material/object/texture names by glob or /regex/.")] = None,
    priority: Annotated[str, typer.Option(help="economy|standard|rush (cloud)")] = "standard",
    fast: Annotated[bool, typer.Option("--fast", help="Fast mode (= --priority rush): fan out wider on faster GPUs; costs a bit more")] = False,
    cloud: Annotated[bool, typer.Option("--cloud", help="Render in the Renda cloud")] = False,
    wait: Annotated[bool, typer.Option("--wait/--no-wait", help="Cloud: wait for the job and download outputs")] = True,
    max_minutes: Annotated[Optional[float], typer.Option("--max-minutes", help="Cloud: refuse if the estimate exceeds this many render-minutes")] = None,
    json_out: JsonOpt = False,
):
    """Render a scene locally (default) or in the cloud (--cloud)."""
    raw, base = load_input(input)
    raw = apply_overrides(raw, engine=engine, res=res, quality=quality, samples=samples, frames=frames, fmt=fmt,
                          transparent=transparent, turntable=turntable, textures=texture)
    _run(raw, base, output, priority="rush" if fast else priority, cloud=cloud, wait=wait, max_minutes=max_minutes, json_out=json_out)


@command
def splat(
    input: Annotated[Path, typer.Argument(help="Scene file or .renda.json package (meshes of any format)")],
    output: Annotated[Path, typer.Option("-o", "--output", help="Output directory")] = Path("renders"),
    preset: Annotated[str, typer.Option(help="fast (mesh → Gaussians, seconds) | standard | high (rendered views + gsplat refinement)")] = "standard",
    fmt: Annotated[str, typer.Option("--format", help="spz|ply|splat")] = "spz",
    views: Annotated[Optional[int], typer.Option(help="Training views (preset default when omitted)")] = None,
    coverage: Annotated[Optional[str], typer.Option(help="auto|sphere|hemisphere|orbit camera placement")] = None,
    fan_out: Annotated[Optional[bool], typer.Option("--fan-out/--no-fan-out",
                                                    help="Render views in parallel containers / inside the trainer (default: auto)")] = None,
    steps: Annotated[Optional[int], typer.Option(help="Refinement steps")] = None,
    sh_degree: Annotated[Optional[int], typer.Option("--sh-degree", help="0-3 view-dependent colour")] = None,
    engine: Annotated[Optional[str], typer.Option(help="Engine for the training views (auto|blender-cycles|blender-eevee|mitsuba|threejs)")] = None,
    dataset: Annotated[bool, typer.Option("--dataset", help="Also return the views + exact COLMAP model (zip)")] = False,
    priority: Annotated[str, typer.Option(help="economy|standard|rush (cloud)")] = "standard",
    fast: Annotated[bool, typer.Option("--fast", help="Fast mode (= --priority rush): fan out wider on faster GPUs; costs a bit more")] = False,
    cloud: Annotated[bool, typer.Option("--cloud", help="Bake in the Renda cloud (needed for standard/high without a CUDA GPU)")] = False,
    wait: Annotated[bool, typer.Option("--wait/--no-wait", help="Cloud: wait for the job and download outputs")] = True,
    max_minutes: Annotated[Optional[float], typer.Option("--max-minutes", help="Cloud: refuse if the estimate exceeds this many render-minutes")] = None,
    json_out: JsonOpt = False,
):
    """Bake a scene into a 3D Gaussian splat (.ply/.spz/.splat), lighting included."""
    raw, base = load_input(input)
    o = raw.setdefault("output", {})
    o.update({"format": "splat", "splatFormat": fmt})
    sb = {"preset": preset, "dataset": dataset}
    for k, v in (("views", views), ("steps", steps), ("shDegree", sh_degree), ("engine", engine), ("coverage", coverage),
                 ("fanOut", fan_out)):
        if v is not None:
            sb[k] = v
    o["splatBake"] = {**o.get("splatBake", {}), **sb}
    o.pop("passes", None)
    raw.pop("animation", None)
    _run(raw, base, output, priority="rush" if fast else priority, cloud=cloud, wait=wait, max_minutes=max_minutes, json_out=json_out)


def _mins(seconds: float) -> str:
    return f"{seconds / 60:.1f} min" if seconds >= 90 else f"{seconds:.0f} s"


def _run(raw: dict, base: Path, output: Path, *, priority: str, cloud: bool, wait: bool, max_minutes: float | None,
         json_out: bool) -> None:
    """Shared body of render/bake/splat: validate, then render locally or submit to the cloud."""
    from .spec import load_package

    pkg = load_package(raw)

    if cloud:
        client = _client()
        claim_url = _ensure_key(client, json_out)
        prepared = client.prepare_package(raw, base, on_progress=(None if json_out else lambda m: console.print(f"[dim]{m}[/]")))
        if max_minutes is not None:
            est = client.estimate(prepared)
            hi = (est.get("estSeconds") or [0, 0])[1]
            if hi / 60 > max_minutes:
                raise CliError("over_budget", f"estimated up to {hi / 60:.1f} render-minutes, above --max-minutes {max_minutes:g}",
                               "lower --res/--quality/--frames, or raise --max-minutes")
        job = client.create_job(prepared, priority=priority)
        if not wait:
            emit(json_out, job, lambda: console.print(f"queued job [bold]{job['id']}[/] — `renda jobs {job['id']}`"))
            return
        _finish_cloud_job(client, _wait_job(client, job, json_out), output, json_out, claim_url)
        return

    from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn

    from .render import run_local

    t0 = time.time()
    if json_out:
        run = run_local(pkg, base, output)
    else:
        with Progress(TextColumn("[bold]rendering"), BarColumn(), TextColumn("{task.completed}/{task.total} frames"),
                      TimeElapsedColumn(), console=console) as prog:
            task = prog.add_task("render", total=len(pkg.frames()))
            run = run_local(pkg, base, output, progress=lambda done, total: prog.update(task, completed=done))
    result = {
        "engine": run.plan.engine, "hardware": run.plan.hardware, "outputs": [str(p) for p in run.outputs],
        "warnings": run.warnings, "seconds": round(time.time() - t0, 2), "frames": len(run.plan.frames),
    }

    def human():
        for p in run.outputs:
            out.print(str(p))
        console.print(f"[green]done[/] with {run.plan.engine} in {result['seconds']}s")
        for w in run.warnings:
            console.print(f"[yellow]warning[/]: {w}")

    emit(json_out, result, human)


def _finish_cloud_job(client, job: dict, output: Path, json_out: bool, claim_url: str | None, extra: dict | None = None) -> None:
    """Download a finished job's outputs and report it; failed jobs are errors (exit 1)."""
    files = client.download_outputs(job, output) if job.get("status") == "succeeded" else []
    result = {**job, **(extra or {}), "files": [str(f) for f in files]}
    if job.get("status") != "succeeded":
        code, message = "job_" + job.get("status", "failed"), job.get("error") or job.get("status")
        hint = f"see `renda jobs {job['id']}`"
        emit(json_out, {"status": "error", "reason": code, "message": message, "hint": hint,
                        "error": {"code": code, "message": message, "hint": hint}, "job": job})
        raise typer.Exit(1)

    def human():
        for f in files:
            out.print(str(f))
        console.print(f"[green]done[/] in {job.get('billedSeconds', 0):.1f} billed s · ${job.get('costUsd', 0):.4f}"
                      + (" · cached" if job.get("cached") else ""))
        for w in job.get("warnings", []):
            console.print(f"[yellow]warning[/]: {w}")

    emit(json_out, result, human, [f"Give the user this link to keep the trial workspace: {claim_url}"] if claim_url else None)


def _ensure_key(client, json_out: bool) -> str | None:
    """No API key yet: start an anonymous trial (agent friendly) and return its claim link."""
    if client.key:
        return None
    from .cloud import load_credentials, save_credentials

    t = client.trial()
    client.key = t["apiKey"]
    creds = load_credentials()
    creds.update({"apiKey": t["apiKey"], "apiUrl": client.base_url, "claimUrl": t["claimUrl"], "trialExpiresAt": t.get("expiresAt")})
    save_credentials(creds)
    if not json_out:
        console.print(f"[dim]no account configured: started a free trial ({t.get('freeSecondsLeft', 0) / 60:.0f} render-minutes). "
                      f"Keep it: {t['claimUrl']}[/]")
    return t["claimUrl"]


def _wait_job(client, job: dict, json_out: bool) -> dict:
    if json_out:
        return client.wait(job["id"])
    from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn

    with Progress(TextColumn("[bold]{task.fields[status]}"), BarColumn(), TextColumn("{task.percentage:>3.0f}%"),
                  TimeElapsedColumn(), console=console) as prog:
        task = prog.add_task("job", total=1.0, status=job.get("status", "queued"))
        return client.wait(job["id"], on_update=lambda j: prog.update(task, completed=j.get("progress", 0), status=j.get("status")))


@command
def bake(
    input: Annotated[Path, typer.Argument(help="Scene file or .renda.json package")],
    output: Annotated[Path, typer.Option("-o", "--output", help="Output directory")] = Path("renders"),
    mode: Annotated[str, typer.Option(help="unlit (lighting baked into the colour, KHR_materials_unlit) | ao | lightmap")] = "unlit",
    size: Annotated[int, typer.Option(help="Texture size in pixels")] = 2048,
    engine: Annotated[Optional[str], typer.Option(help="auto|blender-cycles|mitsuba|threejs")] = None,
    quality: Annotated[Optional[str], typer.Option(help="draft|standard|final")] = None,
    samples: Annotated[Optional[int], typer.Option(help="Samples per texel")] = None,
    uvs: Annotated[str, typer.Option(help="auto (new lightmap UVs) | existing (reuse TEXCOORD_0)")] = "auto",
    texture_format: Annotated[str, typer.Option("--texture-format", help="jpg|png|webp")] = "jpg",
    glb: Annotated[bool, typer.Option("--glb", help="Plain conversion to GLB, no baking")] = False,
    priority: Annotated[str, typer.Option(help="economy|standard|rush (cloud)")] = "standard",
    fast: Annotated[bool, typer.Option("--fast", help="Fast mode (= --priority rush): fan out wider on faster GPUs; costs a bit more")] = False,
    cloud: Annotated[bool, typer.Option("--cloud", help="Bake in the Renda cloud")] = False,
    wait: Annotated[bool, typer.Option("--wait/--no-wait", help="Cloud: wait for the job and download the GLB")] = True,
    max_minutes: Annotated[Optional[float], typer.Option("--max-minutes", help="Cloud: refuse if the estimate exceeds this many render-minutes")] = None,
    json_out: JsonOpt = False,
):
    """Bake lighting into a model's textures and get a GLB back (or --glb: just convert to GLB)."""
    raw, base = load_input(input)
    raw = apply_overrides(raw, engine=engine, quality=quality)
    o = raw.setdefault("output", {})
    o.pop("video", None)
    o.pop("passes", None)
    raw.pop("animation", None)
    o["format"] = "glb"
    if glb:
        o.pop("bake", None)
    else:
        if mode not in ("unlit", "ao", "lightmap"):
            raise CliError("bad_option", f"--mode must be unlit, ao or lightmap, got {mode!r}")
        b = {"mode": mode, "size": size, "uvs": uvs, "textureFormat": texture_format}
        if samples:
            b["samples"] = samples
        o["bake"] = b
    _run(raw, base, output, priority="rush" if fast else priority, cloud=cloud, wait=wait, max_minutes=max_minutes, json_out=json_out)


@command
def video(
    project: Annotated[Path, typer.Argument(help="HyperFrames project directory (has index.html)")],
    var: Annotated[Optional[list[str]], typer.Option("--var", help="key=value composition variable (repeatable). Values: "
                   "text · local .mp4/.png (uploaded) · job:<id> (a finished render's clip) · a local 3D file "
                   "(.glb/.blend/.step…: a turntable is rendered first)")] = None,
    vars_json: Annotated[Optional[str], typer.Option("--vars", help="Variables as a JSON object")] = None,
    output: Annotated[Path, typer.Option("-o", "--output", help="Output directory")] = Path("renders"),
    quality: Annotated[Optional[str], typer.Option(help="draft | looks | delivery (standard/final accepted)")] = None,
    fmt: Annotated[Optional[str], typer.Option("--format", help="mp4 | webm | mov | gif")] = None,
    fps: Annotated[Optional[int], typer.Option(help="Frame rate (default: the composition's)")] = None,
    composition: Annotated[Optional[str], typer.Option(help="Composition file inside the project (default index.html)")] = None,
    name: Annotated[Optional[str], typer.Option(help="Output file stem")] = None,
    duration: Annotated[Optional[float], typer.Option(help="Composition length in seconds, for the estimate")] = None,
    priority: Annotated[str, typer.Option(help="economy|standard|rush")] = "standard",
    fast: Annotated[bool, typer.Option("--fast", help="Fast mode (= --priority rush): fan out wider on faster GPUs; costs a bit more")] = False,
    wait: Annotated[bool, typer.Option("--wait/--no-wait", help="Wait for the video and download it")] = True,
    json_out: JsonOpt = False,
):
    """Render a HyperFrames HTML composition to video in the cloud (CPU), e.g. a title card around a 3D turntable."""
    from .cloud import CloudError
    from .cloud.video import hyperframes_quality, parse_vars, resolve_variables, zip_project

    try:
        variables = parse_vars(var, vars_json)
        q = hyperframes_quality(quality)
    except (CloudError, json.JSONDecodeError) as e:
        raise CliError("bad_option", getattr(e, "message", str(e)), exit_code=EXIT_USAGE) from None
    if fmt and fmt not in ("mp4", "webm", "mov", "gif"):
        raise CliError("bad_option", f"--format must be mp4, webm, mov or gif, got {fmt!r}", exit_code=EXIT_USAGE)
    pkg: dict[str, Any] = {}
    if q is not None:
        pkg["quality"] = q
    for key, value in {"format": fmt, "fps": fps, "composition": composition, "name": name, "durationSec": duration}.items():
        if value is not None:
            pkg[key] = value
    client = _client()
    claim_url = _ensure_key(client, json_out)
    zipped = zip_project(project)
    pkg["project"] = client.upload(zipped)
    input_jobs: list[dict] = []
    if variables:
        resolved, input_jobs = resolve_variables(client, variables, Path.cwd(), wait=lambda j: _wait_job(client, j, json_out))
        pkg["variables"] = resolved
    job = client.create_job(pkg, kind="hyperframes", priority="rush" if fast else priority)
    if not wait:
        emit(json_out, {**job, "inputJobs": input_jobs}, lambda: console.print(f"queued job [bold]{job['id']}[/] — `renda jobs {job['id']}`"))
        return
    _finish_cloud_job(client, _wait_job(client, job, json_out), output, json_out, claim_url,
                      {"inputJobs": input_jobs} if input_jobs else None)


@command
def validate(package: Annotated[Path, typer.Argument(help=".renda.json file")], json_out: JsonOpt = False):
    """Validate a package and print it with defaults applied."""
    from .spec import load_package

    raw, base = load_input(package)
    pkg = load_package(raw)
    warnings = []
    from .assets import local_files

    for key, p in local_files(pkg, base).items():
        if not p.exists():
            warnings.append(f"asset {key!r}: {p} not found")
    emit(json_out, {"valid": True, "warnings": warnings, "package": pkg.dump()},
         lambda: (console.print("[green]valid[/]"), [console.print(f"[yellow]warning[/]: {w}") for w in warnings]))


@command
def flatten(
    package: Annotated[Path, typer.Argument(help=".renda.json that uses `extends` / package references")],
    provenance: Annotated[bool, typer.Option("--provenance", help="Also show which layer set each field")] = False,
    output: Annotated[Optional[Path], typer.Option("-o", help="Write the flat package here")] = None,
    json_out: JsonOpt = False,
):
    """Resolve layers (`extends`) and package references into one flat package."""
    from .layers import flatten_file
    from .spec import load_package

    r = flatten_file(package)
    load_package(r.package)  # validate the composed result
    data: dict[str, Any] = {"package": r.package, "layers": r.layers}
    if provenance:
        data["provenance"] = r.provenance
    if output:
        output.write_text(json.dumps(r.package, indent=2) + "\n")
        data["path"] = str(output)

    def human() -> None:
        out.print_json(data=r.package)
        if provenance:
            width = max((len(k) for k in r.provenance), default=0)
            for k, v in r.provenance.items():
                console.print(f"[dim]{k.ljust(width)}  ← {v}[/]")
        if output:
            console.print(f"[green]wrote[/] {output}")

    emit(json_out, data, human, next=[f"renda render {output or package}"])


@command
def info(file: Annotated[Path, typer.Argument(help="Scene file")], json_out: JsonOpt = False):
    """Inspect a scene: format, size, bounds, meshes, materials, cameras, lights."""
    from .formats import detect
    from .ir import load_stage

    fmt = detect(file)
    stage = load_stage(file, fmt)
    lo, hi = stage.bounds()
    data = {
        "file": str(file), "format": fmt, "upAxis": stage.up_axis,
        "meshes": len(stage.meshes), "triangles": stage.triangle_count(), "vertices": stage.vertex_count(),
        "splats": stage.splat_count(), "shDegree": max((s.sh_degree for s in stage.splats), default=None),
        "bounds": {"min": [round(float(x), 6) for x in lo], "max": [round(float(x), 6) for x in hi],
                   "size": [round(float(x), 6) for x in hi - lo]},
        "materials": sorted(stage.materials), "cameras": [c.name for c in stage.cameras],
        "lights": [{"name": l.name, "type": l.type} for l in stage.lights],
        "frameRange": list(stage.frame_range) if stage.frame_range else None,
        "warnings": stage.meta.get("warnings", []),
    }

    def human():
        t = Table(show_header=False, box=None)
        for k in ("format", "upAxis", "meshes", "triangles", "vertices") + (("splats", "shDegree") if data["splats"] else ()):
            t.add_row(k, str(data[k]))
        t.add_row("size", " × ".join(f"{v:.4g}" for v in data["bounds"]["size"]))
        t.add_row("materials", ", ".join(data["materials"]) or "-")
        t.add_row("cameras", ", ".join(data["cameras"]) or "-")
        t.add_row("lights", ", ".join(f"{l['name']} ({l['type']})" for l in data["lights"]) or "-")
        out.print(t)
        for w in data["warnings"]:
            console.print(f"[yellow]warning[/]: {w}")

    emit(json_out, data, human)


@command
def convert(
    input: Annotated[Path, typer.Argument(help="Scene file")],
    output: Annotated[Path, typer.Argument(help="Output: .rir .glb .gltf .obj .usd .usda .usdc; splats: .ply .splat .spz")],
    json_out: JsonOpt = False,
):
    """Convert between formats via the Renda IR (e.g. .blend → .glb, three.js JSON → .usd, .ply splats → .spz)."""
    from .ir import load_stage
    from .ir.exporters import export

    stage = load_stage(input)
    ext = output.suffix.lower()
    if stage.splats and (ext in (".splat", ".spz") or (ext == ".ply" and not stage.meshes)):
        from .ir.loaders.splat import FILE_UP, write_any
        from .ir.splats import Splats
        from .scene import FLIP_Y_DOWN

        merged = Splats.concat(stage.splats)
        if (stage.up_axis == "-Y") != (FILE_UP[ext] == "-Y"):  # each container has its own axis convention
            merged.transform = FLIP_Y_DOWN
        path = write_any(merged, output)
        emit(json_out, {"output": str(path), "splats": len(merged), "shDegree": merged.sh_degree,
                        "warnings": ([".splat stores no view-dependent colour (SH bands dropped)"]
                                     if ext == ".splat" and merged.sh_degree else [])}, lambda: out.print(str(path)))
        return
    path = export(stage, output)
    emit(json_out, {"output": str(path), "meshes": len(stage.meshes), "triangles": stage.triangle_count(),
                    "warnings": stage.meta.get("warnings", [])}, lambda: out.print(str(path)))


@command
def engines(json_out: JsonOpt = False):
    """List render engines and whether they're available on this machine."""
    from .engines import registry

    rows = [e.describe() for e in registry().values()]

    def human():
        t = Table("engine", "available", "version", "native formats", "notes")
        for r in rows:
            t.add_row(r["name"], "[green]yes[/]" if r["available"] else "[red]no[/]", r["version"] or "-",
                      " ".join(r["accepts"]), r["reason"] or "")
        out.print(t)
        console.print("[dim]Every engine renders every format: non-native formats are converted through the Renda IR.[/]")

    emit(json_out, {"engines": rows}, human)


@command
def init(
    file: Annotated[Path, typer.Argument(help="Scene file to wrap")],
    output: Annotated[Optional[Path], typer.Option("-o", help="Package path (default: <name>.renda.json)")] = None,
    force: Annotated[bool, typer.Option(help="Overwrite")] = False,
    json_out: JsonOpt = False,
):
    """Write a starter .renda.json next to a scene file."""
    from .spec import load_package

    dst = output or file.with_name(f"{file.stem}.renda.json")
    if dst.exists() and not force:
        raise CliError("exists", f"{dst} already exists", "pass --force to overwrite")
    rel = Path(file).resolve().relative_to(dst.parent.resolve()) if Path(file).resolve().is_relative_to(dst.parent.resolve()) else Path(file).resolve()
    doc = {
        "$schema": "https://renda.dev/schema/v1.json",
        "version": 1,
        "name": file.stem,
        "assets": {"scene": {"uri": f"./{rel}" if not rel.is_absolute() else str(rel)}},
        "scene": {"root": "scene"},
        "camera": {"mode": "auto", "fovDeg": 35, "elevationDeg": 20, "azimuthDeg": 35, "padding": 0.1},
        "environment": {"hdri": "studio", "intensity": 1, "background": "hdri"},
        "render": {"engine": "auto", "resolution": [1920, 1080], "quality": "standard"},
        "output": {"format": "png"},
    }
    load_package(doc)
    dst.write_text(json.dumps(doc, indent=2) + "\n")
    emit(json_out, {"package": str(dst)}, lambda: out.print(str(dst)))


@command
def estimate(
    input: Annotated[Path, typer.Argument(help="Scene file or .renda.json package")],
    priority: Annotated[str, typer.Option(help="economy|standard|rush")] = "standard",
    fast: Annotated[bool, typer.Option("--fast", help="Fast mode (= --priority rush): fan out wider on faster GPUs; costs a bit more")] = False,
    engine: Annotated[Optional[str], typer.Option(help="Engine override")] = None,
    res: Annotated[Optional[str], typer.Option(help="Resolution WxH")] = None,
    quality: Annotated[Optional[str], typer.Option(help="draft|standard|final")] = None,
    frames: Annotated[Optional[str], typer.Option(help="Frame range a-b")] = None,
    fmt: Annotated[Optional[str], typer.Option("--format", help="Output format")] = None,
    turntable: Annotated[bool, typer.Option(help="360° camera orbit")] = False,
    texture: Annotated[Optional[list[str]], typer.Option("--texture", help="Texture override, as for `render`")] = None,
    local: Annotated[bool, typer.Option("--local", help="Use the local planner only (no network)")] = False,
    json_out: JsonOpt = False,
):
    """Estimate cloud render time and cost (takes the same overrides as `render`)."""
    from .assets import local_files
    from .planner import estimate_cost, plan, priority_estimates
    from .spec import load_package

    raw, base = load_input(input)
    raw = apply_overrides(raw, engine=engine, res=res, quality=quality, frames=frames, fmt=fmt, turntable=turntable,
                          textures=texture)
    pkg = load_package(raw)
    data = None
    if not local:
        from .cloud import CloudError, api_key

        if api_key():
            try:
                # local paths aren't uploaded for an estimate; the server only needs formats + settings
                data = _client().estimate(raw, "rush" if fast else priority)
                data["source"] = "cloud"
            except CloudError as e:
                if e.code in ("unauthenticated", "network_error"):
                    data = None
                else:
                    raise
    if data is None:
        files = {k: p for k, p in local_files(pkg, base).items() if p.exists()}
        tris = None
        try:
            from .ir import load_stage
            from .scene import asset_path

            root = asset_path(pkg, pkg.scene.root, files)
            if root.suffix.lower() not in (".blend", ".fbx", ".abc"):
                tris = load_stage(root).triangle_count()
        except Exception:
            pass
        p = plan(pkg, files, "rush" if fast else priority, triangles=tris)
        lo, hi = estimate_cost(p)
        single = bool((p.splat_bake or {}).get("single_container"))
        data = {"source": "local", "engine": p.engine, "hardware": p.hardware, "frames": len(p.frames),
                "containers": 1 if single else len(p.chunks), "estSeconds": list(p.est_seconds),
                "estWallSeconds": list(p.est_wall_seconds), "estCostUsd": [lo, hi], "warnings": p.warnings,
                "priorities": priority_estimates(pkg, files, triangles=tris)}

    def human():
        s = data.get("estSeconds", [0, 0])
        c = data.get("estCostUsd", [0, 0])
        out.print(f"{data.get('engine', '?')} on {data.get('hardware', '?')}: ~{s[0]:.0f}–{s[1]:.0f}s, ${c[0]:.4f}–${c[1]:.4f}"
                  f" [dim]({data['source']} estimate)[/]")
        for pr, e in (data.get("priorities") or {}).items():
            w, pc = e.get("estWallSeconds", [0, 0]), e.get("estCostUsd", [0, 0])
            n = e.get("containers", e.get("chunks", 1))
            out.print(f"  [dim]--priority {pr:<8}[/] ${pc[0]:.4f}–${pc[1]:.4f}, wall ~{w[0]:.0f}–{w[1]:.0f}s "
                      f"on {e.get('hardware', '?')} × {n}")
        prs = data.get("priorities") or {}
        if "rush" in prs and "standard" in prs:
            f, st = prs["rush"], prs["standard"]
            out.print(f"  fast (--fast): ${f['estCostUsd'][1]:.4f} in ~{_mins(f['estWallSeconds'][1])}  vs  standard: "
                      f"${st['estCostUsd'][1]:.4f} in ~{_mins(st['estWallSeconds'][1])}  [dim](upper estimates)[/]")
        for w in data.get("warnings", []):
            console.print(f"[yellow]warning[/]: {w}")

    emit(json_out, data, human)


# -- cloud account commands -------------------------------------------------------


@command
def signup(email: Annotated[str, typer.Argument(help="Your email")], json_out: JsonOpt = False):
    """Create an account / sign in: emails a 6-digit code. Then run `renda verify <code>`."""
    from .cloud import load_credentials, save_credentials

    _client().magic_start(email)
    creds = load_credentials()
    creds["pendingEmail"] = email
    save_credentials(creds)
    emit(json_out, {"ok": True, "email": email, "next": "renda verify <code>"},
         lambda: console.print(f"code sent to [bold]{email}[/] — run [bold]renda verify <code>[/]"))


@command
def verify(
    code: Annotated[str, typer.Argument(help="6-digit code from the email")],
    email: Annotated[Optional[str], typer.Option(help="Email (defaults to the one from `renda signup`)")] = None,
    json_out: JsonOpt = False,
):
    """Finish signup with the emailed code; saves an API key."""
    from .cloud import api_url, load_credentials, save_credentials

    creds = load_credentials()
    email = email or creds.get("pendingEmail")
    if not email:
        raise CliError("no_pending_signup", "no email to verify", "run `renda signup <email>` first or pass --email")
    res = _client().magic_verify(email, code.strip())
    creds.pop("pendingEmail", None)
    creds.pop("claimUrl", None)
    creds.pop("trialExpiresAt", None)
    creds.update({"apiKey": res["apiKey"], "email": email, "apiUrl": api_url()})
    save_credentials(creds)
    safe = {k: v for k, v in res.items() if k != "apiKey"}
    emit(json_out, {"ok": True, **safe, "credentials": str(_creds_path())},
         lambda: console.print(f"[green]signed in[/] as {email}; key saved to {_creds_path()}"))


@command
def login(json_out: JsonOpt = False):
    """Sign in through the browser (device flow)."""
    from .cloud import api_url, load_credentials, save_credentials

    client = _client()
    d = client.device_start()
    url = d.get("verificationUriComplete") or d["verificationUri"]
    if json_out:
        print(json.dumps({"verificationUri": url, "userCode": d["userCode"], "expiresIn": d.get("expiresIn")}), flush=True)
    else:
        console.print(f"open [bold]{url}[/] and confirm code [bold]{d['userCode']}[/]")
        try:
            import webbrowser

            webbrowser.open(url)
        except Exception:
            pass
    deadline = time.time() + float(d.get("expiresIn", 600))
    interval = float(d.get("interval", 5))
    while time.time() < deadline:
        time.sleep(interval)
        res = client.device_token(d["deviceCode"])
        if res.get("pending"):
            continue
        creds = load_credentials()
        creds.pop("claimUrl", None)
        creds.pop("trialExpiresAt", None)
        creds.update({"apiKey": res["apiKey"], "apiUrl": api_url(), "email": (res.get("user") or {}).get("email")})
        save_credentials(creds)
        emit(json_out, {"ok": True, "credentials": str(_creds_path())}, lambda: console.print("[green]signed in[/]"))
        return
    raise CliError("expired", "device code expired", "run `renda login` again")


@command
def logout(json_out: JsonOpt = False):
    """Forget the saved API key."""
    from .cloud import load_credentials, save_credentials

    creds = load_credentials()
    creds.pop("apiKey", None)
    save_credentials(creds)
    emit(json_out, {"ok": True}, lambda: console.print("logged out"))


@command
def whoami(json_out: JsonOpt = False):
    """Show the signed-in account, free minutes left and billing status."""
    me = _client().me()

    def human():
        u, org = me.get("user", {}), me.get("org", {})
        left = me.get("balance", {}).get("freeSecondsLeft", 0) / 60
        card = me.get("billing", {}).get("hasPaymentMethod")
        out.print(f"{u.get('email')} · {org.get('name', '')} · {left:.1f} free min left · card: {'yes' if card else 'no'}")

    emit(json_out, me, human)


@command
def jobs(
    job_id: Annotated[Optional[str], typer.Argument(help="Job id (omit to list recent jobs)")] = None,
    limit: Annotated[int, typer.Option(help="How many to list")] = 20,
    cancel: Annotated[bool, typer.Option("--cancel", help="Cancel the job")] = False,
    download: Annotated[Optional[Path], typer.Option("--download", help="Download outputs to this directory")] = None,
    json_out: JsonOpt = False,
):
    """List jobs, show one, cancel it, or download its outputs."""
    client = _client()
    if job_id is None:
        rows = client.list_jobs(limit)

        def human():
            t = Table("id", "status", "engine", "hardware", "billed s", "cost", "created")
            for j in rows:
                t.add_row(j["id"], j["status"], j.get("engine") or "-", j.get("hardware") or "-",
                          f"{j.get('billedSeconds', 0):.1f}", f"${j.get('costUsd', 0):.4f}", j.get("createdAt", ""))
            out.print(t)

        emit(json_out, {"jobs": rows}, human)
        return
    job = client.cancel_job(job_id) if cancel else client.get_job(job_id)
    files = client.download_outputs(job, download) if download and job.get("status") == "succeeded" else []
    data = {**job, "files": [str(f) for f in files]} if files else job
    emit(json_out, data)


@command
def usage(
    since: Annotated[Optional[str], typer.Option("--from", help="ISO date")] = None,
    until: Annotated[Optional[str], typer.Option("--to", help="ISO date")] = None,
    json_out: JsonOpt = False,
):
    """Render minutes and spend."""
    u = _client().usage(since, until)

    def human():
        s = u.get("seconds", {})
        out.print(f"cpu {s.get('cpu', 0) / 60:.1f} min · gpu {s.get('gpu', 0) / 60:.1f} min · "
                  f"billed {u.get('minutesBilled', 0):.1f} min · ${u.get('costUsd', 0):.2f}")

    emit(json_out, u, human)


@command
def billing(
    portal: Annotated[bool, typer.Option("--portal", help="Open the billing portal instead of adding a card")] = False,
    json_out: JsonOpt = False,
):
    """Get a link to add a card (or manage billing). Share it with the account owner."""
    client = _client()
    res = client.billing_portal() if portal else client.billing_checkout()
    emit(json_out, res, lambda: out.print(res["url"]))


@command
def trial(
    force: Annotated[bool, typer.Option("--force", help="Start a fresh trial even if signed in")] = False,
    json_out: JsonOpt = False,
):
    """Start an anonymous trial: 5 free render-minutes, no signup. Prints a claim link for the human."""
    client = _client()
    if client.key and not force:
        from .cloud import load_credentials

        emit(json_out, {"alreadySignedIn": True, "claimUrl": load_credentials().get("claimUrl")},
             lambda: console.print("already signed in on this machine (use --force for a fresh trial)"), ["renda whoami"])
        return
    client.key = None
    claim = _ensure_key(client, True)
    from .cloud import load_credentials

    creds = load_credentials()
    data = {"claimUrl": claim, "expiresAt": creds.get("trialExpiresAt"), "credentials": str(_creds_path())}
    emit(json_out, data, lambda: console.print(f"[green]trial started[/] (max {(data.get('limits') or {}).get('maxDimension', '?')}px, expires {str(data['expiresAt'])[:10]} unless claimed)\n"
                                                f"keep it: [bold]{claim}[/]"),
         [f"Give the user this link to keep the workspace: {claim}", "renda render <file> --cloud --quality draft"])


@command
def claim(json_out: JsonOpt = False):
    """Print the link that keeps an anonymous trial workspace (give it to the human)."""
    from .cloud import load_credentials

    creds = load_credentials()
    url = creds.get("claimUrl")
    if not url:
        emit(json_out, {"claimUrl": None}, lambda: console.print("no pending trial claim on this machine"), ["renda whoami"])
        return
    emit(json_out, {"claimUrl": url, "expiresAt": creds.get("trialExpiresAt")}, lambda: out.print(url),
         ["Agents: give this link to the human; don't open it yourself."])


@command
def balance(json_out: JsonOpt = False):
    """Free render-minutes left and whether a card is on file."""
    me = _client().me()
    left = me.get("balance", {}).get("freeSecondsLeft", 0)
    card = me.get("billing", {}).get("hasPaymentMethod", False)
    emit(json_out, {"freeSecondsLeft": left, "hasPaymentMethod": card, "trial": me.get("trial")},
         lambda: out.print(f"{left / 60:.1f} free min · {'card on file' if card else 'no card'}"),
         [] if card or left > 0 else ["renda billing"])


@command
def mcp(
    action: Annotated[str, typer.Argument(help="add | remove | serve")] = "serve",
    agent: Annotated[Optional[str], typer.Option(help="claude-code | cursor | codex | all (default: detected)")] = None,
    remote: Annotated[bool, typer.Option("--remote", help="Use the hosted MCP endpoint instead of the local stdio server")] = False,
    json_out: JsonOpt = False,
):
    """Run or install the Renda MCP server (delegates to the Node package via npx)."""
    import shutil
    import subprocess

    npx = shutil.which("npx")
    if not npx:
        raise CliError("node_missing", "the Renda MCP server runs on Node, and npx isn't installed",
                       "install Node 20+ (https://nodejs.org) — then rerun", ["claude mcp add renda -- npx -y @rendahq/mcp"])
    if action == "serve":
        raise typer.Exit(subprocess.call([npx, "-y", "@rendahq/mcp"]))
    if action not in ("add", "remove"):
        raise CliError("usage", f"unknown action {action!r}", "use add, remove or serve", exit_code=EXIT_USAGE)
    cmd = [npx, "-y", NODE_CLI, "mcp", action] + (["--agent", agent] if agent else []) + (["--remote"] if remote else [])
    if json_out:
        cmd.append("--json")
    raise typer.Exit(subprocess.call(cmd))


@command
def version(json_out: JsonOpt = False):
    """Print the renda version."""
    emit(json_out, {"version": __version__}, lambda: out.print(__version__))


def _creds_path() -> Path:
    from .cloud.client import credentials_path

    return credentials_path()


if __name__ == "__main__":  # pragma: no cover
    app()
