

def test_glb_without_extension(tmp_path):
    import shutil
    import trimesh
    from renda.ir.loaders import load_stage

    glb = tmp_path / "box.glb"
    trimesh.creation.box().export(glb)
    blob = tmp_path / ("ab" * 32)  # how the worker's asset cache names files
    shutil.copy(glb, blob)
    assert load_stage(blob).meshes


def test_gltf_sun_lux_converted(tmp_path):
    import json
    import trimesh
    from renda.ir.loaders import load_stage

    path = tmp_path / "lit.gltf"
    trimesh.creation.box().export(path)
    doc = json.loads(path.read_text())
    doc.setdefault("extensionsUsed", []).append("KHR_lights_punctual")
    doc["extensions"] = {"KHR_lights_punctual": {"lights": [
        {"type": "directional", "intensity": 2049},  # Blender's export of a 3 W/m² sun
        {"type": "directional", "intensity": 3},     # three.js-style artist units
    ]}}
    doc["nodes"] += [{"name": "spec", "extensions": {"KHR_lights_punctual": {"light": 0}}},
                     {"name": "artist", "extensions": {"KHR_lights_punctual": {"light": 1}}}]
    doc["scenes"][0]["nodes"] += [len(doc["nodes"]) - 2, len(doc["nodes"]) - 1]
    path.write_text(json.dumps(doc))
    suns = {l.name: l.intensity for l in load_stage(path).lights}
    assert abs(suns["spec"] - 3.0) < 0.01
    assert suns["artist"] == 3.0


def test_threejs_torus_knot(tmp_path):
    import json
    from renda.ir.loaders import load_stage

    doc = {"metadata": {"version": 4.6, "type": "Object", "generator": "Object3D.toJSON"},
           "geometries": [{"uuid": "g", "type": "TorusKnotGeometry", "radius": 1, "tube": 0.4,
                           "tubularSegments": 64, "radialSegments": 8, "p": 2, "q": 3}],
           "object": {"uuid": "m", "type": "Mesh", "geometry": "g", "layers": 1,
                      "matrix": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]}}
    path = tmp_path / "knot.json"
    path.write_text(json.dumps(doc))
    (mesh,) = load_stage(path).meshes
    assert len(mesh.points) == 65 * 9  # three.js vertex count
    assert len(mesh.indices) == 64 * 8 * 2
    assert mesh.uvs is not None
