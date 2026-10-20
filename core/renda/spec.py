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

SceneFormat = Literal["blend", "usd", "gltf", "threejs", "fbx", "obj", "stl", "ply", "dae", "3mf", "abc", "splat", "step", "iges", "rir"]
SCENE_FORMATS: tuple[str, ...] = ("blend", "usd", "gltf", "threejs", "fbx", "obj", "stl", "ply", "dae", "3mf", "abc", "splat", "step", "iges", "rir")

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
    ".step": "step",
    ".stp": "step",
    ".iges": "iges",
    ".igs": "iges",
    ".rir": "rir",
}

EngineName = Literal["auto", "blender-cycles", "blender-eevee", "blender-workbench", "mitsuba", "threejs", "gsplat", "filament", "pbrt"]
ENGINES: tuple[str, ...] = ("blender-cycles", "blender-eevee", "blender-workbench", "mitsuba", "threejs", "gsplat", "filament", "pbrt")

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
        "blend", "usd", "gltf", "threejs", "fbx", "obj", "stl", "ply", "dae", "3mf", "abc", "splat", "step", "iges", "rir",
        "hdr", "exr", "png", "jpg", "webp", "avif", "heic", "tiff", "svg", "psd", "gif",
        "mp4", "mov", "webm", "other",
    ] | None = None
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class Transform(_M):
    translate: Vec3 = (0, 0, 0)
    rotateDeg: Vec3 = (0, 0, 0)
    scale: float | Vec3 = 1
    matrix: list[float] | None = Field(default=None, min_length=16, max_length=16)  # column-major; wins over TRS

    def to_matrix(self):
        """4×4 numpy matrix (row-major indexing, like core.ir.trs)."""
        import numpy as np

        from .ir import trs

        if self.matrix is not None:
            return np.asarray(self.matrix, dtype=np.float64).reshape(4, 4).T
        return trs(self.translate, self.rotateDeg, self.scale)


class MaterialOverride(_M):
    # Restrict the override to materials used by objects whose name matches (glob or /regex/flags).
    object: str | None = Field(default=None, min_length=1)
    baseColor: Color | None = None
    metallic: float | None = Field(default=None, ge=0, le=1)
    roughness: float | None = Field(default=None, ge=0, le=1)
    emission: Color | None = None
    emissionStrength: float | None = Field(default=None, ge=0)
    opacity: float | None = Field(default=None, ge=0, le=1)
    transmission: float | None = Field(default=None, ge=0, le=1)
    ior: float | None = Field(default=None, ge=1)

    _c = field_validator("baseColor", "emission")(_check_color)


TextureSlot = Literal["baseColor", "normal", "roughness", "metallic", "emission", "opacity"]
TEXTURE_SLOTS: tuple[str, ...] = ("baseColor", "normal", "roughness", "metallic", "emission", "opacity")


class TextureMatch(_M):
    """Which material texture slots a texture override replaces. Each pattern is a glob
    (case-insensitive, `*` `?` `[...]`), an exact name, or `/regex/flags`. Empty = every material."""

    material: str | None = Field(default=None, min_length=1)
    object: str | None = Field(default=None, min_length=1)
    texture: str | None = Field(default=None, min_length=1)  # file name of a texture the material already uses
    slot: TextureSlot | None = None  # default: the slot `texture` matched, else baseColor


class TextureUV(_M):
    scale: float | tuple[float, float] = 1
    offset: tuple[float, float] = (0, 0)
    rotationDeg: float = 0


class TextureVideo(_M):
    loop: bool = True
    startSec: float = Field(default=0, ge=0)
    speed: float = Field(default=1, gt=0)
    frame: Annotated[int, Field(ge=0)] | None = None  # stills: use this source frame instead of startSec


class TextureOverride(_M):
    match: TextureMatch = Field(default_factory=TextureMatch)
    source: str = Field(min_length=1)  # asset id or uri: any image (png/jpg/webp/avif/heic/tiff/exr/hdr/svg/psd/gif) or video
    uv: TextureUV = Field(default_factory=TextureUV)
    video: TextureVideo = Field(default_factory=TextureVideo)
    colorSpace: Literal["auto", "srgb", "linear"] = "auto"  # auto: sRGB for baseColor/emission, linear otherwise
    strength: float | None = Field(default=None, ge=0)  # normal-map strength / emission strength


UpAxis = Literal["Y", "Z", "-Y"]  # "-Y": COLMAP/OpenCV, Gaussian splat captures


class Include(_M):
    asset: str = Field(min_length=1)
    transform: Transform | None = None  # placement in the root scene's axes
    upAxis: UpAxis | None = None  # this file's axes, converted to the root's before `transform`
    localTransform: Transform | None = None  # in the file's own axes, before the conversion
    materialOverrides: dict[str, MaterialOverride] | None = None  # this include only


class Tessellation(_M):
    linearDeflection: float | None = Field(default=None, gt=0)  # metres; default 0.05% of bbox diagonal
    angularDeflection: float | None = Field(default=None, gt=0, le=3.14)  # radians; default 0.3


class Ground(_M):
    """Ground plane under the scene: a shadow catcher (shadows only) or a visible floor."""

    enabled: bool = True
    mode: Literal["shadowCatcher", "solid", "reflective"] = "shadowCatcher"
    offset: float | None = None  # height along the up axis; None = bottom of the scene bounds
    size: float | None = Field(default=None, gt=0)  # None = reaches the horizon
    color: Color = "#d9d9d9"
    roughness: float = Field(default=0.6, ge=0, le=1)
    reflectivity: float = Field(default=0.5, ge=0, le=1)
    shadowOpacity: float = Field(default=1, ge=0, le=1)
    contactShadows: bool = True

    _c = field_validator("color")(_check_color)


