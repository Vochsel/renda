"""Generate the texture-override test set: procedural (license-clean) textures in many formats, a short
video, and a small product mesh with a named "Screen" material.

    uv run --directory core python ../testdata/textures/generate.py

Needs Pillow (+ pillow-heif for .heic, psd-tools for .psd), imageio-ffmpeg and trimesh. Writes next to
this file; every output is small (512 px) so the folder stays well under 5 MB.
"""

from __future__ import annotations

import math
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
N = 512


def wood(n: int = N, seed: int = 3) -> np.ndarray:
    """Oak-ish rings: distance field + noise, warm palette."""
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:n, 0:n] / n
    noise = sum(np.sin((x * f + rng.random()) * 2 * math.pi + np.sin(y * f * 0.7) * 2) / f for f in (3, 7, 13, 29))
    rings = np.sin((np.hypot(x * 0.35 - 0.6, y * 3.2 + 0.3) * 38 + noise * 1.6)) * 0.5 + 0.5
    grain = (np.sin(y * n * 1.3 + noise * 9) * 0.5 + 0.5) * 0.15
    t = np.clip(rings * 0.75 + grain, 0, 1)[..., None]
    light, dark = np.array([214, 168, 112]), np.array([128, 82, 44])
    return (dark + (light - dark) * t).astype(np.uint8)


def marble(n: int = N) -> np.ndarray:
    y, x = np.mgrid[0:n, 0:n] / n
    v = np.sin((x + y) * 9 + np.sin(x * 23) * 0.9 + np.sin(y * 31) * 0.5) * 0.5 + 0.5
    return np.stack([0.85 + 0.1 * v, 0.86 + 0.1 * v, 0.9 + 0.08 * v], -1).astype(np.float32)


def normal_bumps(n: int = N) -> np.ndarray:
    y, x = np.mgrid[0:n, 0:n] / n * 8 * math.pi
    hx, hy = np.cos(x) * np.sin(y), np.sin(x) * np.cos(y)
    nrm = np.stack([-hx * 0.5, -hy * 0.5, np.ones_like(hx)], -1)
    nrm /= np.linalg.norm(nrm, axis=-1, keepdims=True)
    return ((nrm * 0.5 + 0.5) * 255).astype(np.uint8)


def write_hdr(path: Path, rgb: np.ndarray) -> None:
    """Minimal Radiance RGBE writer (uncompressed scanlines)."""
    h, w, _ = rgb.shape
    mx = rgb.max(axis=-1)
    e = np.where(mx > 1e-32, np.ceil(np.log2(np.maximum(mx, 1e-32))), -128).astype(np.int32)
    scale = np.where(mx > 1e-32, 256.0 / np.exp2(e), 0)[..., None]
    rgbe = np.concatenate([np.clip(rgb * scale, 0, 255), (e + 128).clip(0, 255)[..., None]], -1).astype(np.uint8)
    with open(path, "wb") as f:
        f.write(b"#?RADIANCE\nFORMAT=32-bit_rle_rgbe\n\n" + f"-Y {h} +X {w}\n".encode())
        f.write(rgbe.tobytes())


def logo_svg(path: Path) -> None:
    path.write_text("""<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 256 256">
  <rect width="256" height="256" fill="#f4f1ea"/>
  <circle cx="128" cy="112" r="64" fill="none" stroke="#e8553d" stroke-width="22"/>
  <path d="M96 112 L128 80 L160 112 L128 144 Z" fill="#1d2433"/>
  <rect x="48" y="196" width="160" height="22" rx="11" fill="#1d2433"/>
</svg>
""")


def counter_frames(n_frames: int = 48, size: int = 384) -> list[Image.Image]:
    """An animated screen: hue sweep, moving bar and a big frame counter (so sync is visible)."""
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 150)
    except OSError:
        font = ImageFont.load_default(size=150) if hasattr(ImageFont, "load_default") else None
    frames = []
    for i in range(n_frames):
        t = i / n_frames
        y, x = np.mgrid[0:size, 0:size] / size
        r = 0.5 + 0.5 * np.sin(2 * math.pi * (t + x))
        g = 0.5 + 0.5 * np.sin(2 * math.pi * (t + y + 1 / 3))
        b = np.full_like(x, 0.5 + 0.5 * math.sin(2 * math.pi * (t + 2 / 3)))
        img = Image.fromarray((np.stack([r, g, b], -1) * 200 + 30).astype(np.uint8))
        d = ImageDraw.Draw(img)
        bar = int(t * size)
        d.rectangle([bar - 12, 0, bar + 12, size], fill=(255, 255, 255))
        d.text((size / 2, size / 2), f"{i:02d}", fill=(15, 15, 20), anchor="mm", font=font)
        frames.append(img)
    return frames


