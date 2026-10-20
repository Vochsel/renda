"""Fast mode: tiled stills get one task per tile, and the coordinator stitches them losslessly."""

import os

from PIL import Image

from renda_worker import dispatch, pipeline
from renda_worker.models import JobRequest
from renda_worker.storage import MemoryStorage

from .conftest import job_body

W, H, OV = 640, 360, 32


def _grid(n_cols=3, n_rows=2):
    boxes = []
    for r in range(n_rows):
        for c in range(n_cols):
            x0, x1 = W * c // n_cols, W * (c + 1) // n_cols
            y0, y1 = H * r // n_rows, H * (r + 1) // n_rows
            boxes.append([max(0, x0 - OV), max(0, y0 - OV), min(W, x1 + OV), min(H, y1 + OV)])
    return boxes


def _plan(tiles):
    return {"engine": "blender-cycles", "hardware": "gpu-l40s", "frames": [1], "chunks": [[1]] * len(tiles),
            "tiles": tiles, "tile_overlap": OV, "splats": False, "splat_bake": None}


def test_one_task_per_tile_with_its_own_prefix_and_active_tile():
    tiles = _grid()
    job = JobRequest.model_validate(job_body())
    tasks = dispatch.build_tasks(job, _plan(tiles), ["gltf"], "png")
    assert len(tasks) == len(tiles)
    assert [t.plan["active_tile"] for t in tasks] == list(range(len(tiles)))
    assert len({t.upload_prefix for t in tasks}) == len(tiles) and all(not t.final for t in tasks)


def test_stitch_reassembles_the_frame_exactly(tmp_path):
    frame = Image.frombytes("RGBA", (W, H), os.urandom(W * H * 4))
    tiles = _grid()
    storage = MemoryStorage()
    objects = []
    for i, (x0, y0, x1, y1) in enumerate(tiles):
        p = tmp_path / f"t{i}.png"
        frame.crop((x0, y0, x1, y1)).save(p)
        key = dispatch.tile_prefix("job_1", i) + "frame_0001.png"
        storage.put_file(p, key, "image/png")
        objects.append({"key": key, "contentType": "image/png"})
    body = job_body()
    body["package"]["render"] = {"resolution": [W, H]}
    job = JobRequest.model_validate(body)

    class D:
        pass

    deps = D()
    deps.storage, deps.work_root = storage, tmp_path / "work"
    out = pipeline._stitch_tiles(job, _plan(tiles), objects, deps)
    assert out["key"].endswith("frame_0001.png") and "/tiles/" not in out["key"]
    dest = tmp_path / "stitched.png"
    storage.get_file(out["key"], dest)
    assert Image.open(dest).tobytes() == frame.tobytes()
    assert not [k for k in storage.objects if "/tiles/" in k]  # tile objects cleaned up
