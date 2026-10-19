"""Pydantic mirror of the canonical Zod schema in spec/src/index.ts.

Keep the two in lockstep: tests/test_spec.py validates spec/examples against both
this module and the generated JSON Schema.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SceneFormat = Literal["blend", "usd", "gltf", "threejs", "fbx", "obj", "stl", "ply", "dae", "3mf", "abc", "splat", "rir"]
SCENE_FORMATS: tuple[str, ...] = ("blend", "usd", "gltf", "threejs", "fbx", "obj", "stl", "ply", "dae", "3mf", "abc", "splat", "rir")

EXTENSION_FORMATS: dict[str, str] = {
    ".blend": "blend",
    ".usd": "usd",
    ".usda": "usd",
    ".usdc": "usd",
    ".usdz": "usd",
    ".gltf": "gltf",
    ".glb": "gltf",
    ".json": "threejs",
    ".fbx": "fbx",
    ".obj": "obj",
    ".stl": "stl",
    ".ply": "ply",
    ".dae": "dae",
    ".3mf": "3mf",
    ".abc": "abc",
    ".splat": "splat",
    ".spz": "splat",
    ".ksplat": "splat",
    ".rir": "rir",
}

EngineName = Literal["auto", "blender-cycles", "blender-eevee", "blender-workbench", "mitsuba", "threejs", "gsplat"]
ENGINES: tuple[str, ...] = ("blender-cycles", "blender-eevee", "blender-workbench", "mitsuba", "threejs", "gsplat")

Vec3 = tuple[float, float, float]
_HEX = re.compile(r"^#([0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")


def _check_color(v: Any) -> Any:
    if isinstance(v, str) and not _HEX.match(v):
        raise ValueError("hex colour #rrggbb or #rrggbbaa")
    return v


Color = Union[str, Vec3]


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Asset(_M):
    uri: str = Field(min_length=1)
    format: Literal[
        "blend", "usd", "gltf", "threejs", "fbx", "obj", "stl", "ply", "dae", "3mf", "abc", "splat", "rir",
        "hdr", "exr", "png", "jpg", "webp", "mp4", "other",
    ] | None = None
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class Transform(_M):
    translate: Vec3 = (0, 0, 0)
    rotateDeg: Vec3 = (0, 0, 0)
    scale: float | Vec3 = 1


class MaterialOverride(_M):
    baseColor: Color | None = None
    metallic: float | None = Field(default=None, ge=0, le=1)
    roughness: float | None = Field(default=None, ge=0, le=1)
    emission: Color | None = None
    emissionStrength: float | None = Field(default=None, ge=0)
    opacity: float | None = Field(default=None, ge=0, le=1)
    transmission: float | None = Field(default=None, ge=0, le=1)
    ior: float | None = Field(default=None, ge=1)

    _c = field_validator("baseColor", "emission")(_check_color)


class Include(_M):
    asset: str = Field(min_length=1)
    transform: Transform | None = None


class Scene(_M):
    root: str = Field(min_length=1)
    include: list[Include] = Field(default_factory=list)
    upAxis: Literal["Y", "Z", "-Y"] | None = None  # "-Y": COLMAP/OpenCV, Gaussian splat captures
    unitScale: float = Field(default=1, gt=0)
    materialOverrides: dict[str, MaterialOverride] = Field(default_factory=dict)


class Lens(_M):
    """Physical lens: focal length (overrides fovDeg) and depth of field (on when fStop is set)."""

    focalLengthMm: float | None = Field(default=None, gt=0, le=5000)
    sensorWidthMm: float = Field(default=36, gt=0)
    fStop: float | None = Field(default=None, gt=0, le=256)
    focusDistance: float | None = Field(default=None, gt=0)
    focusTarget: Union[Literal["auto"], Vec3, Annotated[str, Field(min_length=1)]] = "auto"
    apertureBlades: int = Field(default=0, ge=0, le=16)
    apertureRotationDeg: float = 0
    anamorphicRatio: float = Field(default=1, gt=0, le=4)

    @property
    def dof(self) -> bool:
        return self.fStop is not None


class AutoCamera(_M):
    mode: Literal["auto"]
    fovDeg: float = Field(default=35, gt=0, lt=180)
    elevationDeg: float = 20
    azimuthDeg: float = 35
    padding: float = Field(default=0.1, ge=0)
    lens: Lens | None = None


class SceneCamera(_M):
    mode: Literal["scene"]
    name: str | None = None
    lens: Lens | None = None


class LookAtCamera(_M):
    mode: Literal["lookat"]
    position: Vec3
    target: Vec3 = (0, 0, 0)
    up: Vec3 | None = None
    fovDeg: float = Field(default=40, gt=0, lt=180)
    lens: Lens | None = None


Camera = Annotated[Union[AutoCamera, SceneCamera, LookAtCamera], Field(discriminator="mode")]


class Environment(_M):
    hdri: str | None = "studio"  # null disables environment lighting
    intensity: float = Field(default=1, ge=0)
    rotationDeg: float = 0
    background: Union[Literal["hdri", "transparent"], Color] = "hdri"

    @field_validator("background")
    @classmethod
    def _bg(cls, v: Any) -> Any:
        if isinstance(v, str) and v in ("hdri", "transparent"):
            return v
        return _check_color(v)


class SunLight(_M):
    type: Literal["sun"]
    direction: Vec3
    intensity: float = Field(default=3, ge=0)
    color: Color | None = None


class PointLight(_M):
    type: Literal["point"]
    position: Vec3
    intensity: float = Field(default=100, ge=0)
    color: Color | None = None
    radius: float = Field(default=0.05, ge=0)


class SpotLight(_M):
    type: Literal["spot"]
    position: Vec3
    target: Vec3
    intensity: float = Field(default=100, ge=0)
    angleDeg: float = Field(default=45, gt=0, le=180)
    color: Color | None = None


class AreaLight(_M):
    type: Literal["area"]
    position: Vec3
    target: Vec3
    size: tuple[float, float] = (1, 1)
    intensity: float = Field(default=50, ge=0)
    color: Color | None = None


Light = Annotated[Union[SunLight, PointLight, SpotLight, AreaLight], Field(discriminator="type")]

Quality = Literal["draft", "standard", "final"]


class Antialias(_M):
    filter: Literal["gaussian", "box", "blackman-harris", "mitchell"] = "gaussian"
    filterWidth: float = Field(default=1.5, ge=0.01, le=10)  # pixels


class MotionBlur(_M):
    shutter: float = Field(default=0.5, gt=0, le=1)  # fraction of a frame (0.5 = 180°)
    position: Literal["center", "start", "end"] = "center"
    subframes: int = Field(default=8, ge=2, le=64)  # samples in time where motion blur is emulated


class Render(_M):
    engine: EngineName = "auto"
    resolution: tuple[Annotated[int, Field(ge=16, le=16384)], Annotated[int, Field(ge=16, le=16384)]] = (1920, 1080)
    quality: Quality = "standard"
    samples: Annotated[int, Field(gt=0)] | None = None
    denoise: bool = True
    seed: int = 0
    hardware: Literal["auto", "cpu", "gpu"] = "auto"
    antialias: Antialias | None = None
    motionBlur: MotionBlur | None = None


class Turntable(_M):
    revolutions: float = 1
    axis: Literal["up", "X", "Y", "Z"] = "up"


class Animation(_M):
    frames: tuple[int, int]
    fps: float = Field(default=30, gt=0)
    step: int = Field(default=1, gt=0)
    turntable: Turntable | None = None

    def frame_list(self) -> list[int]:
        a, b = self.frames
        return list(range(a, b + 1, self.step))


IMAGE_FORMATS = ("png", "jpg", "webp", "exr")
VIDEO_FORMATS = ("mp4", "webm", "gif", "mov")


class VideoSettings(_M):
    codec: Literal["h264", "h265", "vp9", "prores", "gif"] | None = None
    crf: int = Field(default=18, ge=0, le=51)


class Output(_M):
    format: Literal["png", "jpg", "webp", "exr", "mp4", "webm", "gif", "mov"] = "png"
    passes: list[Literal["beauty", "alpha", "depth", "normal", "albedo", "mask"]] = Field(default_factory=lambda: ["beauty"])
    transparent: bool = False
    video: VideoSettings = Field(default_factory=VideoSettings)
    keepFrames: bool = False

    @property
    def is_video(self) -> bool:
        return self.format in VIDEO_FORMATS


class Bloom(_M):
    intensity: float = Field(default=0.3, ge=0, le=4)
    threshold: float = Field(default=0.8, ge=0, le=1)  # display luminance where the glow starts
    radius: float = Field(default=0.4, ge=0, le=1)


class Post(_M):
    """Look and lens post effects. Exposure/tonemap run inside the engine where it can; the rest is a shared
    stage (renda.post) applied identically to every engine's output."""

    exposure: float = Field(default=0, ge=-10, le=10)  # stops
    tonemap: Literal["agx", "filmic", "aces", "neutral", "standard"] = "agx"
    contrast: float = Field(default=0, ge=-1, le=1)
    saturation: float = Field(default=1, ge=0, le=2)
    temperature: float = Field(default=0, ge=-1, le=1)
    tint: float = Field(default=0, ge=-1, le=1)
    vignette: float = Field(default=0, ge=0, le=1)
    chromaticAberration: float = Field(default=0, ge=0, le=1)
    lensDistortion: float = Field(default=0, ge=-1, le=1)
    bloom: Bloom | None = None
    filmGrain: float = Field(default=0, ge=0, le=1)
    sharpen: float = Field(default=0, ge=0, le=1)


