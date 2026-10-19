"""`.rir` bundle: a directory with stage.json + meshes/*.npz + textures/.

The on-disk cache format for converted scenes (e.g. a .blend extracted once,
rendered by any engine). Written by Blender's extract.py too, so keep the schema
plain: stage.json references mesh files and textures by relative path.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import shutil
from pathlib import Path

import numpy as np

from .stage import Camera, Light, Material, Mesh, Stage

RIR_VERSION = 1


def write_rir(stage: Stage, out_dir: str | Path) -> Path:
    out = Path(out_dir)
    (out / "meshes").mkdir(parents=True, exist_ok=True)
    (out / "textures").mkdir(exist_ok=True)

    copied: dict[str, str] = {}

    def tex(path: str | None) -> str | None:
        if not path:
            return None
        src = Path(path)
        if not src.is_absolute() and stage.base_dir:
            src = Path(stage.base_dir) / src
        if not src.exists():
            return None
        key = str(src.resolve())
        if key not in copied:
            digest = hashlib.sha1(key.encode()).hexdigest()[:10]
            dst = out / "textures" / f"{digest}_{src.name}"
            if src.resolve() != dst.resolve():
                shutil.copyfile(src, dst)
            copied[key] = f"textures/{dst.name}"
        return copied[key]

    materials = {}
    for name, m in stage.materials.items():
        d = dataclasses.asdict(m)
        for f in Material.TEX_FIELDS:
            d[f] = tex(d[f])
        materials[name] = d

    meshes = []
    for i, m in enumerate(stage.meshes):
        rel = f"meshes/{i:05d}.npz"
        arrays = {"points": m.points, "indices": m.indices}
        if m.normals is not None:
            arrays["normals"] = m.normals
        if m.uvs is not None:
            arrays["uvs"] = m.uvs
        if m.colors is not None:
            arrays["colors"] = np.asarray(m.colors, dtype=np.float32)
        np.savez_compressed(out / rel, **arrays)
        meshes.append({"name": m.name, "file": rel, "transform": m.transform.tolist(), "material": m.material})

    splats = []
    if stage.splats:
        (out / "splats").mkdir(exist_ok=True)
    for i, sp in enumerate(stage.splats):
        rel = f"splats/{i:05d}.npz"
        arrays = {"means": sp.means, "scales": sp.scales, "rotations": sp.rotations, "opacities": sp.opacities,
                  "sh_dc": sp.sh_dc}
        if sp.sh_rest is not None:
            arrays["sh_rest"] = sp.sh_rest
        np.savez_compressed(out / rel, **arrays)
        splats.append({"name": sp.name, "file": rel, "transform": sp.transform.tolist(), "antialiased": sp.antialiased})

    lights = []
    for light in stage.lights:
        d = dataclasses.asdict(light)
        d["transform"] = np.asarray(light.transform).tolist()
        d["texture"] = tex(light.texture)
        lights.append(d)

    cameras = []
    for c in stage.cameras:
        d = dataclasses.asdict(c)
        d["transform"] = np.asarray(c.transform).tolist()
        cameras.append(d)

    doc = {
        "rir": RIR_VERSION,
        "upAxis": stage.up_axis,
        "unitScale": stage.unit_scale,
        "sourceFormat": stage.source_format,
        "frameRange": list(stage.frame_range) if stage.frame_range else None,
        "fps": stage.fps,
        "meta": stage.meta,
        "materials": materials,
        "meshes": meshes,
        "splats": splats,
        "lights": lights,
        "cameras": cameras,
    }
    (out / "stage.json").write_text(json.dumps(doc, indent=1, default=_json_default))
    return out


def read_rir(path: str | Path) -> Stage:
    root = Path(path)
    doc = json.loads((root / "stage.json").read_text())
    if doc.get("rir", 1) > RIR_VERSION:
        raise ValueError(f"{root}: rir version {doc['rir']} is newer than this renda supports ({RIR_VERSION})")

    def abs_tex(p: str | None) -> str | None:
        return str(root / p) if p else None

    stage = Stage(
        up_axis=doc.get("upAxis", "Y"),
        unit_scale=doc.get("unitScale", 1.0),
        source_format=doc.get("sourceFormat"),
        base_dir=str(root),
        frame_range=tuple(doc["frameRange"]) if doc.get("frameRange") else None,
        fps=doc.get("fps"),
        meta=doc.get("meta") or {},
    )
    mat_fields = {f.name for f in dataclasses.fields(Material)}
    for name, d in doc.get("materials", {}).items():
        d = {k: v for k, v in d.items() if k in mat_fields}
        for f in Material.TEX_FIELDS:
            d[f] = abs_tex(d.get(f))
        for k in ("base_color", "emission"):
            if k in d:
                d[k] = tuple(d[k])
        stage.materials[name] = Material(**d)
    for md in doc.get("meshes", []):
        arr = np.load(root / md["file"])
        stage.meshes.append(Mesh(
            name=md["name"], points=arr["points"], indices=arr["indices"],
            normals=arr["normals"] if "normals" in arr else None,
            uvs=arr["uvs"] if "uvs" in arr else None,
            colors=arr["colors"] if "colors" in arr else None,
            transform=np.array(md["transform"]), material=md.get("material"),
        ))
    if doc.get("splats"):
        from .splats import Splats

        for sd in doc["splats"]:
            arr = np.load(root / sd["file"])
            stage.splats.append(Splats(
                sd["name"], arr["means"], arr["scales"], arr["rotations"], arr["opacities"], arr["sh_dc"],
                arr["sh_rest"] if "sh_rest" in arr else None, np.array(sd["transform"]), sd.get("antialiased", False),
            ))
    light_fields = {f.name for f in dataclasses.fields(Light)}
    for ld in doc.get("lights", []):
        ld = {k: v for k, v in ld.items() if k in light_fields}
        ld["transform"] = np.array(ld["transform"])
        ld["texture"] = abs_tex(ld.get("texture"))
        ld["color"] = tuple(ld.get("color", (1, 1, 1)))
        ld["size"] = tuple(ld.get("size", (1, 1)))
        stage.lights.append(Light(**ld))
    cam_fields = {f.name for f in dataclasses.fields(Camera)}
    for cd in doc.get("cameras", []):
        cd = {k: v for k, v in cd.items() if k in cam_fields}
        cd["transform"] = np.array(cd["transform"])
        stage.cameras.append(Camera(**cd))
    return stage


def _json_default(o):
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    raise TypeError(type(o))
