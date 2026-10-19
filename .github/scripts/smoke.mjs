#!/usr/bin/env node
// Production smoke test: public endpoints, local validation, and one tiny real cloud render.
// Usage: RENDA_API_KEY=… [RENDA_API_URL=https://renda.vochsel.com] node .github/scripts/smoke.mjs
// Writes a Markdown summary to $GITHUB_STEP_SUMMARY when set; exits 1 on any failure.
import { execFileSync } from "node:child_process";
import { appendFileSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const api = (process.env.RENDA_API_URL || "https://renda.vochsel.com").replace(/\/+$/, "");
const cli = join(root, "cli/dist/bin.js");
const results = [];

async function check(name, fn) {
  const t0 = Date.now();
  try {
    const detail = (await fn()) ?? "";
    results.push({ name, ok: true, ms: Date.now() - t0, detail });
  } catch (e) {
    results.push({ name, ok: false, ms: Date.now() - t0, detail: String(e?.message ?? e).slice(0, 500) });
  }
}

function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}

function renda(args) {
  // The CLI prints JSON when stdout is not a TTY; non-zero exit still carries a JSON body.
  let out;
  try {
    out = execFileSync(process.execPath, [cli, ...args, "--json"], {
      env: { ...process.env, RENDA_API_URL: api }, encoding: "utf8", timeout: 15 * 60_000,
    });
  } catch (e) {
    out = e.stdout?.toString() || "";
    if (!out.trim()) throw new Error(`renda ${args[0]} failed: ${e.stderr?.toString().slice(0, 300) || e.message}`);
  }
  return JSON.parse(out.slice(out.indexOf("{")));
}

await check("GET /api/v1/pricing", async () => {
  const r = await fetch(`${api}/api/v1/pricing`);
  assert(r.ok, `HTTP ${r.status}`);
  const body = await r.json();
  assert(JSON.stringify(body).includes("gpu-l4"), "pricing has no gpu-l4 entry");
  return "ok";
});

await check("GET /llms.txt", async () => {
  const r = await fetch(`${api}/llms.txt`);
  assert(r.ok, `HTTP ${r.status}`);
  const text = await r.text();
  assert(/renda/i.test(text) && text.length > 500, "llms.txt looks empty");
  return `${text.length} bytes`;
});

await check("worker events endpoint is deployed", async () => {
  // Unsigned → 401. A 404 means the route was dropped from the deploy (happened once).
  const r = await fetch(`${api}/api/v1/internal/worker/events`, { method: "POST", body: "{}", headers: { "content-type": "application/json" } });
  assert(r.status === 401, `expected 401, got ${r.status}`);
  return "401 as expected";
});

const dir = mkdtempSync(join(tmpdir(), "renda-smoke-"));
const pkgPath = join(dir, "smoke.renda.json");
// A fresh seed per run so the result cache can't short-circuit the real pipeline.
const seed = Number(process.env.GITHUB_RUN_ID ?? Date.now()) % 2_000_000_000;
writeFileSync(pkgPath, JSON.stringify({
  version: 1,
  name: "ci-smoke",
  scene: { root: join(root, "testdata/scenes/scene.stl") },
  render: { resolution: [320, 240], quality: "draft", seed },
  output: { format: "png" },
}));

await check("renda validate", async () => {
  const r = renda(["validate", pkgPath]);
  assert(r.status === "ok", `validate: ${JSON.stringify(r).slice(0, 300)}`);
  return "valid";
});

await check("cloud render (STL, 320×240 draft)", async () => {
  assert(process.env.RENDA_API_KEY, "RENDA_API_KEY is not set");
  const r = renda(["render", pkgPath, "-o", join(dir, "out")]);
  assert(r.status === "ok", `render: ${JSON.stringify(r).slice(0, 400)}`);
  const job = r.job ?? {};
  assert(job.status === "succeeded", `job ${job.id} is ${job.status}: ${job.error ?? ""}`);
  const out = (job.outputs ?? [])[0];
  assert(out?.url, "job has no outputs");
  const img = await fetch(out.url);
  assert(img.ok, `output URL HTTP ${img.status}`);
  const bytes = new Uint8Array(await img.arrayBuffer());
  assert(bytes[0] === 0x89 && bytes[1] === 0x50, "output is not a PNG");
  assert((r.files ?? []).length > 0 && readFileSync(r.files[0]).length === bytes.length, "CLI download doesn't match the output");
  return `${job.id} · ${job.engine} · ${job.billedSeconds}s billed · ${bytes.length} bytes`;
});

const failed = results.filter((r) => !r.ok);
const table = [
  `## Renda production smoke ${failed.length ? "❌" : "✅"}`,
  "",
  `Target: ${api}`,
  "",
  "| Check | Result | Time | Detail |",
  "|---|---|---|---|",
  ...results.map((r) => `| ${r.name} | ${r.ok ? "pass" : "**FAIL**"} | ${(r.ms / 1000).toFixed(1)}s | ${r.detail.replace(/\|/g, "\\|").replace(/\n/g, " ")} |`),
  "",
].join("\n");
console.log(table);
if (process.env.GITHUB_STEP_SUMMARY) appendFileSync(process.env.GITHUB_STEP_SUMMARY, table);
process.exit(failed.length ? 1 : 0);
