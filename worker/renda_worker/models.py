"""Request bodies the worker accepts. Scene packages are validated by `renda.spec`; here we only
check the envelope and the HyperFrames package shape (which core doesn't know about)."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

ASSET_URI = re.compile(r"^asset://[0-9a-f]{64}$")
JOB_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class JobRequest(BaseModel):
    """Body of POST {MODAL_SUBMIT_URL}."""

    model_config = ConfigDict(extra="ignore")

    jobId: str
    kind: Literal["scene", "hyperframes"] = "scene"
    package: dict[str, Any]
    priority: Literal["economy", "standard", "rush"] = "standard"
    inputs: dict[str, HttpUrl] = Field(default_factory=dict, description="assetUri -> presigned GET url")
    outputPrefix: str | None = None
    callbackUrl: HttpUrl

    @field_validator("jobId")
    @classmethod
    def _job_id(cls, v: str) -> str:
        if not JOB_ID.match(v):
            raise ValueError("jobId must be 1-64 chars of [A-Za-z0-9_-]")
        return v

    @field_validator("inputs")
    @classmethod
    def _inputs(cls, v: dict[str, HttpUrl]) -> dict[str, HttpUrl]:
        bad = [k for k in v if not ASSET_URI.match(k)]
        if bad:
            raise ValueError(f"inputs keys must be asset://<sha256>, got {bad[:3]}")
        return v

    @model_validator(mode="after")
    def _prefix(self) -> "JobRequest":
        expected = f"outputs/{self.jobId}/"
        if self.outputPrefix is None:
            self.outputPrefix = expected
        elif not self.outputPrefix.startswith("outputs/") or ".." in self.outputPrefix:
            raise ValueError("outputPrefix must live under outputs/")
        if not self.outputPrefix.endswith("/"):
            self.outputPrefix += "/"
        if self.kind == "hyperframes":
            HyperframesPackage.model_validate(self.package)
        return self


class HyperframesPackage(BaseModel):
    """Package for kind="hyperframes". `project` is a zip of the HyperFrames project directory."""

    model_config = ConfigDict(extra="forbid")

    project: str
    name: str | None = Field(None, max_length=200, description="Output file stem")
    composition: str | None = Field(None, description="Composition file inside the project, default index.html")
    width: int | None = Field(None, ge=16, le=7680)
    height: int | None = Field(None, ge=16, le=7680)
    fps: int | None = Field(None, ge=1, le=240)
    format: Literal["mp4", "webm", "mov", "gif"] = "mp4"
    quality: Literal["draft", "looks", "delivery"] = "looks"
    durationSec: float | None = Field(None, gt=0, description="Estimate hint from the API; not used by the render")
    variables: dict[str, Any] | None = Field(
        None, description="Composition variables. String values that are asset://<sha> or https:// media URLs are "
                          "downloaded into the project and replaced with a local path before rendering.")

    @field_validator("project")
    @classmethod
    def _project(cls, v: str) -> str:
        if not (ASSET_URI.match(v) or v.startswith("https://")):
            raise ValueError("project must be asset://<sha256> or an https:// url to a .zip")
        return v

    @field_validator("composition")
    @classmethod
    def _composition(cls, v: str | None) -> str | None:
        if v is not None and (v.startswith("/") or ".." in v.split("/")):
            raise ValueError("composition must be a relative path inside the project")
        return v
