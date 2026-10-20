// Billed seconds per hardware class for a list of Renda job ids, from production usage records (read-only).
// Run from web/ so @neondatabase/serverless resolves:  node ../bench/agents/renda_cost.mjs '["job_…"]'
import { createRequire } from "node:module";
import { join } from "node:path";

const require = createRequire(join(process.cwd(), "package.json"));
const { neon } = require("@neondatabase/serverless");

const ids = JSON.parse(process.argv[2] ?? "[]");
const sql = neon(process.env.DATABASE_URL);
const rows = ids.length
  ? await sql`select job_id, hardware, sum(seconds)::float as s from usage_events where job_id = any(${ids}) group by job_id, hardware`
  : [];
const out = {};
for (const r of rows) (out[r.job_id] ??= {})[r.hardware] = r.s;
process.stdout.write(JSON.stringify(out));
