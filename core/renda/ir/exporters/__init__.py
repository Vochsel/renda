"""Stage → glTF/GLB, OBJ, USD, .rir."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from ..rir import write_rir
from ..stage import Material, Stage


def _texture_image(stage: Stage, path: str | None):
    if not path:
        return None
    from PIL import Image

    p = Path(path)
    if not p.is_absolute() and stage.base_dir:
        p = Path(stage.base_dir) / p
    try:
        return Image.open(p)
    except Exception:
        return None


def _trimesh_scene(stage: Stage, pbr: bool = True):
    import trimesh

    scene = trimesh.Scene()
    mats: dict[str, object] = {}
    for i, mesh in enumerate(stage.meshes):
        mat: Material = stage.material_for(mesh)
        if mat.name not in mats:
            if pbr:
                mats[mat.name] = trimesh.visual.material.PBRMaterial(
                    name=mat.name,
                    baseColorFactor=[float(c) for c in mat.base_color],
                    metallicFactor=float(mat.metallic),
                    roughnessFactor=float(mat.roughness),
                    emissiveFactor=[float(c) * min(mat.emission_strength, 1.0) for c in mat.emission],
                    baseColorTexture=_texture_image(stage, mat.base_color_tex),
                    metallicRoughnessTexture=_texture_image(stage, mat.metallic_roughness_tex),
                    normalTexture=_texture_image(stage, mat.normal_tex),
                    emissiveTexture=_texture_image(stage, mat.emission_tex),
                    alphaMode="BLEND" if mat.opacity < 1 else "OPAQUE",
                    doubleSided=mat.double_sided,
                )
            else:
                c = (np.clip(np.asarray(mat.base_color[:3]) ** (1 / 2.2), 0, 1) * 255).astype(np.uint8)
                mats[mat.name] = trimesh.visual.material.SimpleMaterial(
                    name=mat.name, diffuse=list(c) + [255], image=_texture_image(stage, mat.base_color_tex))
        uv = mesh.uvs if mesh.uvs is not None else np.zeros((len(mesh.points), 2), dtype=np.float32)
        visual = trimesh.visual.TextureVisuals(uv=uv, material=mats[mat.name])
        tm = trimesh.Trimesh(vertices=mesh.points, faces=mesh.indices, vertex_normals=mesh.normals,
                             visual=visual, process=False)
        if mesh.colors is not None and len(mesh.colors) == len(mesh.points):
            # vertex-coloured meshes (scans, PLY): COLOR_0 (linear floats, per the glTF spec) next to the
            # material; viewers and Blender's importer multiply it into the base colour. trimesh writes
            # custom attributes as "_COLOR_0"; _fix_color_attribute renames it after export.
            rgba = np.ones((len(mesh.colors), 4), dtype=np.float32)
            rgba[:, : mesh.colors.shape[1]] = np.clip(mesh.colors[:, :4], 0, 1)
            tm.vertex_attributes["COLOR_0"] = rgba
        scene.add_geometry(tm, node_name=f"{mesh.name}_{i}", geom_name=f"{mesh.name}_{i}", transform=mesh.transform)
    return scene


def to_gltf(stage: Stage, out: str | Path) -> Path:
    """Write .glb (or .gltf). Lights/cameras are not written (engines get them from the job)."""
    out = Path(out)
    scene = _trimesh_scene(stage, pbr=True)
    if out.suffix.lower() == ".gltf":
        scene.export(str(out), file_type="gltf")
        data = json.loads(out.read_text())
        if _fix_color_attribute(data):
            out.write_text(json.dumps(data))
    else:
        out.write_bytes(_patch_glb(scene.export(file_type="glb")))
    return out


def _fix_color_attribute(gltf: dict) -> bool:
    """Rename trimesh's "_COLOR_0" custom attribute to the standard COLOR_0. Returns True if changed."""
    changed = False
    for m in gltf.get("meshes", []):
        for prim in m.get("primitives", []):
            attrs = prim.get("attributes", {})
            if "_COLOR_0" in attrs and "COLOR_0" not in attrs:
                attrs["COLOR_0"] = attrs.pop("_COLOR_0")
                changed = True
    return changed


