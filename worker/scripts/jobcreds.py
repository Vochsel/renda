"""Per-job credentials for scripts that submit to the worker directly (what web/src/lib/worker-auth.ts does).

    RENDA_WORKER_SECRET           → jobKey = HMAC-SHA256(secret, "job:" + jobId)
    CLOUDFLARE_API_TOKEN + R2_*   → storage = R2 temporary credentials scoped to outputs/<jobId>/, work/<jobId>/
Runs on your machine (which holds the master values), never in a Modal container.
"""

from __future__ import annotations

import hashlib
import hmac
import os


def job_key(job_id: str, secret: str | None = None) -> str:
    return hmac.new((secret or os.environ["RENDA_WORKER_SECRET"]).encode(), f"job:{job_id}".encode(), hashlib.sha256).hexdigest()


def mint_storage(job_id: str, ttl: int = 6 * 3600) -> dict:
    import requests

    account, bucket = os.environ["R2_ACCOUNT_ID"], os.environ.get("R2_BUCKET", "renda")
    prefixes = [f"outputs/{job_id}/", f"work/{job_id}/"]
    r = requests.post(f"https://api.cloudflare.com/client/v4/accounts/{account}/r2/temp-access-credentials",
                      headers={"authorization": f"Bearer {os.environ['CLOUDFLARE_API_TOKEN']}"},
                      json={"bucket": bucket, "parentAccessKeyId": os.environ["R2_ACCESS_KEY_ID"],
                            "permission": "object-read-write", "ttlSeconds": ttl, "prefixes": prefixes}, timeout=30)
    body = r.json()
    if not r.ok or not body.get("success"):
        raise SystemExit(f"R2 temp credentials failed ({r.status_code}): {body.get('errors')}")
    res = body["result"]
    return {"accessKeyId": res["accessKeyId"], "secretAccessKey": res["secretAccessKey"], "sessionToken": res["sessionToken"],
            "bucket": bucket, "endpoint": os.environ.get("R2_ENDPOINT") or f"https://{account}.r2.cloudflarestorage.com",
            "prefixes": prefixes}


def job_credentials(job_id: str) -> dict:
    return {"jobKey": job_key(job_id), "storage": mint_storage(job_id)}
