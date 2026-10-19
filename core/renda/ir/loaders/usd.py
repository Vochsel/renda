"""USD (.usd/.usda/.usdc/.usdz) → Stage via usd-core (pxr)."""

from __future__ import annotations

import math
import tempfile
import zipfile
from pathlib import Path

import numpy as np

from ..stage import Camera, Light, Material, Mesh, Stage


def available() -> bool:
    try:
        import pxr  # noqa: F401
    except ImportError:
        return False
    return True


def _mat(gf) -> np.ndarray:
    # Gf matrices are row-vector (v * M); we use column vectors (M @ v).
    return np.array(gf, dtype=np.float64).reshape(4, 4).T


def load(path: Path) -> Stage:
    try:
        from pxr import Usd, UsdGeom, UsdLux, UsdShade
    except ImportError as e:
        raise RuntimeError("USD support needs usd-core: uv tool install 'renda[usd]'") from e

    usd = Usd.Stage.Open(str(path))
    if usd is None:
        raise ValueError(f"could not open USD stage {path}")
    up = UsdGeom.GetStageUpAxis(usd)
    stage = Stage(up_axis="Z" if up == UsdGeom.Tokens.z else "Y", source_format="usd", base_dir=str(path.parent),
                  unit_scale=float(UsdGeom.GetStageMetersPerUnit(usd) or 1.0))
    warnings = stage.meta.setdefault("warnings", [])
    start, end = usd.GetStartTimeCode(), usd.GetEndTimeCode()
    if end > start:
        stage.frame_range = (int(start), int(end))
        stage.fps = usd.GetTimeCodesPerSecond()
    tc = Usd.TimeCode.Default()
    xcache = UsdGeom.XformCache(tc)
    mat_cache: dict[str, str] = {}
    usdz_dir = _extract_usdz(path) if path.suffix.lower() == ".usdz" else None

    for prim in usd.Traverse(Usd.TraverseInstanceProxies()):
        if not UsdGeom.Imageable(prim).ComputeVisibility(tc) == UsdGeom.Tokens.inherited:
            continue
        if prim.IsA(UsdGeom.Mesh):
            mesh = _mesh(prim, UsdGeom, tc)
            if mesh is None:
                continue
            mesh.transform = _mat(xcache.GetLocalToWorldTransform(prim))
            bound = UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()[0]
            if bound:
                key = str(bound.GetPath())
                if key not in mat_cache:
                    m = _material(bound, UsdShade, usdz_dir or path.parent)
                    stage.materials[m.name] = m
                    mat_cache[key] = m.name
                mesh.material = mat_cache[key]
            else:
                dc = UsdGeom.Mesh(prim).GetDisplayColorAttr().Get(tc)
                if dc is not None and len(dc) == 1:
                    name = f"displayColor_{len(stage.materials)}"
                    stage.materials[name] = Material(name, base_color=tuple(float(x) for x in dc[0]) + (1.0,))
                    mesh.material = name
            stage.meshes.append(mesh)
        elif prim.IsA(UsdGeom.PointInstancer):
            warnings.append(f"usd: PointInstancer {prim.GetPath()} not expanded yet")
        elif prim.IsA(UsdGeom.Camera):
            gcam = UsdGeom.Camera(prim).GetCamera(tc)
            world = _mat(xcache.GetLocalToWorldTransform(prim))
            if gcam.projection == gcam.Orthographic:
                stage.cameras.append(Camera(prim.GetName(), world, 40.0, ortho_height=gcam.verticalAperture / 10.0))
            else:
                stage.cameras.append(Camera(prim.GetName(), world, float(gcam.GetFieldOfView(gcam.FOVVertical)),
                                            near=gcam.clippingRange.min, far=gcam.clippingRange.max))
        elif prim.HasAPI(UsdLux.LightAPI) or prim.GetTypeName().endswith("Light"):
            light = _light(prim, UsdLux, tc, _mat(xcache.GetLocalToWorldTransform(prim)), usdz_dir or path.parent)
            if light:
                stage.lights.append(light)
    return stage


def _extract_usdz(path: Path) -> Path:
    out = Path(tempfile.mkdtemp(prefix="renda-usdz-"))
    with zipfile.ZipFile(path) as z:
        z.extractall(out)
    return out