def _patch_glb(glb: bytes) -> bytes:
    """Apply _fix_color_attribute to a GLB's JSON chunk (re-padded to 4 bytes)."""
    import struct

    json_len = struct.unpack_from("<I", glb, 12)[0]
    data = json.loads(glb[20:20 + json_len])
    if not _fix_color_attribute(data):
        return glb
    js = json.dumps(data, separators=(",", ":")).encode()
    js += b" " * (-len(js) % 4)
    rest = glb[20 + json_len:]
    body = struct.pack("<I", len(js)) + b"JSON" + js + rest
    return glb[:8] + struct.pack("<I", 12 + len(body)) + body


def to_obj(stage: Stage, out: str | Path) -> Path:
    out = Path(out)
    scene = _trimesh_scene(stage, pbr=False)
    scene.export(str(out), file_type="obj")
    return out


def to_usd(stage: Stage, out: str | Path) -> Path:
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade, Vt

    out = Path(out)
    usd = Usd.Stage.CreateNew(str(out))
    UsdGeom.SetStageUpAxis(usd, UsdGeom.Tokens.z if stage.up_axis == "Z" else UsdGeom.Tokens.y)
    UsdGeom.SetStageMetersPerUnit(usd, 1.0)
    root = UsdGeom.Xform.Define(usd, "/World")
    usd.SetDefaultPrim(root.GetPrim())
    mats = {}
    for name, m in stage.materials.items():
        safe = "".join(ch if ch.isalnum() else "_" for ch in name) or "mat"
        if safe[0].isdigit():
            safe = "m_" + safe
        mp = f"/World/Looks/{safe}_{len(mats)}"
        mat = UsdShade.Material.Define(usd, mp)
        sh = UsdShade.Shader.Define(usd, mp + "/Surface")
        sh.CreateIdAttr("UsdPreviewSurface")
        sh.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*m.base_color[:3]))
        sh.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(float(m.metallic))
        sh.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(float(m.roughness))
        sh.CreateInput("opacity", Sdf.ValueTypeNames.Float).Set(float(m.opacity))
        sh.CreateInput("ior", Sdf.ValueTypeNames.Float).Set(float(m.ior))
        if m.emission_strength > 0:
            sh.CreateInput("emissiveColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*[c * m.emission_strength for c in m.emission]))
        mat.CreateSurfaceOutput().ConnectToSource(sh.ConnectableAPI(), "surface")
        mats[name] = mat
    for i, mesh in enumerate(stage.meshes):
        safe = "".join(ch if ch.isalnum() else "_" for ch in mesh.name) or "mesh"
        if safe[0].isdigit():
            safe = "m_" + safe
        x = UsdGeom.Xform.Define(usd, f"/World/{safe}_{i}")
        x.AddTransformOp().Set(Gf.Matrix4d(mesh.transform.T.tolist()))
        g = UsdGeom.Mesh.Define(usd, f"/World/{safe}_{i}/mesh")
        g.CreatePointsAttr(Vt.Vec3fArray.FromNumpy(mesh.points.astype(np.float32)))
        g.CreateFaceVertexCountsAttr(Vt.IntArray([3] * len(mesh.indices)))
        g.CreateFaceVertexIndicesAttr(Vt.IntArray(mesh.indices.reshape(-1).tolist()))
        g.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
        if mesh.normals is not None:
            g.CreateNormalsAttr(Vt.Vec3fArray.FromNumpy(mesh.normals.astype(np.float32)))
            g.SetNormalsInterpolation(UsdGeom.Tokens.vertex)
        if mesh.uvs is not None:
            pv = UsdGeom.PrimvarsAPI(g).CreatePrimvar("st", Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.vertex)
            pv.Set(Vt.Vec2fArray.FromNumpy(mesh.uvs.astype(np.float32)))
        if mesh.material in mats:
            UsdShade.MaterialBindingAPI.Apply(g.GetPrim()).Bind(mats[mesh.material])
    usd.GetRootLayer().Save()
    return out


def export(stage: Stage, out: str | Path) -> Path:
    out = Path(out)
    ext = out.suffix.lower()
    if ext in (".glb", ".gltf"):
        return to_gltf(stage, out)
    if ext == ".obj":
        return to_obj(stage, out)
    if ext in (".usd", ".usda", ".usdc"):
        return to_usd(stage, out)
    if ext == ".rir":
        return write_rir(stage, out)
    raise ValueError(f"can't export to {ext}; supported: .rir .glb .gltf .obj .usd .usda .usdc")
