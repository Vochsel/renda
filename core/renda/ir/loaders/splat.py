"""Gaussian splat files ↔ IR Splats.

- 3DGS ``.ply`` (Kerbl et al.): binary little-endian vertices with x/y/z, f_dc_*, f_rest_*,
  opacity (logit), scale_* (log), rot_* (w,x,y,z). COLMAP/OpenCV axes (Y down).
- antimatter15 ``.splat``: 32-byte records — position f32×3, linear scale f32×3, RGBA u8
  (colour after SH band 0, alpha after sigmoid), rotation u8×4 (w,x,y,z, q*128+128). Converted
  from 3DGS .ply without changing axes, so also Y down. No view-dependent colour.
- Niantic ``.spz`` (v2/v3): gzip, 16-byte header then column blocks (24-bit fixed-point
  positions, alpha, colour, log-scale, rotation, SH). Stored in RUB (three.js-style, Y up).
  v4 (current Niantic/PlayCanvas tools): uncompressed 32-byte NGSP header + table of contents,
  then the same columns as separately zstd-compressed streams.
- ``.ksplat`` (mkkellogg's GaussianSplats3D) is not decoded here; the three.js engine reads it
  natively through Spark, everything else asks for a conversion.
"""

from __future__ import annotations

import gzip
import math
import struct
from pathlib import Path

import numpy as np

from ..splats import SH_C0, Splats, logit
from ..stage import Stage

SPZ_MAGIC = 0x5053474E  # "NGSP"
SPZ_COLOR_SCALE = 0.15


class SplatFormatError(ValueError):
    pass


# -- detection helpers (also used by formats.detect) -------------------------------------


def ply_header(path: str | Path, limit: int = 64 * 1024) -> str | None:
    path = Path(path)
    with open(path, "rb") as f:
        head = f.read(limit)
    end = head.find(b"end_header")
    if not head.startswith(b"ply") or end < 0:
        return None
    return head[: end + len("end_header")].decode("latin-1")


def is_splat_ply(path: str | Path) -> bool:
    h = ply_header(path)
    if not h:
        return False
    props = {line.split()[-1] for line in h.splitlines() if line.startswith("property")}
    return {"f_dc_0", "opacity", "scale_0", "rot_0"} <= props


def is_spz(path: str | Path) -> bool:
    with open(path, "rb") as f:
        if f.read(4) == struct.pack("<I", SPZ_MAGIC):  # v4: header isn't compressed
            return True
    try:
        with gzip.open(path, "rb") as f:
            head = f.read(8)
    except (OSError, EOFError):
        return False
    return len(head) == 8 and struct.unpack("<I", head[:4])[0] == SPZ_MAGIC


def looks_like_splat(path: str | Path, sample: int = 2000) -> bool:
    """Content check for extensionless antimatter15 .splat blobs (no magic bytes): size is a
    multiple of 32 and the first records decode to sane positions, scales and unit quaternions."""
    path = Path(path)
    size = path.stat().st_size
    if size == 0 or size % 32:
        return False
    with open(path, "rb") as f:
        raw = f.read(min(size, sample * 32))
    rec = np.frombuffer(raw, dtype=_SPLAT_DTYPE)
    pos, scale = rec["pos"], rec["scale"]
    if not (np.isfinite(pos).all() and np.isfinite(scale).all()):
        return False
    if np.abs(pos).max() > 1e5 or (scale <= 0).any() or scale.max() > 1e4:
        return False
    q = (rec["rot"].astype(np.float32) - 128) / 128
    n = np.linalg.norm(q, axis=1)
    return bool(np.median(np.abs(n - 1)) < 0.05)


# -- 3DGS .ply ------------------------------------------------------------------------------

_PLY_TYPES = {"float": "<f4", "float32": "<f4", "double": "<f8", "uchar": "u1", "uint8": "u1", "char": "i1",
              "short": "<i2", "ushort": "<u2", "int": "<i4", "uint": "<u4", "int32": "<i4", "uint32": "<u4"}