def _mesh(prim, UsdGeom, tc) -> Mesh | None:
    gm = UsdGeom.Mesh(prim)
    pts = gm.GetPointsAttr().Get(tc)
    counts = gm.GetFaceVertexCountsAttr().Get(tc)
    idx = gm.GetFaceVertexIndicesAttr().Get(tc)
    if not pts or not counts or not idx:
        return None
    P = np.asarray(pts, dtype=np.float64)
    counts = np.asarray(counts, dtype=np.int64)
    idx = np.asarray(idx, dtype=np.int64)

    # fan-triangulate in corner space
    tris = []
    off = 0
    for n in counts:
        for i in range(1, n - 1):
            tris.append((off, off + i, off + i + 1))
        off += n
    corner_tris = np.asarray(tris, dtype=np.int64).reshape(-1, 3)
    if gm.GetOrientationAttr().Get() == UsdGeom.Tokens.leftHanded:
        corner_tris = corner_tris[:, [0, 2, 1]]

    pv_api = UsdGeom.PrimvarsAPI(prim)
    normals, n_interp = None, None
    npv = pv_api.GetPrimvar("normals")
    if npv and npv.HasValue():
        normals, n_interp = np.asarray(npv.ComputeFlattened(tc), dtype=np.float64), npv.GetInterpolation()
    elif gm.GetNormalsAttr().HasValue():
        normals, n_interp = np.asarray(gm.GetNormalsAttr().Get(tc), dtype=np.float64), gm.GetNormalsInterpolation()
    uvs, uv_interp = None, None
    for name in ("st", "UVMap", "uv", "st0"):
        upv = pv_api.GetPrimvar(name)
        if upv and upv.HasValue():
            uvs, uv_interp = np.asarray(upv.ComputeFlattened(tc), dtype=np.float64), upv.GetInterpolation()
            break

    face_varying = any(i == UsdGeom.Tokens.faceVarying for i in (n_interp, uv_interp) if i is not None)
    if face_varying:
        # one vertex per corner
        points = P[idx]
        def per_corner(arr, interp):
            if arr is None:
                return None
            if interp == UsdGeom.Tokens.faceVarying and len(arr) == len(idx):
                return arr
            if interp in (UsdGeom.Tokens.vertex, UsdGeom.Tokens.varying) and len(arr) == len(P):
                return arr[idx]
            return None
        return Mesh(prim.GetName(), points, corner_tris, per_corner(normals, n_interp), per_corner(uvs, uv_interp))
    tri = idx[corner_tris]
    ok = lambda a: a is not None and len(a) == len(P)  # noqa: E731
    return Mesh(prim.GetName(), P, tri, normals if ok(normals) else None, uvs if ok(uvs) else None)


def _material(bound, UsdShade, base: Path) -> Material:
    m = Material(name=bound.GetPrim().GetName())
    surf = bound.ComputeSurfaceSource()[0]
    if not surf or surf.GetIdAttr().Get() != "UsdPreviewSurface":
        return m

    def inp(name):
        i = surf.GetInput(name)
        if not i:
            return None, None
        if i.HasConnectedSource():
            src = i.GetConnectedSource()[0]
            tex = UsdShade.Shader(src.GetPrim())
            f = tex.GetInput("file")
            if f and f.Get() is not None:
                asset = f.Get()
                p = asset.resolvedPath or asset.path
                if p and not Path(p).is_absolute():
                    p = str(base / p)
                return None, p
            return None, None
        return i.Get(), None

    v, t = inp("diffuseColor")
    if v is not None:
        m.base_color = tuple(float(x) for x in v) + (1.0,)
    m.base_color_tex = t
    for field, key in (("metallic", "metallic"), ("roughness", "roughness"), ("opacity", "opacity"), ("ior", "ior")):
        v, t = inp(key)
        if v is not None:
            setattr(m, field, float(v))
        if t and field in ("metallic", "roughness", "opacity"):
            setattr(m, f"{field}_tex", t)
    v, t = inp("emissiveColor")
    if v is not None and max(v) > 0:
        m.emission = tuple(float(x) for x in v)
        m.emission_strength = 1.0
    m.emission_tex = t
    _, m.normal_tex = inp("normal")
    m.base_color = m.base_color[:3] + (m.opacity,)
    return m


def _light(prim, UsdLux, tc, world: np.ndarray, base: Path) -> Light | None:
    api = UsdLux.LightAPI(prim)
    inten = float(api.GetIntensityAttr().Get(tc) or 1.0) * 2 ** float(api.GetExposureAttr().Get(tc) or 0.0)
    color = tuple(float(x) for x in (api.GetColorAttr().Get(tc) or (1, 1, 1)))
    t = prim.GetTypeName()
    name = prim.GetName()
    if t == "DistantLight":
        return Light(name, "sun", color, inten / 1000.0 if inten > 100 else inten, world)
    if t == "SphereLight":
        r = float(UsdLux.SphereLight(prim).GetRadiusAttr().Get(tc) or 0.05)
        # UsdLux intensity is radiance (nits-ish): watts ≈ L · area · π
        return Light(name, "point", color, inten * 4 * math.pi * r * r * math.pi, world, radius=r)
    if t == "RectLight":
        rl = UsdLux.RectLight(prim)
        w, h = float(rl.GetWidthAttr().Get(tc) or 1), float(rl.GetHeightAttr().Get(tc) or 1)
        return Light(name, "area", color, inten * w * h * math.pi, world, size=(w, h))
    if t == "DiskLight":
        r = float(UsdLux.DiskLight(prim).GetRadiusAttr().Get(tc) or 0.5)
        return Light(name, "area", color, inten * math.pi * r * r * math.pi, world, size=(2 * r, 2 * r))
    if t == "DomeLight":
        tex = UsdLux.DomeLight(prim).GetTextureFileAttr().Get(tc)
        path = None
        if tex is not None:
            path = tex.resolvedPath or tex.path
            if path and not Path(path).is_absolute():
                path = str(base / path)
        return Light(name, "dome", color, inten, world, texture=path or None)
    return None
