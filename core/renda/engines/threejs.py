"""three.js adapter: headless Chromium (Playwright) + WebGL. The cheapest preview engine.

Renders three.js JSON natively (ObjectLoader) and glTF natively (GLTFLoader);
anything else, or packages with material overrides/includes, goes IR → .glb.
three.js is vendored on first use from npm (pinned) into the renda cache.
"""

from __future__ import annotations

import base64
import functools
import http.server
import io
import json
import os
import shutil
import tarfile
import threading
import time
from pathlib import Path

from .base import Capabilities, Engine, RenderContext, RenderResult, frame_filename, samples_for

THREE_VERSION = "0.180.0"  # Spark (splats) needs >= 0.180
SPARK_VERSION = "2.3.1"
HERE = Path(__file__).resolve().parent / "threejs_render"


def three_dir() -> Path:
    """Vendored three.js package (npm tarball) — downloaded once, pinned."""
    root = Path(os.environ.get("RENDA_CACHE_DIR", Path.home() / ".cache" / "renda")) / f"three-{THREE_VERSION}"
    if (root / "build" / "three.module.js").exists():
        return root
    import httpx

    url = f"https://registry.npmjs.org/three/-/three-{THREE_VERSION}.tgz"
    data = httpx.get(url, timeout=120, follow_redirects=True).raise_for_status().content
    tmp = root.with_suffix(".part")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
        members = [m for m in tf.getmembers() if m.name.startswith(("package/build/", "package/examples/jsm/"))]
        tf.extractall(tmp, members=members, filter="data")
    (tmp / "package").rename(root)
    shutil.rmtree(tmp, ignore_errors=True)
    return root


def _vendor_npm(name: str, version: str, dirname: str, keep: tuple[str, ...]) -> Path:
    root = Path(os.environ.get("RENDA_CACHE_DIR", Path.home() / ".cache" / "renda")) / dirname
    if root.exists():
        return root
    import httpx

    base = name.split("/")[-1]
    url = f"https://registry.npmjs.org/{name}/-/{base}-{version}.tgz"
    data = httpx.get(url, timeout=120, follow_redirects=True).raise_for_status().content
    tmp = root.with_suffix(".part")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
        members = [m for m in tf.getmembers() if m.name.startswith(keep) and not m.name.endswith(".map")]
        tf.extractall(tmp, members=members, filter="data")
    (tmp / "package").rename(root)
    shutil.rmtree(tmp, ignore_errors=True)
    return root


def spark_dir() -> Path:
    """Vendored Spark (sparkjs.dev) Gaussian splat renderer for three.js, pinned."""
    return _vendor_npm("@sparkjsdev/spark", SPARK_VERSION, f"spark-{SPARK_VERSION}", ("package/dist/spark.module.js", "package/LICENSE"))


def _chromium_ok() -> bool:
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError:
        return False
    browsers = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", Path.home() / (
        "Library/Caches/ms-playwright" if os.uname().sysname == "Darwin" else ".cache/ms-playwright")))
    return browsers.exists() and any(p.name.startswith(("chromium", "chromium_headless_shell")) for p in browsers.iterdir())


def _texture_jobs(ctx: RenderContext, stage, frames: list[int], work: Path, warnings: list[str]) -> list[dict]:
    """Texture overrides for the page: match on the IR (before glTF export, so object-scoped material
    copies are in the file), copy each frame's image next to index.html, and let the page assign them."""
    from ..textures import TexturePlan, resolve_texture_overrides

    resolved = resolve_texture_overrides(ctx.pkg, ctx.asset_paths, frames, ctx.cache_dir)
    plan = TexturePlan(stage, resolved)
    warnings += plan.warnings()
    tex_dir = work / "tex"
    tex_dir.mkdir(exist_ok=True)
    copied: dict[str, str] = {}

    def url(p: Path) -> str:
        key = str(p)
        if key not in copied:
            name = f"t{len(copied):05d}"
            if p.suffix.lower() in (".exr", ".hdr"):  # the page loads plain images; clamp HDR to LDR
                import numpy as np
                from PIL import Image

                from ..images import linear_to_srgb, read_hdr_image

                arr = np.clip(read_hdr_image(p)[..., :3], 0, 1)
                Image.fromarray((linear_to_srgb(arr) * 255 + 0.5).astype(np.uint8)).save(tex_dir / f"{name}.png")
                copied[key] = f"tex/{name}.png"
            else:
                shutil.copyfile(p, tex_dir / f"{name}{p.suffix.lower()}")
                copied[key] = f"tex/{name}{p.suffix.lower()}"
        return copied[key]

    jobs = []
    for r, targets in zip(resolved, plan.targets):
        if not targets:
            continue
        jobs.append({
            "targets": [{"material": m, "slot": s, "colorSpace": r.colorspace(s)} for m, s in targets],
            "frames": {str(f): url(r.path(f)) for f in frames},
            "uv": list(r.uv()),
            "strength": r.spec.strength,
        })
    return jobs


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):  # noqa: D401
        pass