def read_ply(path: str | Path) -> Splats:
    path = Path(path)
    h = ply_header(path)
    if h is None:
        raise SplatFormatError(f"{path.name}: not a PLY file")
    if "binary_little_endian" not in h:
        raise SplatFormatError(f"{path.name}: only binary little-endian 3DGS .ply is supported")
    count, fields, in_vertex = 0, [], False
    for line in h.splitlines():
        parts = line.split()
        if parts[:2] == ["element", "vertex"]:
            count, in_vertex = int(parts[2]), True
        elif parts and parts[0] == "element":
            if in_vertex:
                break
        elif in_vertex and parts[:1] == ["property"]:
            if parts[1] == "list":
                raise SplatFormatError(f"{path.name}: list properties in the vertex element aren't a 3DGS layout")
            fields.append((parts[2], _PLY_TYPES[parts[1]]))
    dtype = np.dtype(fields)
    offset = len(h.encode("latin-1")) + 1  # the newline after end_header
    data = np.fromfile(path, dtype=dtype, count=count, offset=offset)
    names = set(dtype.names or ())
    need = {"x", "y", "z", "f_dc_0", "f_dc_1", "f_dc_2", "opacity", "scale_0", "scale_1", "scale_2",
            "rot_0", "rot_1", "rot_2", "rot_3"}
    if not need <= names:
        raise SplatFormatError(f"{path.name}: missing 3DGS properties {sorted(need - names)}")
    col = lambda *ks: np.stack([data[k].astype(np.float32) for k in ks], axis=1)  # noqa: E731
    rest_keys = sorted((k for k in names if k.startswith("f_rest_")), key=lambda k: int(k.split("_")[-1]))
    rest = None
    if rest_keys:
        per = len(rest_keys) // 3  # channel-major: all R coeffs, then G, then B
        rest = col(*rest_keys).reshape(count, 3, per).transpose(0, 2, 1)
    return Splats(path.stem, col("x", "y", "z"), col("scale_0", "scale_1", "scale_2"),
                  col("rot_0", "rot_1", "rot_2", "rot_3"), data["opacity"].astype(np.float32),
                  col("f_dc_0", "f_dc_1", "f_dc_2"), rest)


def write_ply(s: Splats, path: Path) -> Path:
    s = s.baked()
    n = len(s)
    k = 0 if s.sh_rest is None else s.sh_rest.shape[1]
    names = ["x", "y", "z", "f_dc_0", "f_dc_1", "f_dc_2"]
    names += [f"f_rest_{i}" for i in range(3 * k)]
    names += ["opacity", "scale_0", "scale_1", "scale_2", "rot_0", "rot_1", "rot_2", "rot_3"]
    cols = [s.means, s.sh_dc]
    if k:
        cols.append(s.sh_rest.transpose(0, 2, 1).reshape(n, 3 * k))
    cols += [s.opacities[:, None], s.scales, s.rotations]
    arr = np.ascontiguousarray(np.concatenate(cols, axis=1), dtype="<f4")
    header = "ply\nformat binary_little_endian 1.0\n" + f"element vertex {n}\n"
    header += "".join(f"property float {nm}\n" for nm in names) + "end_header\n"
    with open(path, "wb") as f:
        f.write(header.encode("ascii"))
        f.write(arr.tobytes())
    return Path(path)


# -- antimatter15 .splat --------------------------------------------------------------------

_SPLAT_DTYPE = np.dtype([("pos", "<f4", 3), ("scale", "<f4", 3), ("rgba", "u1", 4), ("rot", "u1", 4)])


def read_splat(path: str | Path) -> Splats:
    path = Path(path)
    size = path.stat().st_size
    if size % 32:
        raise SplatFormatError(f"{path.name}: size {size} isn't a multiple of 32 bytes (antimatter15 .splat)")
    rec = np.fromfile(path, dtype=_SPLAT_DTYPE)
    rgba = rec["rgba"].astype(np.float32) / 255.0
    rot = (rec["rot"].astype(np.float32) - 128.0) / 128.0
    return Splats(path.stem, rec["pos"], np.log(np.maximum(rec["scale"], 1e-12)), rot, logit(rgba[:, 3]),
                  (rgba[:, :3] - 0.5) / SH_C0)


def write_splat(s: Splats, path: Path) -> Path:
    """Sorted by descending size·opacity like the reference converter (viewers stream it)."""
    s = s.baked()
    alpha = 1 / (1 + np.exp(-s.opacities.astype(np.float64)))
    order = np.argsort(-np.exp(s.scales.astype(np.float64).sum(1)) * alpha)
    rec = np.zeros(len(s), dtype=_SPLAT_DTYPE)
    rec["pos"] = s.means[order]
    rec["scale"] = np.exp(s.scales[order])
    rgb = np.clip(s.sh_dc[order] * SH_C0 + 0.5, 0, 1)
    rec["rgba"] = np.clip(np.concatenate([rgb, alpha[order, None]], 1) * 255 + 0.5, 0, 255).astype(np.uint8)
    q = s.rotations[order].astype(np.float64)
    q /= np.maximum(np.linalg.norm(q, axis=1, keepdims=True), 1e-12)
    rec["rot"] = np.clip(q * 128 + 128, 0, 255).astype(np.uint8)
    rec.tofile(path)
    return Path(path)