class RendaPackage(_M):
    schema_: str | None = Field(default=None, alias="$schema")
    version: Literal[1] = 1
    name: str | None = Field(default=None, max_length=200)
    assets: dict[str, Asset] = Field(default_factory=dict)
    scene: Scene
    camera: Camera = Field(default_factory=lambda: AutoCamera(mode="auto"))
    environment: Environment = Field(default_factory=lambda: Environment(hdri="studio"))
    lights: list[Light] = Field(default_factory=list)
    render: Render = Field(default_factory=Render)
    animation: Animation | None = None
    output: Output = Field(default_factory=Output)
    post: Post | None = None

    @field_validator("assets")
    @classmethod
    def _asset_ids(cls, v: dict[str, Asset]) -> dict[str, Asset]:
        for k in v:
            if not re.match(r"^[A-Za-z0-9_.-]+$", k):
                raise ValueError(f"invalid asset id {k!r}")
        return v

    @model_validator(mode="after")
    def _cross(self) -> "RendaPackage":
        if self.render.motionBlur and not (self.animation and self.animation.frames[1] > self.animation.frames[0]):
            raise ValueError("render.motionBlur: motion blur needs movement: add an animation range (e.g. a turntable)")
        if self.output.is_video and self.animation is None:
            raise ValueError(f'output.format "{self.output.format}" needs an animation range')
        if self.output.transparent and self.output.format in ("jpg", "mp4", "gif"):
            raise ValueError(f"{self.output.format} has no alpha channel; use png/webp/exr/webm/mov")
        return self

    # -- helpers -------------------------------------------------------------
    def frames(self) -> list[int]:
        return self.animation.frame_list() if self.animation else [1]

    def asset_uri(self, ref: str) -> str:
        """Asset id → uri, or the ref itself if it's already a uri/path."""
        return self.assets[ref].uri if ref in self.assets else ref

    def dump(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True, exclude_none=True)