class ThreeEngine(Engine):
    name = "threejs"
    accepts = frozenset({"threejs", "gltf"})
    capabilities = Capabilities(
        gpu=True, passes=frozenset({"beauty", "alpha"}), animation=True,
        cost_cpu=0.05, cost_gpu=0.01, load_seconds=3.0,
    )

    def available(self) -> bool:
        return _chromium_ok()

    def unavailable_reason(self) -> str | None:
        return "uv tool install 'renda[threejs]' && uvx playwright install chromium"

    def version(self) -> str | None:
        return f"three@{THREE_VERSION}"

    def render(self, ctx: RenderContext, frames: list[int]) -> RenderResult:
        from PIL import Image
        from playwright.sync_api import sync_playwright

        from ..ir import load_stage
        from ..ir.camera import resolve_cameras
        from ..ir.exporters import to_gltf
        from ..scene import asset_path, build_stage, env_map_path
        from ..spec import parse_color

        t0 = time.time()
        pkg, plan = ctx.pkg, ctx.plan
        warnings: list[str] = []
        import tempfile

        base = Path(ctx.cache_dir or ctx.out_dir) / "threejs-site"  # cache_dir may be shared: unique per render
        base.mkdir(parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(dir=base))
        shutil.copyfile(HERE / "index.html", work / "index.html")
        for name, src in (("three", three_dir), ("spark", spark_dir)):
            link = work / name
            if link.is_symlink() and not link.exists():
                link.unlink()
            if not link.exists():
                try:
                    link.symlink_to(src(), target_is_directory=True)
                except OSError:
                    shutil.copytree(src(), link)

        fmt = plan.scene_format
        root = asset_path(pkg, pkg.scene.root, ctx.asset_paths)
        native = (fmt in self.accepts and not pkg.scene.materialOverrides and not pkg.scene.textureOverrides
                  and not pkg.scene.include and pkg.scene.unitScale == 1)
        splat_jobs: list[dict] = []
        texture_jobs: list[dict] = []
        if fmt == "splat" and root.suffix.lower() == ".ksplat":
            # Only Spark decodes .ksplat: hand it over untouched (assumed COLMAP/OpenCV axes).
            from ..ir import Stage
            from ..spec import LookAtCamera

            if not isinstance(pkg.camera, LookAtCamera):
                raise RuntimeError(".ksplat can't be auto-framed (renda can't read its bounds); use a lookat camera "
                                   "(Y-up coordinates) or convert the file to .ply/.spz")
            shutil.copyfile(root, work / "scene.ksplat")
            splat_jobs.append({"url": "scene.ksplat", "fileType": "ksplat", "flipYDown": pkg.scene.upAxis in (None, "-Y")})
            stage = Stage(up_axis="Y")
            scene_url, kind = "", "none"
        elif native:
            stage = load_stage(root, fmt)
            warnings += stage.meta.pop("warnings", [])
            ext = ".json" if fmt == "threejs" else root.suffix.lower()
            shutil.copyfile(root, work / f"scene{ext}")
            # .gltf with external buffers/textures: copy siblings too
            if ext == ".gltf":
                for sib in root.parent.iterdir():
                    if sib.is_file() and sib != root and not (work / sib.name).exists():
                        shutil.copyfile(sib, work / sib.name)
            scene_url, kind = f"scene{ext}", ("threejs" if fmt == "threejs" else "gltf")
        else:
            stage, w = build_stage(pkg, ctx.asset_paths, ctx.cache_dir)
            warnings += w
            if pkg.scene.textureOverrides:
                texture_jobs = _texture_jobs(ctx, stage, frames, work, warnings)
            if stage.meshes or not stage.splats:
                to_gltf(stage, work / "scene.glb")
                scene_url, kind = "scene.glb", "gltf"
            else:
                scene_url, kind = "", "none"
            if stage.splats:
                from ..ir.loaders.splat import write_ply
                from ..ir.splats import Splats

                write_ply(Splats.concat(stage.splats), work / "splats.ply")
                splat_jobs.append({"url": "splats.ply", "fileType": "ply", "flipYDown": False})
                if pkg.lights:
                    warnings.append("threejs: splats carry baked lighting; package lights only affect meshes")
        if pkg.scene.upAxis and pkg.scene.upAxis != "-Y":
            stage.up_axis = pkg.scene.upAxis

        cams = resolve_cameras(pkg, stage, frames, warnings)
        env = pkg.environment
        hdri = env_map_path(pkg, ctx.asset_paths)
        if hdri:
            shutil.copyfile(hdri, work / "env.hdr")
        bg = env.background
        transparent = pkg.output.transparent
        background = None if bg in ("hdri", "transparent") or transparent else list(parse_color(bg)[:3])
        if stage.up_axis == "Z" and hdri:
            warnings.append("threejs: environment maps are Y-up; lighting direction may differ for Z-up scenes")

        lights = []
        for l in pkg.lights:
            d = l.model_dump()
            d["color"] = list(parse_color(l.color, (1.0, 1.0, 1.0))[:3])
            if l.type in ("point", "spot"):
                d["candela"] = l.intensity * 683.0 / (4 * 3.141592653589793)
            lights.append(d)

        ss = max(1, min(samples_for(self.name, pkg.render.quality, pkg.render.samples), 4))
        w, h = pkg.render.resolution
        while ss > 1 and max(w, h) * ss > 8192:
            ss -= 1
        job = {
            "scene": {"url": scene_url, "kind": kind},
            "splats": splat_jobs,
            "width": w * ss, "height": h * ss, "transparent": transparent or bg == "transparent",
            "background": background,
            "env": {"url": "env.hdr" if hdri else None, "room": hdri is None, "intensity": env.intensity,
                    "rotationDeg": env.rotationDeg, "visible": bg == "hdri" and not transparent},
            "lights": lights,
            "cameras": {str(f): c.to_json() for f, c in cams.items()},
            "textures": texture_jobs,
            "fps": pkg.animation.fps if pkg.animation else None,
            "frameStart": pkg.animation.frames[0] if pkg.animation else 1,
        }

        handler = functools.partial(_Quiet, directory=str(work))
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        files: list[Path] = []
        out_fmt = pkg.output.format if not pkg.output.is_video else "png"
        try:
            with sync_playwright() as p:
                args = ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"]
                if plan.hardware.startswith("gpu"):
                    args = ["--use-angle=vulkan", "--enable-features=Vulkan", "--ignore-gpu-blocklist"]
                browser = p.chromium.launch(args=args)
                page = browser.new_page(viewport={"width": min(w * ss, 4096), "height": min(h * ss, 4096)})
                page.add_init_script(f"window.RENDA_JOB = {json.dumps(job)};")
                logs: list[str] = []
                page.on("console", lambda m: logs.append(m.text) if m.type in ("error", "warning") else None)
                page.goto(f"http://127.0.0.1:{server.server_address[1]}/index.html")
                page.wait_for_function("window.RENDA_READY !== undefined", timeout=120_000)
                ready = page.evaluate("window.RENDA_READY")
                if not ready.get("ok"):
                    raise RuntimeError(f"three.js failed to load the scene: {ready.get('error')}\n" + "\n".join(logs[-10:]))
                warnings += ready.get("warnings", [])
                for f in frames:
                    url = page.evaluate("(f) => window.renderFrame(f)", f)
                    img = Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1]))).convert("RGBA")
                    if ss > 1:
                        img = img.resize((w, h), Image.LANCZOS)
                    alpha = img.getchannel("A")
                    if not job["transparent"]:
                        img = img.convert("RGB")
                    dst = ctx.out_dir / frame_filename("beauty", f, out_fmt)
                    if out_fmt == "exr":
                        import numpy as np

                        from ..images import save_exr

                        arr = np.asarray(img, dtype=np.float32) / 255.0
                        save_exr(np.where(arr <= 0.04045, arr / 12.92, ((arr + 0.055) / 1.055) ** 2.4), dst)
                    else:
                        img.save(dst, format={"png": "PNG", "jpg": "JPEG", "webp": "WEBP"}[out_fmt],
                                 **({"quality": 92} if out_fmt in ("jpg", "webp") else {}))
                    files.append(dst)
                    if "alpha" in pkg.output.passes:
                        a = ctx.out_dir / frame_filename("alpha", f, "png")
                        alpha.save(a)
                        files.append(a)
                browser.close()
        finally:
            server.shutdown()
            shutil.rmtree(work, ignore_errors=True)
        unsupported = [p for p in pkg.output.passes if p not in self.capabilities.passes]
        if unsupported:
            warnings.append(f"threejs: passes {unsupported} unsupported, skipped")
        return RenderResult(files, warnings, time.time() - t0)