# -- Niantic .spz ---------------------------------------------------------------------------


def read_spz(path: str | Path) -> Splats:
    path = Path(path)
    data = path.read_bytes()
    if struct.unpack_from("<I", data, 0)[0] == SPZ_MAGIC:
        raw, version, n, sh_degree, frac_bits, flags = _spz_v4_columns(path, data)
        off = 0
    else:
        raw = gzip.decompress(data)
        magic, version, n, sh_degree, frac_bits, flags, _ = struct.unpack_from("<IIIBBBB", raw, 0)
        if magic != SPZ_MAGIC:
            raise SplatFormatError(f"{path.name}: not an .spz file")
        if version not in (2, 3):
            raise SplatFormatError(f"{path.name}: .spz version {version} isn't supported (2–4)")
        off = 16
    k = {0: 0, 1: 3, 2: 8, 3: 15}[sh_degree]

    def take(nbytes: int) -> np.ndarray:
        nonlocal off
        out = np.frombuffer(raw, dtype=np.uint8, count=nbytes, offset=off)
        off += nbytes
        return out

    p = take(n * 9).reshape(n, 3, 3).astype(np.int32)
    fixed = p[..., 0] | (p[..., 1] << 8) | (p[..., 2] << 16)
    fixed = np.where(fixed & 0x800000, fixed - (1 << 24), fixed)
    means = fixed.astype(np.float32) / (1 << frac_bits)
    alpha = take(n).astype(np.float32) / 255.0
    color = take(n * 3).reshape(n, 3).astype(np.float32)
    scales = take(n * 3).reshape(n, 3).astype(np.float32) / 16.0 - 10.0
    if version == 2:
        xyz = take(n * 3).reshape(n, 3).astype(np.float32) / 127.5 - 1.0
        w = np.sqrt(np.maximum(0.0, 1.0 - (xyz**2).sum(1)))
        rot = np.concatenate([w[:, None], xyz], 1)
    else:
        rot = _spz_unpack_smallest_three(take(n * 4).reshape(n, 4))
    rest = None
    if k:
        rest = (take(n * k * 3).reshape(n, k, 3).astype(np.float32) - 128.0) / 128.0
    sh_dc = (color / 255.0 - 0.5) / SPZ_COLOR_SCALE
    return Splats(path.stem, means, scales, rot, logit(alpha), sh_dc, rest, antialiased=bool(flags & 1))


def _spz_v4_columns(path: Path, data: bytes) -> tuple[bytes, int, int, int, int, int]:
    """Decode the v4 container into the v3 column layout (the per-attribute bytes are identical)."""
    import zstandard

    magic, version, n, sh_degree, frac_bits, flags, num_streams, toc = struct.unpack_from("<IIIBBBBI", data, 0)
    if version != 4:
        raise SplatFormatError(f"{path.name}: .spz version {version} isn't supported (2–4)")
    if toc < 32 or toc + num_streams * 16 > len(data):
        raise SplatFormatError(f"{path.name}: corrupt .spz table of contents")
    sizes = [struct.unpack_from("<QQ", data, toc + i * 16) for i in range(num_streams)]
    off = toc + num_streams * 16
    dctx = zstandard.ZstdDecompressor()
    columns = []
    for compressed, uncompressed in sizes:
        if off + compressed > len(data):
            raise SplatFormatError(f"{path.name}: .spz stream runs past end of file")
        columns.append(dctx.decompress(data[off:off + compressed], max_output_size=uncompressed))
        off += compressed
    return b"".join(columns), version, n, sh_degree, frac_bits, flags


def _spz_unpack_smallest_three(b: np.ndarray) -> np.ndarray:
    """v3 rotation: index of the largest |component| (2 bits) + three 9-bit magnitudes with signs."""
    comp = (b[:, 0].astype(np.uint32) | (b[:, 1].astype(np.uint32) << 8) | (b[:, 2].astype(np.uint32) << 16)
            | (b[:, 3].astype(np.uint32) << 24))
    largest = comp >> 30
    mask = (1 << 9) - 1
    q = np.zeros((len(b), 4), dtype=np.float64)  # x, y, z, w (spz order)
    total = np.zeros(len(b))
    for i in (3, 2, 1, 0):
        sel = largest != i
        mag = (comp & mask).astype(np.float64)
        neg = (comp >> 9) & 1
        val = math.sqrt(0.5) * mag / mask
        val = np.where(neg == 1, -val, val)
        q[sel, i] = val[sel]
        total += np.where(sel, val * val, 0)
        comp = np.where(sel, comp >> 10, comp)
    big = np.sqrt(np.maximum(0.0, 1.0 - total))
    q[np.arange(len(b)), largest] = big
    return np.stack([q[:, 3], q[:, 0], q[:, 1], q[:, 2]], 1)  # → w, x, y, z


