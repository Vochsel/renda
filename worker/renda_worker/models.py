"""Request bodies the worker accepts. Scene packages are validated by `renda.spec`; here we only
check the envelope and the HyperFrames package shape (which core doesn't know about)."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

ASSET_URI = re.compile(r"^asset://[0-9a-f]{64}$")
JOB_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class StorageGrant(BaseModel):
    """Cloudflare R2 temporary credentials the web app mints per job, scoped to that job's prefixes."""

    model_config = ConfigDict(extra="ignore")

    accessKeyId: str = Field(min_length=1)
    secretAccessKey: str = Field(min_length=1, repr=False)
    sessionToken: str = Field(min_length=1, repr=False)
    bucket: str = Field(min_length=1)
    endpoint: HttpUrl
    prefixes: list[str] = Field(default_factory=list)
    expiresAt: str | None = None


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
    # Per-job event-signing key: HMAC-SHA256(RENDA_WORKER_SECRET, "job:" + jobId), derived by web. Required by
    # the submit endpoint; optional here so local runs and tests can build requests without one.
    jobKey: str | None = Field(None, min_length=32, repr=False)
    # Per-job R2 credentials (see storage.py). Required by the submit endpoint in production.
    storage: StorageGrant | None = Field(None, repr=False)

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
        if not self.outputPrefix.startswith(f"outputs/{self.jobId}/"):
            # Scoped credentials only cover this job's prefixes; anything else would fail at upload time.
            if self.storage is not None:
                raise ValueError("outputPrefix must be outputs/<jobId>/ when per-job storage credentials are used")
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
