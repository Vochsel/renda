"""three.js JSON (Object/Scene format 4.x, as written by `object.toJSON()`) → Stage.

Pure Python — no Node needed. Covers what real-world exports contain:
BufferGeometry (indexed or not, groups for multi-material, typed or base64
arrays), the common primitive geometries, InstancedMesh, MeshStandard/Physical/
Basic/Lambert/Phong/Toon materials, textures from data URIs/URLs/DataTextures,
Group/Object3D hierarchy, Perspective/Orthographic cameras and
Directional/Point/Spot/Ambient/Hemisphere/RectArea lights.
"""

from __future__ import annotations

import base64
import json
import math
import tempfile
import urllib.parse
from pathlib import Path
from typing import Any

import numpy as np

from ..stage import Camera, Light, Material, Mesh, Stage, identity, look_at

DTYPES = {
    "Float32Array": np.float32, "Float64Array": np.float64,
    "Uint8Array": np.uint8, "Uint8ClampedArray": np.uint8, "Int8Array": np.int8,
    "Uint16Array": np.uint16, "Int16Array": np.int16,
    "Uint32Array": np.uint32, "Int32Array": np.int32,
}

CANDELA_TO_WATT = 4 * math.pi / 683.0  # same convention as Blender's glTF importer


def load(path: Path) -> Stage:
    doc = json.loads(Path(path).read_text())
    return ThreeLoader(doc, Path(path).parent).run()


def load_dict(doc: dict, base_dir: Path | None = None) -> Stage:
    return ThreeLoader(doc, base_dir or Path.cwd()).run()


def srgb_hex_to_linear(value: int | None, default=(1.0, 1.0, 1.0)) -> tuple[float, float, float]:
    if value is None:
        return default
    v = int(value)
    rgb = ((v >> 16) & 255, (v >> 8) & 255, v & 255)
    return tuple(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in (x / 255.0 for x in rgb))


def _matrix(obj: dict) -> np.ndarray:
    m = obj.get("matrix")
    if m and len(m) == 16:
        return np.array(m, dtype=np.float64).reshape(4, 4).T  # three.js stores column-major
    return identity()


def _torus_knot(g: dict) -> tuple:
    """Port of three.js TorusKnotGeometry (same vertex order, normals and UVs)."""
    radius, tube = g.get("radius", 1.0), g.get("tube", 0.4)
    ts, rs = int(g.get("tubularSegments", 64)), int(g.get("radialSegments", 8))
    p, q = g.get("p", 2), g.get("q", 3)

    def curve(u: float) -> np.ndarray:
        qr = q / p * u
        cs = math.cos(qr)
        return np.array([radius * (2 + cs) * 0.5 * math.cos(u), radius * (2 + cs) * 0.5 * math.sin(u),
                         radius * math.sin(qr) * 0.5])

    pts, nrm, uvs = [], [], []
    for i in range(ts + 1):
        u = i / ts * p * math.pi * 2
        p1, p2 = curve(u), curve(u + 0.01)
        T, N = p2 - p1, p2 + p1
        B = np.cross(T, N)
        N = np.cross(B, T)
        B /= np.linalg.norm(B) or 1
        N /= np.linalg.norm(N) or 1
        for j in range(rs + 1):
            v = j / rs * math.pi * 2
            cx, cy = -tube * math.cos(v), tube * math.sin(v)
            vert = p1 + cx * N + cy * B
            pts.append(vert)
            n = vert - p1
            nrm.append(n / (np.linalg.norm(n) or 1))
            uvs.append((i / ts, j / rs))
    idx = []
    for j in range(1, ts + 1):
        for i in range(1, rs + 1):
            a = (rs + 1) * (j - 1) + (i - 1)
            b = (rs + 1) * j + (i - 1)
            c = (rs + 1) * j + i
            d = (rs + 1) * (j - 1) + i
            idx += [(a, b, d), (b, c, d)]
    return np.array(pts), np.array(idx), np.array(nrm), np.array(uvs), None, []