def write_mp4(frames: list[Image.Image], path: Path, fps: int = 24) -> None:
    import imageio_ffmpeg

    w, h = frames[0].size
    cmd = [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-s", f"{w}x{h}", "-r", str(fps), "-i", "-", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "26",
           "-movflags", "+faststart", str(path)]
    subprocess.run(cmd, input=b"".join(f.tobytes() for f in frames), check=True)


def product_glb(path: Path) -> None:
    """A phone-ish slab: 'Body' (dark aluminium) + a UV-mapped 'Screen' quad on the front."""
    import trimesh

    body = trimesh.creation.box(extents=[0.72, 1.46, 0.08])
    body.visual = trimesh.visual.TextureVisuals(
        uv=np.zeros((len(body.vertices), 2)),
        material=trimesh.visual.material.PBRMaterial(name="Body", baseColorFactor=[40, 42, 48, 255],
                                                     metallicFactor=0.8, roughnessFactor=0.35))
    w, h, z = 0.64, 1.34, 0.0405
    verts = np.array([[-w / 2, -h / 2, z], [w / 2, -h / 2, z], [w / 2, h / 2, z], [-w / 2, h / 2, z]])
    uv = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], dtype=float)  # trimesh: v up (flipped on glTF export)
    screen = trimesh.Trimesh(vertices=verts, faces=[[0, 1, 2], [0, 2, 3]], process=False)
    screen.visual = trimesh.visual.TextureVisuals(
        uv=uv, material=trimesh.visual.material.PBRMaterial(name="Screen", baseColorFactor=[10, 10, 12, 255],
                                                           metallicFactor=0.0, roughnessFactor=0.15))
    scene = trimesh.Scene()
    scene.add_geometry(body, node_name="Phone", geom_name="Phone")
    scene.add_geometry(screen, node_name="Display", geom_name="Display")
    scene.export(str(path), file_type="glb")


def main() -> None:
    HERE.mkdir(exist_ok=True)
    w = Image.fromarray(wood())
    w.save(HERE / "oak.jpg", quality=88)
    w.save(HERE / "oak.webp", quality=85)
    w.save(HERE / "oak.avif", quality=70)
    try:
        import pillow_heif

        pillow_heif.register_heif_opener()
        w.save(HERE / "oak.heic", quality=70)
    except ImportError:
        print("skip oak.heic (pip install pillow-heif)")
    # 16-bit greyscale TIFF roughness map
    rough = (np.asarray(w.convert("L").resize((256, 256)), dtype=np.float32) / 255 * 65535).astype(np.uint16)
    Image.fromarray(rough).save(HERE / "oak_roughness16.tif")
    Image.fromarray(normal_bumps()).save(HERE / "bumps_normal.png")
    m = marble(128)  # float formats are big: keep them small
    write_hdr(HERE / "marble.hdr", m)
    try:
        import mitsuba as mi

        mi.set_variant("scalar_rgb")
        mi.Bitmap(np.ascontiguousarray(m)).write(str(HERE / "marble.exr"))
    except ImportError:
        print("skip marble.exr (needs mitsuba or imageio EXR)")
    logo_svg(HERE / "logo.svg")
    try:
        from psd_tools import PSDImage

        PSDImage.frompil(Image.fromarray(wood(256, seed=11)).convert("RGB")).save(HERE / "oak_layer.psd")
    except ImportError:
        print("skip oak_layer.psd (pip install psd-tools)")
    frames = counter_frames()
    write_mp4(frames, HERE / "screen.mp4")
    small = [f.resize((128, 128)) for f in frames[::2]]
    small[0].save(HERE / "screen.gif", save_all=True, append_images=small[1:], duration=1000 // 12, loop=0)
    product_glb(HERE / "phone.glb")
    total = sum(p.stat().st_size for p in HERE.iterdir() if p.is_file())
    print(f"wrote {len(list(HERE.iterdir()))} files, {total / 1e6:.2f} MB")


if __name__ == "__main__":
    main()