def write_spz(s: Splats, path: Path, frac_bits: int = 12) -> Path:
    """Write .spz v2 (positions must fit 24-bit fixed point: |x| < 2048 at 12 fractional bits)."""
    s = s.baked()
    n = len(s)
    deg = s.sh_degree
    k = {0: 0, 1: 3, 2: 8, 3: 15}[deg]
    fixed = np.round(s.means.astype(np.float64) * (1 << frac_bits)).astype(np.int64)
    if np.abs(fixed).max(initial=0) >= (1 << 23):
        raise SplatFormatError("positions too large for .spz fixed point; lower frac_bits")
    fixed &= 0xFFFFFF
    pos = np.stack([fixed & 0xFF, (fixed >> 8) & 0xFF, (fixed >> 16) & 0xFF], -1).astype(np.uint8)
    alpha = np.clip(1 / (1 + np.exp(-s.opacities.astype(np.float64))) * 255 + 0.5, 0, 255).astype(np.uint8)
    color = np.clip((s.sh_dc * SPZ_COLOR_SCALE + 0.5) * 255 + 0.5, 0, 255).astype(np.uint8)
    scales = np.clip((s.scales + 10.0) * 16.0 + 0.5, 0, 255).astype(np.uint8)
    q = s.rotations.astype(np.float64)
    q /= np.maximum(np.linalg.norm(q, axis=1, keepdims=True), 1e-12)
    q *= np.where(q[:, :1] < 0, -1, 1)  # w ≥ 0 so it can be reconstructed
    rot = np.clip((q[:, 1:] + 1) * 127.5 + 0.5, 0, 255).astype(np.uint8)
    parts = [struct.pack("<IIIBBBB", SPZ_MAGIC, 2, n, deg, frac_bits, int(s.antialiased), 0),
             pos.tobytes(), alpha.tobytes(), color.tobytes(), scales.tobytes(), rot.tobytes()]
    if k:
        parts.append(np.clip(s.sh_rest * 128 + 128.5, 0, 255).astype(np.uint8).tobytes())
    Path(path).write_bytes(gzip.compress(b"".join(parts), compresslevel=6))
    return Path(path)


# -- dispatch -------------------------------------------------------------------------------

# Axes each container stores (the IR keeps file coordinates; build_stage normalises "-Y").
FILE_UP = {".ply": "-Y", ".splat": "-Y", ".spz": "Y"}


def kind_of(path: str | Path) -> str:
    """'ply' | 'splat' | 'spz' | 'ksplat' by extension, falling back to content."""
    path = Path(path)
    ext = path.suffix.lower()
    if ext in (".ply", ".splat", ".spz", ".ksplat"):
        return ext[1:]
    with open(path, "rb") as f:
        head = f.read(4)
    if head.startswith(b"ply"):
        return "ply"
    if head == struct.pack("<I", SPZ_MAGIC) or (head.startswith(b"\x1f\x8b") and is_spz(path)):  # v4 / v2–3
        return "spz"
    return "splat"


def read_any(path: str | Path) -> tuple[Splats, str]:
    """→ (splats, the file's up axis)."""
    path = Path(path)
    kind = kind_of(path)
    if kind == "ply":
        return read_ply(path), "-Y"
    if kind == "spz":
        return read_spz(path), "Y"
    if kind == "ksplat":
        raise SplatFormatError(
            f"{path.name}: .ksplat can only be rendered with engine=threejs; for other engines convert it "
            "to .ply or .spz first (e.g. with the GaussianSplats3D or spz tools)")
    return read_splat(path), "-Y"


def write_any(s: Splats, path: Path) -> Path:
    ext = Path(path).suffix.lower()
    return {".ply": write_ply, ".splat": write_splat, ".spz": write_spz}[ext](s, Path(path))


def load(path: Path) -> Stage:
    path = Path(path)
    splats, up = read_any(path)
    stage = Stage(splats=[splats], up_axis=up, source_format="splat", base_dir=str(path.parent))
    stage.meta["splat_kind"] = kind_of(path)
    return stage
