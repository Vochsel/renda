"""Scene IR (Hydra-lite): loaders produce a Stage, engines consume it."""

from .camera import ResolvedCamera, frame_bounds, resolve_cameras
from .loaders import load_stage, loader_available
from .rir import read_rir, write_rir
from .stage import Camera, Light, Material, Mesh, Stage, look_at, trs

__all__ = [
    "Camera", "Light", "Material", "Mesh", "Stage", "ResolvedCamera",
    "frame_bounds", "resolve_cameras", "load_stage", "loader_available",
    "read_rir", "write_rir", "look_at", "trs",
]
