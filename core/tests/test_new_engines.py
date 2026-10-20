"""filament + pbrt adapters: registry, planner routing, and the pbrt scene writer (no binaries needed)."""

from pathlib import Path

import numpy as np
import trimesh

from renda.engines import get_engine, registry
from renda.engines.pbrt import PbrtEngine, write_ply
from renda.ir import Material, Mesh, Stage
from renda.ir.exporters.mitsuba import TextureCache
from renda.planner import choose_hardware
from renda.scene import package_lights
from renda.spec import ENGINES, load_package


def test_registered_and_in_spec():
    for name in ("filament", "pbrt"):
        assert name in registry()
        assert name in ENGINES
        d = get_engine(name).describe()
        assert d["name"] == name and "effects" in d


def test_cpu_only_routing():
    pkg = load_package({"version": 1, "scene": {"root": "./a.glb"}, "render": {"engine": "pbrt", "hardware": "gpu"}})
    warnings: list[str] = []
    assert choose_hardware("pbrt", pkg, "rush", 64, 1.0, 1, warnings, {}) == "cpu"
    assert any("CPU containers" in w for w in warnings)
    assert choose_hardware("filament", pkg, "standard", 2, 1.0, 1, [], {}) == "cpu"


def test_write_ply_roundtrips(tmp_path: Path):
    box = trimesh.creation.box()
    uv = np.random.default_rng(0).random((len(box.vertices), 2))
    write_ply(tmp_path / "b.ply", box.vertices, box.faces, box.vertex_normals, uv)
    back = trimesh.load(tmp_path / "b.ply", process=False)
    assert np.allclose(back.vertices, box.vertices, atol=1e-6)
    assert np.array_equal(back.faces, box.faces)


def test_pbrt_world_maps_materials_and_lights(tmp_path: Path):
    stage = Stage(up_axis="Y")
    stage.materials = {
        "gold": Material("gold", base_color=(1.0, 0.8, 0.3, 1.0), metallic=1.0, roughness=0.2),
        "glass": Material("glass", transmission=1.0, ior=1.45, roughness=0.0),
        "paint": Material("paint", base_color=(0.8, 0.1, 0.1, 1.0), roughness=0.4),
        "chalk": Material("chalk", roughness=1.0),
        "lamp": Material("lamp", emission=(1.0, 0.9, 0.8), emission_strength=5.0),
    }
    box = trimesh.creation.box()
    for i, name in enumerate(stage.materials):
        m = np.eye(4)
        m[0, 3] = i * 2.0
        stage.meshes.append(Mesh(name, box.vertices.astype(np.float32), box.faces.astype(np.int32), transform=m, material=name))
    pkg = load_package({"version": 1, "scene": {"root": "./a.glb"},
                        "lights": [{"type": "sun", "direction": [-1, -1, -1], "intensity": 3},
                                   {"type": "point", "position": [0, 2, 0], "intensity": 100}]})
    lines = PbrtEngine()._world(stage, pkg, None, tmp_path, [], TextureCache(None), package_lights)
    text = "\n".join(lines)
    assert '"conductor"' in text and '"dielectric"' in text and '"coateddiffuse"' in text and '"diffuse"' in text
    assert 'AreaLightSource "diffuse"' in text
    assert 'LightSource "distant"' in text and 'LightSource "point"' in text
    assert len(list(tmp_path.glob("mesh*.ply"))) == len(stage.meshes)
    # world transforms are baked into the PLY points
    moved = trimesh.load(tmp_path / "mesh00004.ply", process=False)
    assert np.isclose(moved.vertices[:, 0].mean(), 8.0)