def load_package(path_or_dict: str | Path | dict[str, Any]) -> RendaPackage:
    """Parse a package from a dict, a path to a .renda.json, or a JSON string."""
    if isinstance(path_or_dict, dict):
        return RendaPackage.model_validate(path_or_dict)
    s = str(path_or_dict)
    if s.lstrip().startswith("{"):
        return RendaPackage.model_validate(json.loads(s))
    return RendaPackage.model_validate(json.loads(Path(s).read_text()))


def package_for_file(uri: str, **overrides: Any) -> RendaPackage:
    """Wrap a bare scene file into a default package."""
    data: dict[str, Any] = dict(overrides)
    data["assets"] = {"scene": {"uri": uri}, **overrides.get("assets", {})}
    data["scene"] = {"root": "scene", **overrides.get("scene", {})}
    return RendaPackage.model_validate(data)


def format_from_path(path: str) -> str | None:
    m = re.search(r"(\.[a-z0-9]+)(?:[?#].*)?$", path.lower())
    return EXTENSION_FORMATS.get(m.group(1)) if m else None


def parse_color(c: Color | None, default: tuple[float, float, float] = (0.8, 0.8, 0.8)) -> tuple[float, ...]:
    """Hex or [r,g,b] (0..1, linear-ish) → tuple. #rrggbbaa returns 4 values."""
    if c is None:
        return default
    if isinstance(c, str):
        h = c.lstrip("#")
        vals = tuple(int(h[i : i + 2], 16) / 255 for i in range(0, len(h), 2))
        # hex colours are sRGB; convert to linear for renderers
        return tuple(_srgb_to_linear(v) for v in vals[:3]) + vals[3:]
    return tuple(float(x) for x in c)


def _srgb_to_linear(v: float) -> float:
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
