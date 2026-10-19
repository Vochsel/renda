"""Backfill gallery metadata (width/height/thumbnail/placeholder) for outputs rendered before the worker
recorded it. Dry run by default; pass --apply to upload thumbnails and update jobs.outputs.

    cd worker
    set -a; source ../web/.env.local; set +a        # DATABASE_URL + R2_* credentials
    uv run --with 'psycopg[binary]' python scripts/backfill_thumbs.py              # dry run
    uv run --with 'psycopg[binary]' python scripts/backfill_thumbs.py --apply      # write

Outputs whose objects already expired (R2 lifecycle deletes outputs/ after 30 days) are skipped.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from renda_worker import media  # noqa: E402
from renda_worker.storage import R2Storage  # noqa: E402

LISTED = ("image/", "video/")


def needs_backfill(o: dict) -> bool:
    return o.get("width") is None and str(o.get("contentType", "")).startswith(LISTED)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="upload thumbnails and update the database")
    ap.add_argument("--org", help="only this org id")
    ap.add_argument("--limit", type=int, default=500, help="max jobs to scan (newest first)")
    args = ap.parse_args()

    import psycopg

    storage = R2Storage()
    where = "status = 'succeeded'" + (" and org_id = %(org)s" if args.org else "")
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn, conn.cursor() as cur:
        cur.execute(f"select id, outputs from jobs where {where} order by created_at desc limit %(limit)s", {"org": args.org, "limit": args.limit})
        rows = cur.fetchall()
        scanned = updated = skipped = 0
        for job_id, outputs in rows:
            todo = [i for i, o in enumerate(outputs or []) if needs_backfill(o)]
            if not todo:
                continue
            changed = False
            with tempfile.TemporaryDirectory(prefix="backfill-") as tmp:
                for i in todo:
                    o = outputs[i]
                    scanned += 1
                    local = Path(tmp) / o["key"].rsplit("/", 1)[-1]
                    try:
                        storage.get_file(o["key"], local)
                    except Exception as exc:  # expired / missing object
                        skipped += 1
                        print(f"skip {job_id} {o['key']}: {type(exc).__name__}")
                        continue
                    if not args.apply:
                        m = media.media_for(local)
                        print(f"would add {job_id} {o['key']}: {f'{m.width}x{m.height}' if m else 'no metadata'}")
                        continue
                    media.attach(o, local, storage)
                    changed = changed or "width" in o
            if changed:
                cur.execute("update jobs set outputs = %s::jsonb where id = %s", (json.dumps(outputs), job_id))
                updated += 1
        if args.apply:
            conn.commit()
    print(f"{'applied' if args.apply else 'dry run'}: {scanned} outputs scanned, {updated} jobs updated, {skipped} skipped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