class ThreeLoader:
    def __init__(self, doc: dict, base_dir: Path):
        meta = doc.get("metadata", {})
        if meta.get("type") == "BufferGeometry":  # bare geometry export
            doc = {"geometries": [{"uuid": "g", "type": "BufferGeometry", "data": doc.get("data", doc)}],
                   "object": {"type": "Mesh", "uuid": "m", "geometry": "g"}}
        self.doc = doc
        self.base_dir = base_dir
        self.stage = Stage(up_axis="Y", source_format="threejs", base_dir=str(base_dir))
        self.warnings: list[str] = self.stage.meta.setdefault("warnings", [])
        self.geometries = {g["uuid"]: g for g in doc.get("geometries", [])}
        self.materials_json = {m["uuid"]: m for m in doc.get("materials", [])}
        self.textures = {t["uuid"]: t for t in doc.get("textures", [])}
        self.images = {i["uuid"]: i for i in doc.get("images", [])}
        self.shapes = {s["uuid"]: s for s in doc.get("shapes", [])}
        self._geom_cache: dict[str, tuple | None] = {}
        self._mat_cache: dict[str, str] = {}
        self._img_cache: dict[str, str | None] = {}
        self._tex_dir: Path | None = None
        self._world: dict[str, np.ndarray] = {}
        self._pending_targets: list[tuple[Light, Any, np.ndarray]] = []

    # -- entry ---------------------------------------------------------------
    def run(self) -> Stage:
        root = self.doc.get("object")
        if root is None:
            raise ValueError("three.js JSON has no 'object' (expected output of Object3D.toJSON())")
        self._index_world(root, identity())
        self._walk(root, identity())
        for light, target, origin in self._pending_targets:
            pos = self._world.get(target) if isinstance(target, str) else None
            tpos = pos[:3, 3] if pos is not None else np.zeros(3)
            if np.linalg.norm(tpos - origin) < 1e-9:
                tpos = origin + np.array([0, -1.0, 0])
            light.transform = look_at(origin, tpos, [0, 1, 0] if abs((tpos - origin)[1]) < 0.99 * np.linalg.norm(tpos - origin) else [0, 0, 1])
        bg = root.get("background")
        if isinstance(bg, int):
            self.stage.meta["background"] = list(srgb_hex_to_linear(bg))
        return self.stage

    def _index_world(self, obj: dict, parent: np.ndarray) -> None:
        w = parent @ _matrix(obj)
        if "uuid" in obj:
            self._world[obj["uuid"]] = w
        for ch in obj.get("children", []):
            self._index_world(ch, w)

    def _walk(self, obj: dict, parent: np.ndarray) -> None:
        world = parent @ _matrix(obj)
        if obj.get("visible", True) is False:
            return
        t = obj.get("type", "Object3D")
        name = obj.get("name") or f"{t}_{obj.get('uuid', '')[:8]}"
        if t in ("Mesh", "SkinnedMesh", "InstancedMesh", "BatchedMesh"):
            self._mesh(obj, world, name)
        elif t in ("Points", "Line", "LineSegments", "LineLoop", "Sprite"):
            self.warnings.append(f"threejs: {t} {name!r} skipped (only meshes render)")
        elif t in ("PerspectiveCamera", "OrthographicCamera"):
            self._camera(obj, world, name)
        elif t.endswith("Light"):
            self._light(obj, world, name)
        for ch in obj.get("children", []):
            self._walk(ch, world)

    # -- meshes --------------------------------------------------------------
    def _mesh(self, obj: dict, world: np.ndarray, name: str) -> None:
        if obj.get("type") == "BatchedMesh":
            self.warnings.append(f"threejs: BatchedMesh {name!r} not supported yet, skipped")
            return
        geo = self._geometry(obj.get("geometry"))
        if geo is None:
            return
        points, indices, normals, uvs, colors, groups = geo
        mats = obj.get("material")
        mat_list = mats if isinstance(mats, list) else [mats]
        mat_names = [self._material(m) for m in mat_list]

        instances = [identity()]
        if obj.get("type") == "InstancedMesh":
            arr = obj.get("instanceMatrix", {}).get("array", [])
            count = int(obj.get("count", len(arr) // 16))
            a = np.asarray(arr, dtype=np.float64)[: count * 16].reshape(-1, 4, 4)
            instances = [m.T for m in a]

        parts: list[tuple[np.ndarray, str | None]]
        if groups and len(mat_names) > 1:
            parts = []
            for g in groups:
                start, count = int(g["start"]) // 3, int(g["count"]) // 3
                mi = int(g.get("materialIndex", 0))
                parts.append((indices[start : start + count], mat_names[mi] if mi < len(mat_names) else mat_names[0]))
        else:
            parts = [(indices, mat_names[0] if mat_names else None)]

        for ii, inst in enumerate(instances):
            for pi, (idx, mat) in enumerate(parts):
                if len(idx) == 0:
                    continue
                suffix = (f".{ii}" if len(instances) > 1 else "") + (f".g{pi}" if len(parts) > 1 else "")
                self.stage.meshes.append(Mesh(
                    name=name + suffix, points=points, indices=idx, normals=normals, uvs=uvs, colors=colors,
                    transform=world @ inst, material=mat,
                ))

    def _attr(self, a: dict | None) -> np.ndarray | None:
        if a is None:
            return None
        arr = a.get("array")
        dtype = DTYPES.get(a.get("type", "Float32Array"), np.float32)
        if isinstance(arr, str):  # base64 payload (some exporters)
            data = np.frombuffer(base64.b64decode(arr), dtype=dtype)
        else:
            data = np.asarray(arr if arr is not None else [], dtype=dtype)
        item = int(a.get("itemSize", 1))
        out = data.astype(np.float64)
        if a.get("normalized") and np.issubdtype(dtype, np.integer):
            out = out / np.iinfo(dtype).max
        return out.reshape(-1, item) if item > 1 else out

    def _geometry(self, uuid: str | None) -> tuple | None:
        if uuid is None:
            return None
        if uuid in self._geom_cache:
            return self._geom_cache[uuid]
        g = self.geometries.get(uuid)
        result = None
        if g is None:
            self.warnings.append(f"threejs: geometry {uuid} missing")
        elif g.get("type") in ("BufferGeometry", "Geometry", "InstancedBufferGeometry"):
            result = self._buffer_geometry(g)
        else:
            result = self._primitive(g)
        self._geom_cache[uuid] = result
        return result

    def _buffer_geometry(self, g: dict) -> tuple | None:
        data = g.get("data", {})
        attrs = data.get("attributes", {})
        # InterleavedBufferAttribute.toJSON stores buffers on the geometry's data object:
        # interleavedBuffers[uuid] = {buffer, type, stride}; arrayBuffers[uuid] = uint32 words
        ib_store = data.get("interleavedBuffers", {})
        ab_store = data.get("arrayBuffers", {})

        def attr(name: str) -> np.ndarray | None:
            a = attrs.get(name)
            if a and a.get("isInterleavedBufferAttribute"):
                buf = ib_store.get(a.get("data"))
                words = ab_store.get(buf.get("buffer")) if buf else None
                if words is None:
                    self.warnings.append(f"threejs: interleaved attribute {name} without buffer, skipped")
                    return None
                raw = np.asarray(words, dtype=np.uint32).tobytes()
                arr = np.frombuffer(raw, dtype=DTYPES.get(buf.get("type", "Float32Array"), np.float32)).astype(np.float64)
                stride = int(buf["stride"])
                item, off = int(a["itemSize"]), int(a.get("offset", 0))
                return arr.reshape(-1, stride)[:, off : off + item]
            return self._attr(a)

        pos = attr("position")
        if pos is None or len(pos) == 0:
            self.warnings.append(f"threejs: geometry {g.get('uuid')} has no positions, skipped")
            return None
        normals = attr("normal")
        uvs = attr("uv")
        colors = attr("color")
        idx_a = data.get("index")
        if idx_a:
            indices = self._attr(idx_a).astype(np.int64).reshape(-1)
        else:
            indices = np.arange(len(pos), dtype=np.int64)
        indices = indices[: len(indices) // 3 * 3].reshape(-1, 3)
        groups = data.get("groups") or []  # start/count index into the (possibly implicit) index buffer
        return pos, indices, normals, uvs, colors, groups

    def _primitive(self, g: dict) -> tuple | None:
        import trimesh

        t = g.get("type", "")
        try:
            if t == "BoxGeometry":
                m = trimesh.creation.box(extents=(g.get("width", 1), g.get("height", 1), g.get("depth", 1)))
            elif t == "SphereGeometry":
                m = trimesh.creation.uv_sphere(radius=g.get("radius", 1), count=(max(g.get("heightSegments", 16), 4), max(g.get("widthSegments", 32), 4)))
            elif t == "IcosahedronGeometry":
                m = trimesh.creation.icosphere(subdivisions=min(int(g.get("detail", 2)), 5), radius=g.get("radius", 1))
            elif t in ("CylinderGeometry", "ConeGeometry"):
                rt = 0.0 if t == "ConeGeometry" else g.get("radiusTop", 1)
                rb = g.get("radius", 1) if t == "ConeGeometry" else g.get("radiusBottom", 1)
                h = g.get("height", 1)
                if abs(rt - rb) < 1e-9:
                    m = trimesh.creation.cylinder(radius=rb, height=h, sections=g.get("radialSegments", 32))
                else:
                    m = trimesh.creation.cone(radius=rb, height=h, sections=g.get("radialSegments", 32))
                    m.apply_translation((0, 0, -h / 2))
                m.apply_transform(trimesh.transformations.rotation_matrix(-math.pi / 2, (1, 0, 0)))  # Z-axis → Y-axis
            elif t == "TorusKnotGeometry":
                return _torus_knot(g)
            elif t == "TorusGeometry":
                m = trimesh.creation.torus(major_radius=g.get("radius", 1), minor_radius=g.get("tube", 0.4))
            elif t == "PlaneGeometry":
                w, h = g.get("width", 1) / 2, g.get("height", 1) / 2
                pts = np.array([[-w, -h, 0], [w, -h, 0], [w, h, 0], [-w, h, 0]], dtype=np.float64)
                uv = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], dtype=np.float64)
                nrm = np.tile([0, 0, 1.0], (4, 1))
                return pts, np.array([[0, 1, 2], [0, 2, 3]]), nrm, uv, None, []
            elif t == "CircleGeometry":
                n = int(g.get("segments", 32))
                r = g.get("radius", 1)
                ang = np.linspace(0, 2 * math.pi, n, endpoint=False)
                pts = np.vstack([[0, 0, 0], np.c_[np.cos(ang) * r, np.sin(ang) * r, np.zeros(n)]])
                idx = np.array([[0, i + 1, (i + 1) % n + 1] for i in range(n)])
                return pts, idx, np.tile([0, 0, 1.0], (n + 1, 1)), None, None, []
            else:
                self.warnings.append(f"threejs: {t} not supported; export with geometry.toNonIndexed()/BufferGeometry or convert first")
                return None
        except Exception as e:  # pragma: no cover - trimesh API drift
            self.warnings.append(f"threejs: failed to build {t}: {e}")
            return None
        return (np.asarray(m.vertices), np.asarray(m.faces), np.asarray(m.vertex_normals), None, None, [])

    # -- materials -----------------------------------------------------------
    def _material(self, uuid: str | None) -> str | None:
        if uuid is None:
            return None
        if uuid in self._mat_cache:
            return self._mat_cache[uuid]
        mj = self.materials_json.get(uuid)
        if mj is None:
            return None
        t = mj.get("type", "MeshStandardMaterial")
        name = mj.get("name") or f"{t}_{uuid[:8]}"
        base = name
        i = 1
        while name in self.stage.materials:
            name = f"{base}.{i:03d}"
            i += 1
        rgb = srgb_hex_to_linear(mj.get("color"), (1.0, 1.0, 1.0))
        opacity = float(mj.get("opacity", 1.0)) if mj.get("transparent") else 1.0
        m = Material(name=name, base_color=rgb + (opacity,), opacity=opacity)
        if t in ("MeshStandardMaterial", "MeshPhysicalMaterial"):
            m.roughness = float(mj.get("roughness", 1.0))
            m.metallic = float(mj.get("metalness", 0.0))
        elif t == "MeshPhongMaterial":
            shin = float(mj.get("shininess", 30))
            m.roughness = float(np.clip(math.sqrt(2 / (shin + 2)), 0.02, 1.0))
        elif t in ("MeshLambertMaterial", "MeshToonMaterial", "MeshMatcapMaterial"):
            m.roughness = 1.0
        elif t == "MeshBasicMaterial":
            m.unlit = True
        elif t in ("MeshNormalMaterial", "MeshDepthMaterial", "ShaderMaterial", "RawShaderMaterial"):
            self.warnings.append(f"threejs: {t} {name!r} approximated as grey standard material")
        if t == "MeshPhysicalMaterial":
            m.transmission = float(mj.get("transmission", 0.0))
            m.ior = float(mj.get("ior", 1.5))
        em = mj.get("emissive")
        if em:
            m.emission = srgb_hex_to_linear(em, (0, 0, 0))
            m.emission_strength = float(mj.get("emissiveIntensity", 1.0))
        m.base_color_tex = self._texture(mj.get("map"))
        m.normal_tex = self._texture(mj.get("normalMap"))
        m.roughness_tex = self._texture(mj.get("roughnessMap"))
        m.metallic_tex = self._texture(mj.get("metalnessMap"))
        m.emission_tex = self._texture(mj.get("emissiveMap"))
        m.opacity_tex = self._texture(mj.get("alphaMap"))
        self.stage.materials[name] = m
        self._mat_cache[uuid] = name
        return name

    def _texture(self, uuid: str | None) -> str | None:
        if not uuid:
            return None
        tex = self.textures.get(uuid)
        if not tex:
            return None
        return self._image(tex.get("image"))

    def _image(self, uuid: str | None) -> str | None:
        if not uuid:
            return None
        if uuid in self._img_cache:
            return self._img_cache[uuid]
        img = self.images.get(uuid)
        out = None
        if img is not None:
            url = img.get("url")
            if isinstance(url, list):  # cube textures: take the first face
                url = url[0] if url else None
            try:
                out = self._materialize(uuid, url)
            except Exception as e:
                self.warnings.append(f"threejs: image {uuid} unreadable: {e}")
        self._img_cache[uuid] = out
        return out

    def _materialize(self, uuid: str, url: Any) -> str | None:
        if self._tex_dir is None:
            self._tex_dir = Path(tempfile.mkdtemp(prefix="renda-three-"))
        if isinstance(url, dict) and "data" in url:  # DataTexture
            from PIL import Image

            w, h = int(url["width"]), int(url["height"])
            arr = np.asarray(url["data"], dtype=np.float64)
            ch = len(arr) // (w * h)
            if arr.max() <= 1.0:
                arr = arr * 255
            arr = arr.astype(np.uint8).reshape(h, w, ch)
            p = self._tex_dir / f"{uuid}.png"
            Image.fromarray(arr.squeeze() if ch == 1 else arr).save(p)
            return str(p)
        if not isinstance(url, str):
            return None
        if url.startswith("data:"):
            header, payload = url.split(",", 1)
            ext = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}.get(header[5:].split(";")[0], ".bin")
            raw = base64.b64decode(payload) if ";base64" in header else urllib.parse.unquote_to_bytes(payload)
            p = self._tex_dir / f"{uuid}{ext}"
            p.write_bytes(raw)
            return str(p)
        if url.startswith(("http://", "https://")):
            import httpx

            r = httpx.get(url, timeout=30, follow_redirects=True)
            r.raise_for_status()
            p = self._tex_dir / f"{uuid}_{Path(urllib.parse.urlparse(url).path).name or 'tex'}"
            p.write_bytes(r.content)
            return str(p)
        local = (self.base_dir / url)
        return str(local) if local.exists() else None

    # -- cameras / lights ----------------------------------------------------
    def _camera(self, obj: dict, world: np.ndarray, name: str) -> None:
        zoom = float(obj.get("zoom", 1.0)) or 1.0
        if obj["type"] == "OrthographicCamera":
            height = (float(obj.get("top", 1)) - float(obj.get("bottom", -1))) / zoom
            self.stage.cameras.append(Camera(name, world, 40.0, ortho_height=height,
                                             near=obj.get("near", 0.1), far=obj.get("far", 2000)))
        else:
            fov = float(obj.get("fov", 50))
            if zoom != 1.0:
                fov = math.degrees(2 * math.atan(math.tan(math.radians(fov) / 2) / zoom))
            self.stage.cameras.append(Camera(name, world, fov, near=obj.get("near", 0.1),
                                             far=obj.get("far", 2000), aspect=obj.get("aspect")))

    def _light(self, obj: dict, world: np.ndarray, name: str) -> None:
        t = obj["type"]
        color = srgb_hex_to_linear(obj.get("color"), (1, 1, 1))
        inten = float(obj.get("intensity", 1.0))
        origin = world[:3, 3].copy()
        if t == "DirectionalLight":
            light = Light(name, "sun", color, inten, world)
            self._pending_targets.append((light, obj.get("target"), origin))
        elif t == "SpotLight":
            light = Light(name, "spot", color, inten * CANDELA_TO_WATT, world,
                          angle_deg=math.degrees(2 * float(obj.get("angle", math.pi / 3))))
            self._pending_targets.append((light, obj.get("target"), origin))
        elif t == "PointLight":
            light = Light(name, "point", color, inten * CANDELA_TO_WATT, world)
        elif t == "RectAreaLight":
            light = Light(name, "area", color, inten * float(obj.get("width", 1)) * float(obj.get("height", 1)), world,
                          size=(float(obj.get("width", 1)), float(obj.get("height", 1))))
        elif t in ("AmbientLight", "HemisphereLight"):
            if t == "HemisphereLight":
                ground = srgb_hex_to_linear(obj.get("groundColor"), color)
                color = tuple((a + b) / 2 for a, b in zip(color, ground))
            light = Light(name, "ambient", color, inten, world)
        else:
            self.warnings.append(f"threejs: {t} not supported, skipped")
            return
        self.stage.lights.append(light)