class Scene(_M):
    root: str = Field(min_length=1)
    include: list[Include] = Field(default_factory=list)
    upAxis: UpAxis | None = None
    unitScale: float = Field(default=1, gt=0)
    materialOverrides: dict[str, MaterialOverride] = Field(default_factory=dict)
    textureOverrides: list[TextureOverride] = Field(default_factory=list)
    tessellation: Tessellation | None = None  # CAD (STEP/IGES) meshing density
    ground: Ground | None = None  # ground plane / shadow catcher


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
MODEL_FORMATS = ("glb",)
BAKE_ENGINES = ("blender-cycles", "mitsuba", "threejs")


class Bake(_M):
    """Bake lighting into textures and return a GLB. See spec/src/index.ts for the modes."""

    mode: Literal["unlit", "ao", "lightmap"] = "unlit"
    size: int = Field(default=2048, ge=64, le=8192)
    uvs: Literal["auto", "existing"] = "auto"
    samples: Annotated[int, Field(gt=0)] | None = None
    margin: int = Field(default=4, ge=0, le=64)
    textureFormat: Literal["png", "jpg", "webp"] = "jpg"


class VideoSettings(_M):
    codec: Literal["h264", "h265", "vp9", "prores", "gif"] | None = None
    crf: int = Field(default=18, ge=0, le=51)


class SplatBakeCamera(_M):
    position: Vec3
    target: Vec3
    up: Vec3 | None = None
    fovDeg: float = Field(default=40.0, gt=1, lt=170)


class SplatBake(_M):
    """Bake the scene into a 3D Gaussian splat (`output.format: "splat"`)."""

    preset: Literal["fast", "standard", "high"] = "standard"
    views: Literal["auto"] | Annotated[int, Field(ge=8, le=1000)] | None = None
    cameras: Annotated[list[SplatBakeCamera], Field(min_length=8, max_length=1000)] | None = None
    resolution: int | None = Field(default=None, ge=128, le=2048)
    steps: int | None = Field(default=None, ge=0, le=100000)
    shDegree: int | None = Field(default=None, ge=0, le=3)
    maxGaussians: int | None = Field(default=None, ge=1000, le=10_000_000)
    engine: EngineName = "auto"
    coverage: Literal["auto", "sphere", "hemisphere", "orbit"] = "auto"
    fanOut: Literal["auto"] | bool = "auto"
    dataset: bool = False


class Output(_M):
    format: Literal["png", "jpg", "webp", "exr", "mp4", "webm", "gif", "mov", "glb", "splat"] = "png"
    passes: list[Literal["beauty", "alpha", "depth", "normal", "albedo", "mask"]] = Field(default_factory=lambda: ["beauty"])
    transparent: bool = False
    video: VideoSettings = Field(default_factory=VideoSettings)
    keepFrames: bool = False
    bake: Bake | None = None
    splatFormat: Literal["ply", "spz", "splat"] | None = None
    splatBake: SplatBake | None = None

    @property
    def is_video(self) -> bool:
        return self.format in VIDEO_FORMATS

    @property
    def is_model(self) -> bool:
        return self.format in MODEL_FORMATS

    @property
    def is_splat(self) -> bool:
        return self.format == "splat"


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
        if self.output.is_splat:
            if self.animation is not None:
                raise ValueError('output.format "splat" bakes a static scene; remove animation')
            if self.output.splatBake and self.output.splatBake.engine == "gsplat":
                raise ValueError("output.splatBake.engine: training views need a mesh renderer (cycles, eevee, mitsuba, threejs or auto)")
        elif self.output.splatFormat or self.output.splatBake:
            raise ValueError('splatFormat/splatBake need output.format "splat"')
        if self.output.transparent and self.output.format in ("jpg", "mp4", "gif"):
            raise ValueError(f"{self.output.format} has no alpha channel; use png/webp/exr/webm/mov")
        if self.output.bake is not None and self.output.format != "glb":
            raise ValueError('bake returns a model: set output.format to "glb"')
        if self.output.format == "glb" and self.animation is not None:
            raise ValueError('output.format "glb" exports/bakes a single pose; remove animation')
        if self.output.bake is not None and self.render.engine in ("gsplat", "blender-eevee", "blender-workbench"):
            raise ValueError(f"{self.render.engine} can't bake; use auto, blender-cycles, mitsuba or threejs")
        return self

    # -- helpers -------------------------------------------------------------
    def frames(self) -> list[int]:
        return self.animation.frame_list() if self.animation else [1]

    def asset_uri(self, ref: str) -> str:
        """Asset id → uri, or the ref itself if it's already a uri/path."""
        return self.assets[ref].uri if ref in self.assets else ref

    def dump(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True, exclude_none=True)


LAYERED_MESSAGE = ("This package uses `extends` or package references; flatten it first "
                   "(renda.layers.flatten / flatten_file, or `renda flatten`).")


def load_package(path_or_dict: str | Path | dict[str, Any]) -> RendaPackage:
    """Parse a package from a dict, a path to a .renda.json, or a JSON string.

    A path is flattened first (its `extends` and package references resolved relative to the file).
    Dicts and JSON strings must already be flat — they have no location to resolve against.
    """
    from .layers import flatten_file, is_layered

    if isinstance(path_or_dict, dict):
        data = path_or_dict
    else:
        s = str(path_or_dict)
        if s.lstrip().startswith("{"):
            data = json.loads(s)
        else:
            return RendaPackage.model_validate(flatten_file(s).package)
    if is_layered(data):
        raise ValueError(LAYERED_MESSAGE)
    return RendaPackage.model_validate(data)


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
