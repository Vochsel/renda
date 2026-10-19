import math

import numpy as np
import pytest
import trimesh
from fixtures import threejs_cube_scene, write_mesh_files, write_threejs

from renda.ir import load_stage, read_rir, resolve_cameras, write_rir
from renda.ir.exporters import export
from renda.ir.loaders.threejs import load_dict
from renda.ir.stage import auto_smooth, look_at
from renda.spec import load_package


@pytest.mark.parametrize("ext", ["glb", "stl", "obj", "ply"])
def test_trimesh_formats(tmp_path, ext):
    stage = load_stage(write_mesh_files(tmp_path)[ext])
    assert stage.triangle_count() == 12
    lo, hi = stage.bounds()
    np.testing.assert_allclose(hi - lo, [1, 2, 3], atol=1e-5)
    assert stage.meshes[0].normals is not None


def test_stl_is_z_up_and_flat_shaded(tmp_path):
    stage = load_stage(write_mesh_files(tmp_path)["stl"])
    assert stage.up_axis == "Z"
    m = stage.meshes[0]
    assert len(m.points) == 36  # split at the 90° edges
    assert np.allclose(np.abs(m.normals).max(axis=1), 1)


def test_auto_smooth_keeps_smooth_meshes_indexed():
    s = trimesh.creation.icosphere(3)
    pts, idx, n, _ = auto_smooth(s.vertices, s.faces, 30)
    assert len(pts) == len(s.vertices)


def test_threejs_loader():
    stage = load_dict(threejs_cube_scene())
    assert stage.up_axis == "Y" and stage.source_format == "threejs"
    assert len(stage.meshes) == 3  # cube split by 2 material groups + floor
    assert stage.triangle_count() == 14
    assert {"Red", "Blue", "Floor"} <= set(stage.materials)
    red = stage.materials["Red"]
    assert red.base_color[0] > 0.5 and red.base_color[1] < 0.05 and red.roughness == 0.4
    assert stage.materials["Blue"].metallic == 0.5
    assert stage.materials["Floor"].roughness == 1.0
    cam = stage.camera("MainCam")
    assert cam.fov_y_deg == 45
    np.testing.assert_allclose(cam.position(), [0, 0.5, 4])
    sun = next(l for l in stage.lights if l.type == "sun")
    d = sun.direction()
    np.testing.assert_allclose(d, -np.array([3, 5, 2]) / np.linalg.norm([3, 5, 2]), atol=1e-6)  # aims at the cube
    floor = next(m for m in stage.meshes if m.name == "Floor")
    assert np.allclose(floor.world_points()[:, 1], -0.5)  # rotated flat, moved down


def test_threejs_interleaved_and_instanced():
    doc = threejs_cube_scene()
    tri = [0, 0, 0, 1, 0, 0, 0, 1, 0]  # one triangle, stride 3
    words = np.frombuffer(np.asarray(tri, dtype=np.float32).tobytes(), dtype=np.uint32).tolist()
    doc["geometries"].append({"uuid": "geo-il", "type": "BufferGeometry", "data": {
        "attributes": {"position": {"isInterleavedBufferAttribute": True, "itemSize": 3, "data": "ib", "offset": 0}},
        "interleavedBuffers": {"ib": {"uuid": "ib", "buffer": "ab", "type": "Float32Array", "stride": 3}},
        "arrayBuffers": {"ab": words}}})
    m = np.eye(4)
    inst = np.concatenate([m.T.ravel(), (np.eye(4) + np.pad([[0, 0, 0, 5]], ((3, 0), (0, 0)))).T.ravel()]).tolist()
    doc["object"]["children"].append({"uuid": "im", "type": "InstancedMesh", "name": "Tris", "geometry": "geo-il",
                                      "material": "mat-red", "count": 2, "instanceMatrix": {"array": inst}})
    stage = load_dict(doc)
    tris = [mm for mm in stage.meshes if mm.name.startswith("Tris")]
    assert len(tris) == 2


def test_rir_roundtrip(tmp_path):
    stage = load_dict(threejs_cube_scene())
    write_rir(stage, tmp_path / "s.rir")
    back = read_rir(tmp_path / "s.rir")
    assert back.triangle_count() == stage.triangle_count()
    assert set(back.materials) == set(stage.materials)
    assert [c.name for c in back.cameras] == ["MainCam"]
    np.testing.assert_allclose(back.bounds()[0], stage.bounds()[0])


@pytest.mark.parametrize("ext", ["glb", "obj", "usda"])
def test_export_roundtrip(tmp_path, ext):
    if ext == "usda":
        pytest.importorskip("pxr")
    stage = load_stage(write_threejs(tmp_path))
    out = export(stage, tmp_path / f"out.{ext}")
    back = load_stage(out)
    assert back.triangle_count() == stage.triangle_count()
    np.testing.assert_allclose(back.bounds()[1] - back.bounds()[0], stage.bounds()[1] - stage.bounds()[0], atol=1e-4)


def test_auto_camera_frames_bounds():
    stage = load_dict(threejs_cube_scene())
    pkg = load_package({"scene": {"root": "x"}, "render": {"resolution": [400, 400]}})
    cam = resolve_cameras(pkg, stage, [1])[1]
    lo, hi = stage.bounds()
    radius = np.linalg.norm(hi - lo) / 2
    dist = np.linalg.norm(cam.position - cam.target)
    assert dist * math.sin(math.radians(35) / 2) >= radius  # bounding sphere fits
    assert cam.position[1] > cam.target[1]  # elevated, Y-up


def test_turntable_orbits():
    stage = load_dict(threejs_cube_scene())
    pkg = load_package({"scene": {"root": "x"}, "animation": {"frames": [1, 4], "turntable": {"revolutions": 1}}})
    cams = resolve_cameras(pkg, stage, [1, 2, 3, 4])
    c1, c3 = cams[1], cams[3]
    np.testing.assert_allclose(c1.target, c3.target)
    rel1, rel3 = c1.position - c1.target, c3.position - c3.target
    np.testing.assert_allclose(rel1[1], rel3[1])  # same height
    np.testing.assert_allclose(rel1[[0, 2]], -rel3[[0, 2]], atol=1e-9)  # half a turn


def test_scene_camera_mode_and_fallback():
    stage = load_dict(threejs_cube_scene())
    pkg = load_package({"scene": {"root": "x"}, "camera": {"mode": "scene", "name": "MainCam"}})
    cam = resolve_cameras(pkg, stage, [1])[1]
    np.testing.assert_allclose(cam.position, [0, 0.5, 4])
    warnings = []
    pkg = load_package({"scene": {"root": "x"}, "camera": {"mode": "scene", "name": "Nope"}})
    resolve_cameras(pkg, stage, [1], warnings)
    assert warnings and "Nope" in warnings[0]


def test_look_at_points_down_minus_z():
    m = look_at([0, 0, 5], [0, 0, 0], [0, 1, 0])
    np.testing.assert_allclose(-m[:3, 2], [0, 0, -1])
