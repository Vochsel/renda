import pytest
from pydantic import ValidationError

from renda_worker.models import HyperframesPackage, JobRequest

from .conftest import SHA, URI, job_body


def test_valid_body_defaults_output_prefix():
    job = JobRequest.model_validate(job_body())
    assert job.outputPrefix == "outputs/job_1/"
    assert job.priority == "standard"


@pytest.mark.parametrize(
    "over",
    [
        {"jobId": "../etc"},
        {"inputs": {"https://evil": "https://x"}},
        {"callbackUrl": "not a url"},
        {"outputPrefix": "cache/../x/"},
        {"priority": "whenever"},
        {"kind": "hyperframes", "package": {"project": "/etc/passwd"}},
    ],
)
def test_rejects_bad_bodies(over):
    with pytest.raises(ValidationError):
        JobRequest.model_validate(job_body(**over))


def test_hyperframes_package():
    p = HyperframesPackage.model_validate({"project": URI, "fps": 60, "format": "webm"})
    assert p.quality == "looks"
    with pytest.raises(ValidationError):
        HyperframesPackage.model_validate({"project": URI, "composition": "../x.html"})
    with pytest.raises(ValidationError):
        HyperframesPackage.model_validate({"project": URI, "fps": 25})
    JobRequest.model_validate(job_body(kind="hyperframes", package={"project": f"asset://{SHA}"}))
