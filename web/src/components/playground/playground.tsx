"use client";

import { LiveCost } from "@/components/live-cost";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { safeParsePackage } from "@rendahq/spec";
import type { ActionResult, QueueJob } from "@/app/dashboard/playground/actions";
import type { Job } from "@/lib/jobs";
import type { UploadInput, UploadTicket } from "@/lib/uploads";
import {
  ACTIONS, CONFIRM_ABOVE, DEFAULT_OPTIONS, MAX_BATCH, PLAYGROUND_PREFIX, RESOLUTIONS,
  combineEstimates, contentTypeFor, copyAs, enginesFor, expandBatch, inlineGltf, pickScene, roleFor,
  type FileRole, type PlaygroundEngine, type PlaygroundFile, type PlaygroundOptions, type PriorityRange,
} from "@/lib/playground";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { StatusBadge } from "@/components/status-badge";
import { CopyButton } from "@/components/copy-button";
import { GlbViewer } from "@/components/viewers/glb-viewer";
import { SplatViewer } from "@/components/viewers/splat-viewer";
import { cn } from "@/lib/utils";

type Priority = PlaygroundOptions["priority"];
type PriorityEstimate = PriorityRange & { chunks?: number };
type Estimate = { warnings?: string[]; priorities?: Record<Priority, PriorityEstimate> };

export type PlaygroundActions = {
  registerUploads: (files: UploadInput[]) => Promise<ActionResult<UploadTicket[]>>;
  estimate: (pkg: unknown, priority: Priority) => Promise<ActionResult<unknown>>;
  submit: (pkg: unknown, priority: Priority, idempotencyKey?: string) => Promise<ActionResult<Job>>;
  getJob: (id: string) => Promise<ActionResult<Job>>;
  getJobs: (ids: string[]) => Promise<ActionResult<QueueJob[]>>;
  cancel: (id: string) => Promise<ActionResult<QueueJob>>;
  recent: () => Promise<ActionResult<QueueJob[]>>;
};

/** A dropped file: hashed in the browser, then uploaded straight to R2. */
type Local = {
  key: string;
  name: string;
  size: number;
  role: FileRole;
  file: File;
  sha256?: string;
  progress: number; // 0..1 upload progress
  state: "hashing" | "ready" | "uploading" | "uploaded" | "error";
  note?: string;
};

/** One queued job. `pkg` is kept so a failed or canceled row can be retried (rows restored after a refresh can't). */
type Row = {
  key: string;
  label: string;
  priority: Priority;
  pkg?: unknown;
  estimate?: PriorityEstimate;
  state: "uploading" | "submitting" | "submitted" | "error";
  job?: QueueJob;
  error?: string;
};

const TERMINAL = new Set(["succeeded", "failed", "canceled"]);
const ACCEPT = ".glb,.gltf,.blend,.usd,.usda,.usdc,.usdz,.fbx,.obj,.stl,.ply,.dae,.3mf,.abc,.json,.step,.stp,.iges,.igs,.splat,.spz,.ksplat,.hdr,.exr,.bin,.mtl,.png,.jpg,.jpeg,.webp";
const SUBMIT_CONCURRENCY = 2;
const POLL_MS = 2500;

const mb = (n: number) => (n >= 1e9 ? `${(n / 1e9).toFixed(2)} GB` : n >= 1e6 ? `${(n / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1e3))} KB`);
const usd = (n: number) => (n < 0.01 ? `$${n.toFixed(4)}` : `$${n.toFixed(2)}`);
const secs = (n: number) => (n >= 90 ? `${(n / 60).toFixed(1)} min` : `${Math.round(n)} s`);
const msg = (e: { message: string; hint?: string }) => (e.hint ? `${e.message} ${e.hint}` : e.message);
const rowLabel = (name: string | null | undefined, id: string) => (name?.startsWith(PLAYGROUND_PREFIX) ? name.slice(PLAYGROUND_PREFIX.length) : name ?? id);

async function sha256Hex(blob: Blob): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", await blob.arrayBuffer());
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/** PUT to the presigned URL with progress (fetch can't report upload progress). */
function putWithProgress(url: string, file: Blob, headers: Record<string, string>, onProgress: (p: number) => void): Promise<void> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", url);
    for (const [k, v] of Object.entries(headers)) xhr.setRequestHeader(k, v);
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
    xhr.onload = () => (xhr.status >= 200 && xhr.status < 300 ? resolve() : reject(new Error(`upload failed (${xhr.status})`)));
    xhr.onerror = () => reject(new Error("upload failed (network or CORS)"));
    xhr.send(file);
  });
}

/** .gltf + its .bin/textures → one self-contained .gltf, so nothing depends on relative paths. */
async function bundleGltf(files: File[]): Promise<{ files: File[]; notes: Map<string, string> }> {
  const notes = new Map<string, string>();
  const gltf = files.find((f) => /\.gltf$/i.test(f.name));
  if (!gltf) return { files, notes };
  const siblings = new Map<string, Uint8Array>();
  for (const f of files) if (f !== gltf && /\.(bin|png|jpe?g|webp|ktx2)$/i.test(f.name)) siblings.set(f.name, new Uint8Array(await f.arrayBuffer()));
  const { text, inlined, missing } = inlineGltf(await gltf.text(), siblings);
  if (!inlined.length && !missing.length) return { files, notes };
  const merged = new File([text], gltf.name, { type: "model/gltf+json" });
  notes.set(gltf.name, missing.length ? `inlined ${inlined.length}; missing: ${missing.join(", ")}` : `bundled ${inlined.length} file${inlined.length === 1 ? "" : "s"}`);
  for (const n of inlined) notes.set(n, `bundled into ${gltf.name}`);
  return { files: files.map((f) => (f === gltf ? merged : f)), notes };
}

/** Run `fn` over `items` with at most `n` in flight. */
async function pool<T>(items: T[], n: number, fn: (t: T) => Promise<void>) {
  const queue = [...items];
  await Promise.all(Array.from({ length: Math.min(n, queue.length) }, async () => {
    while (queue.length) await fn(queue.shift()!);
  }));
}

export function Playground({ actions }: { actions: PlaygroundActions }) {
  const [files, setFiles] = useState<Local[]>([]);
  const [opts, setOpts] = useState<PlaygroundOptions>(DEFAULT_OPTIONS);
  const [extraEngines, setExtraEngines] = useState<PlaygroundEngine[]>([]);
  const [extraRes, setExtraRes] = useState<[number, number][]>([]);
  const [tab, setTab] = useState<"options" | "json">("options");
  const [jsonText, setJsonText] = useState<string | null>(null); // non-null once the user edits the JSON
  const [estimate, setEstimate] = useState<Estimate | null>(null);
  const [itemEstimates, setItemEstimates] = useState<Record<Priority, PriorityEstimate>[]>([]);
  const [estimateError, setEstimateError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(0); // batches still uploading
  const [error, setError] = useState<string | null>(null);
  const [queue, setQueue] = useState<Row[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  const set = <K extends keyof PlaygroundOptions>(k: K, v: PlaygroundOptions[K]) => {
    setOpts((o) => ({ ...o, [k]: v }));
    setJsonText(null);
  };
  const patchRow = useCallback((key: string, p: Partial<Row>) => setQueue((q) => q.map((r) => (r.key === key ? { ...r, ...p } : r))), []);

  // ---- files ------------------------------------------------------------------------------------
  const addFiles = useCallback(async (list: FileList | File[]) => {
    const { files: bundled, notes } = await bundleGltf([...list]);
    const next: Local[] = bundled.map((file) => ({
      key: `${file.name}:${file.size}:${file.lastModified}:${Math.random().toString(36).slice(2, 7)}`,
      name: file.name,
      size: file.size,
      role: notes.get(file.name)?.startsWith("bundled into") ? "sidecar" : roleFor(file.name),
      file,
      progress: 0,
      state: "hashing",
      note: notes.get(file.name),
    }));
    setFiles((cur) => [...cur, ...next]);
    setJsonText(null);
    for (const f of next) {
      try {
        const sha256 = await sha256Hex(f.file);
        setFiles((cur) => cur.map((c) => (c.key === f.key ? { ...c, sha256, state: "ready" } : c)));
      } catch {
        setFiles((cur) => cur.map((c) => (c.key === f.key ? { ...c, state: "error", note: "couldn't read file" } : c)));
      }
    }
  }, []);

  const removeFile = (key: string) => {
    setFiles((cur) => cur.filter((f) => f.key !== key));
    setJsonText(null);
  };

  // Files that go into packages (scenes + HDRI), once hashed.
  const used = useMemo(() => files.filter((f) => (f.role === "scene" || f.role === "hdri") && f.sha256), [files]);
  const sceneFile = pickScene(used);
  const sceneCount = used.filter((f) => f.role === "scene").length;
  const pkgFiles: PlaygroundFile[] = useMemo(
    () => used.map((f) => ({ name: f.name, size: f.size, uri: `asset://${f.sha256}`, role: f.role })),
    [used],
  );
  const hashing = files.some((f) => f.state === "hashing");

  // ---- packages: one per scene × engine × resolution, or the hand-edited JSON ------------------
  const batch = useMemo(() => {
    try {
      return sceneFile ? expandBatch(pkgFiles, opts, { engines: extraEngines, resolutions: extraRes }) : null;
    } catch {
      return null;
    }
  }, [pkgFiles, opts, sceneFile, extraEngines, extraRes]);
  const generated = batch?.items[0]?.pkg ?? null;
  const text = jsonText ?? (generated ? JSON.stringify(generated, null, 2) : "");
  const parsedJson = useMemo(() => {
    if (!text) return { pkg: null as unknown, issues: [] as string[] };
    try {
      const raw = JSON.parse(text);
      const r = safeParsePackage(raw);
      return { pkg: raw as unknown, issues: r.success ? [] : r.error.issues.map((i) => `${i.path.join(".") || "(root)"}: ${i.message}`) };
    } catch (e) {
      return { pkg: null, issues: [`JSON: ${(e as Error).message}`] };
    }
  }, [text]);
  // A hand-edited package is one job; otherwise the expanded batch.
  const items = useMemo<{ label: string; pkg: unknown }[]>(() => {
    if (jsonText !== null) return parsedJson.pkg ? [{ label: sceneFile ? `${sceneFile.name} · custom JSON` : "custom JSON", pkg: parsedJson.pkg }] : [];
    return batch?.items.map((i) => ({ label: i.label, pkg: i.pkg })) ?? [];
  }, [jsonText, parsedJson.pkg, batch, sceneFile]);
  const overCap = jsonText === null && (batch?.total ?? 0) > MAX_BATCH;
  const valid = parsedJson.pkg != null && parsedJson.issues.length === 0 && items.length > 0
    && items.every((i) => safeParsePackage(i.pkg).success);

  // ---- live estimate (debounced): one job → its estimate; a batch → summed cost, slowest wall ----
  useEffect(() => {
    if (!valid) return;
    let stale = false;
    const t = setTimeout(async () => {
      const results: Record<Priority, PriorityEstimate>[] = [];
      let warnings: string[] | undefined;
      let failure: string | null = null;
      await pool(items.map((it, i) => ({ it, i })), 4, async ({ it, i }) => {
        const r = await actions.estimate(it.pkg, opts.priority);
        if (!r.ok) return void (failure ??= r.error.message);
        const e = r.data as Estimate;
        if (e.priorities) results[i] = e.priorities;
        if (i === 0) warnings = e.warnings;
      });
      if (stale) return;
      if (failure || results.filter(Boolean).length !== items.length) {
        setEstimate(null);
        setItemEstimates([]);
        setEstimateError(failure ?? "Couldn't estimate.");
        return;
      }
      const combined = Object.fromEntries((["economy", "standard", "rush"] as const).map((p) => [p, combineEstimates(results.map((r) => r[p]))!])) as Record<Priority, PriorityEstimate>;
      setEstimate({ priorities: combined, warnings });
      setItemEstimates(results);
      setEstimateError(null);
    }, 400);
    return () => {
      stale = true;
      clearTimeout(t);
    };
  }, [valid, items, opts.priority, actions]);

  // ---- uploads ----------------------------------------------------------------------------------
  /** Upload every dropped file these packages reference; the server says which bytes it already has. */
  async function uploadFor(pkgs: unknown[]) {
    const refs = JSON.stringify(pkgs);
    const toSend = files.filter((f) => f.sha256 && refs.includes(f.sha256));
    if (!toSend.length) return;
    const reg = await actions.registerUploads(
      toSend.map((f) => ({ filename: f.name, size: f.size, sha256: f.sha256!, contentType: contentTypeFor(f.name) })),
    );
    if (!reg.ok) throw new Error(msg(reg.error));
    await Promise.all(
      reg.data.map(async (t, i) => {
        const f = toSend[i];
        if (t.exists) {
          setFiles((cur) => cur.map((c) => (c.key === f.key ? { ...c, state: "uploaded", progress: 1, note: c.note ?? "already uploaded" } : c)));
          return;
        }
        setFiles((cur) => cur.map((c) => (c.key === f.key ? { ...c, state: "uploading" } : c)));
        await putWithProgress(t.uploadUrl, f.file, t.uploadHeaders, (p) =>
          setFiles((cur) => cur.map((c) => (c.key === f.key ? { ...c, progress: p } : c))),
        );
        setFiles((cur) => cur.map((c) => (c.key === f.key ? { ...c, state: "uploaded", progress: 1 } : c)));
      }),
    );
  }

  /** Submit one row; rate-limited submissions back off and retry a few times before giving up. */
  const submitRow = useCallback(async (row: Row) => {
    patchRow(row.key, { state: "submitting", error: undefined });
    const idem = crypto.randomUUID();
    for (let attempt = 0; ; attempt++) {
      const r = await actions.submit(row.pkg, row.priority, idem);
      if (r.ok) return patchRow(row.key, { state: "submitted", job: r.data as QueueJob });
      if (r.error.code === "rate_limited" && attempt < 4) {
        patchRow(row.key, { error: "rate limited — retrying…" });
        await new Promise((res) => setTimeout(res, 2000 * 2 ** attempt));
        continue;
      }
      return patchRow(row.key, { state: "error", error: msg(r.error) });
    }
  }, [actions, patchRow]);

  // ---- add to queue (non-blocking: the form is free again immediately) -------------------------
  function enqueue() {
    if (!valid || hashing || !sceneFile) return;
    setError(null);
    if (overCap) return setError(`That's ${batch!.total} jobs; the most per submit is ${MAX_BATCH}. Drop fewer files or compare fewer engines/resolutions.`);
    if (items.length > CONFIRM_ABOVE && !window.confirm(`Queue ${items.length} jobs?`)) return;
    const priority = opts.priority;
    const rows: Row[] = items.map((it, i) => ({
      key: crypto.randomUUID(),
      label: it.label,
      priority,
      pkg: it.pkg,
      estimate: itemEstimates[i]?.[priority],
      state: "uploading",
    }));
    setQueue((q) => [...rows, ...q]);
    setUploading((n) => n + 1);
    (async () => {
      try {
        await uploadFor(rows.map((r) => r.pkg));
      } catch (e) {
        for (const r of rows) patchRow(r.key, { state: "error", error: (e as Error).message });
        return;
      } finally {
        setUploading((n) => n - 1);
      }
      await pool(rows, SUBMIT_CONCURRENCY, submitRow);
    })();
  }

  // ---- restore the last day's playground jobs after a refresh -----------------------------------
  useEffect(() => {
    let cancelled = false;
    actions.recent().then((r) => {
      if (cancelled || !r.ok || !r.data.length) return;
      setQueue((q) => {
        const have = new Set(q.map((x) => x.job?.id));
        const restored: Row[] = r.data.filter((j) => !have.has(j.id)).map((j) => ({
          key: j.id, label: rowLabel(j.name, j.id), priority: j.priority as Priority, state: "submitted", job: j,
        }));
        return [...q, ...restored];
      });
    });
    return () => {
      cancelled = true;
    };
  }, [actions]);

  // ---- poll every active job in one request -----------------------------------------------------
  const activeIds = queue.filter((r) => r.job && !TERMINAL.has(r.job.status)).map((r) => r.job!.id);
  const activeKey = activeIds.join(",");
  useEffect(() => {
    if (!activeKey) return;
    const ids = activeKey.split(",");
    const t = setInterval(async () => {
      const r = await actions.getJobs(ids);
      if (!r.ok) return;
      const byId = new Map(r.data.map((j) => [j.id, j]));
      setQueue((q) => q.map((row) => (row.job && byId.has(row.job.id) ? { ...row, job: byId.get(row.job.id)! } : row)));
    }, POLL_MS);
    return () => clearInterval(t);
  }, [activeKey, actions]);

  async function cancelRow(row: Row) {
    if (!row.job) return;
    const r = await actions.cancel(row.job.id);
    if (r.ok) patchRow(row.key, { job: r.data });
    else patchRow(row.key, { error: msg(r.error) });
  }
  const cancelAll = () => queue.filter((r) => r.job && !TERMINAL.has(r.job.status)).forEach(cancelRow);
  const retryRow = (row: Row) => row.pkg && submitRow({ ...row, job: undefined });

  const copies = generated && sceneFile && valid ? copyAs(parsedJson.pkg as never, opts, sceneFile.name) : null;
  const engines = enginesFor(opts.action);
  const usesRes = opts.action === "still" || opts.action === "turntable";
  const pe = valid ? estimate?.priorities : undefined; // stale estimates hide while the package is invalid
  const selectedRow = queue.find((r) => r.key === selected);
  const finished = queue.filter((r) => r.job?.status === "succeeded");
  const n = items.length;

  const toggle = <T,>(list: T[], v: T, eq: (a: T, b: T) => boolean) => (list.some((x) => eq(x, v)) ? list.filter((x) => !eq(x, v)) : [...list, v]);
  const sameRes = (a: [number, number], b: [number, number]) => a[0] === b[0] && a[1] === b[1];

  return (
    <div className="space-y-6">
      {/* Drop zone */}
      <div
        role="button"
        tabIndex={0}
        aria-label="Add files: drop them here or press Enter to browse"
        onClick={() => input.current?.click()}
        onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), input.current?.click())}
        onDragOver={(e) => (e.preventDefault(), setDragging(true))}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          if (e.dataTransfer.files.length) addFiles(e.dataTransfer.files);
        }}
        className={cn(
          "border-border hover:border-foreground/40 grid cursor-pointer place-items-center rounded-xl border border-dashed px-6 py-10 text-center transition-colors",
          dragging && "border-foreground bg-muted/50",
        )}
      >
        <div>
          <p className="text-sm font-medium">Drop 3D files here, or click to browse</p>
          <p className="text-muted-foreground mt-1 text-xs">
            Several scenes = one job each. .glb .gltf(+.bin, textures) .blend .usdz .fbx .obj .stl .ply .dae .3mf .abc .step .iges · splats .ply .spz .splat · optional .hdr/.exr environment
          </p>
        </div>
        <input
          ref={input}
          type="file"
          multiple
          accept={ACCEPT}
          className="hidden"
          data-testid="playground-file-input"
          onChange={(e) => {
            if (e.target.files?.length) addFiles(e.target.files);
            e.target.value = "";
          }}
        />
      </div>

      {files.length > 0 && (
        <ul className="divide-border border-border divide-y rounded-lg border text-sm" aria-label="Files">
          {files.map((f) => (
            <li key={f.key} className="flex items-center gap-3 px-3 py-2">
              <span className="min-w-0 flex-1 truncate font-mono text-xs">{f.name}</span>
              <span className="text-muted-foreground hidden font-mono text-[11px] sm:inline">
                {f.role}
                {f.note ? ` · ${f.note}` : ""}
              </span>
              <span className="text-muted-foreground w-16 text-right font-mono text-[11px]">{mb(f.size)}</span>
              <span className="w-20">
                {f.state === "uploading" ? (
                  <Progress value={f.progress * 100} aria-label={`Uploading ${f.name}`} />
                ) : (
                  <span className="text-muted-foreground font-mono text-[11px]">
                    {f.state === "hashing" ? "hashing…" : f.state === "uploaded" ? "uploaded" : f.state === "error" ? "error" : f.role === "unsupported" ? "ignored" : "ready"}
                  </span>
                )}
              </span>
              <button type="button" onClick={() => removeFile(f.key)} className="text-muted-foreground hover:text-foreground text-xs" aria-label={`Remove ${f.name}`}>
                ✕
              </button>
            </li>
          ))}
        </ul>
      )}

      {/* Action */}
      <div role="radiogroup" aria-label="What to make" className="grid grid-cols-2 gap-2 sm:grid-cols-5">
        {ACTIONS.map((a) => (
          <button
            key={a.id}
            type="button"
            role="radio"
            aria-checked={opts.action === a.id}
            onClick={() => set("action", a.id)}
            className={cn(
              "rounded-lg border px-3 py-2 text-left transition-colors",
              opts.action === a.id ? "border-foreground bg-muted/60" : "border-border hover:border-foreground/40",
            )}
          >
            <span className="block text-sm font-medium">{a.label}</span>
            <span className="text-muted-foreground block text-[11px]">{a.hint}</span>
          </button>
        ))}
      </div>

      {/* Options / JSON */}
      <div
        className="border-border overflow-hidden rounded-lg border"
        onKeyDown={(e) => {
          const t = e.target as HTMLElement;
          if (e.key === "Enter" && !e.shiftKey && (t.tagName === "INPUT" || t.tagName === "SELECT")) {
            e.preventDefault();
            enqueue();
          }
        }}
      >
        <div className="border-border flex gap-4 border-b px-4" role="tablist">
          {(["options", "json"] as const).map((t) => (
            <button
              key={t}
              role="tab"
              type="button"
              aria-selected={tab === t}
              onClick={() => setTab(t)}
              className={cn("-mb-px border-b py-2 font-mono text-xs", tab === t ? "border-foreground" : "text-muted-foreground border-transparent")}
            >
              {t === "options" ? "Options" : ".renda.json"}
            </button>
          ))}
          {jsonText !== null && <span className="text-muted-foreground ml-auto self-center text-[11px]">edited by hand · one job · options paused</span>}
        </div>
        {tab === "options" ? (
          <div className="grid gap-4 p-4 sm:grid-cols-2 lg:grid-cols-3">
            {engines.length > 0 && (
              <Field label="Engine">
                <select className={SELECT} value={engines.includes(opts.engine) ? opts.engine : "auto"} onChange={(e) => set("engine", e.target.value as PlaygroundEngine)}>
                  {engines.map((e) => <option key={e} value={e}>{e}</option>)}
                </select>
                <Chips
                  label="Compare with"
                  options={engines.filter((e) => e !== opts.engine).map((e) => ({ id: e, label: e.replace("blender-", ""), on: extraEngines.includes(e) }))}
                  onToggle={(id) => setExtraEngines((l) => toggle(l, id as PlaygroundEngine, (a, b) => a === b))}
                />
              </Field>
            )}
            {usesRes && (
              <Field label="Resolution">
                <div className="flex gap-2">
                  <select
                    className={SELECT}
                    value={RESOLUTIONS.find((r) => r.value.join("x") === opts.resolution.join("x"))?.label ?? "custom"}
                    onChange={(e) => {
                      const r = RESOLUTIONS.find((x) => x.label === e.target.value);
                      if (r) set("resolution", r.value);
                    }}
                  >
                    {RESOLUTIONS.map((r) => <option key={r.label}>{r.label}</option>)}
                    <option value="custom">custom</option>
                  </select>
                  <input aria-label="Width" type="number" min={16} max={8192} className={cn(SELECT, "w-20")} value={opts.resolution[0]}
                    onChange={(e) => set("resolution", [Number(e.target.value) || 16, opts.resolution[1]])} />
                  <input aria-label="Height" type="number" min={16} max={8192} className={cn(SELECT, "w-20")} value={opts.resolution[1]}
                    onChange={(e) => set("resolution", [opts.resolution[0], Number(e.target.value) || 16])} />
                </div>
                <Chips
                  label="Also render at"
                  options={RESOLUTIONS.filter((r) => !sameRes(r.value, opts.resolution)).map((r) => ({ id: r.label, label: r.label, on: extraRes.some((x) => sameRes(x, r.value)) }))}
                  onToggle={(id) => {
                    const r = RESOLUTIONS.find((x) => x.label === id)!.value;
                    setExtraRes((l) => toggle(l, r, sameRes));
                  }}
                />
              </Field>
            )}
            {opts.action !== "splat" && opts.action !== "glb" && (
              <Field label="Quality">
                <select className={SELECT} value={opts.quality} onChange={(e) => set("quality", e.target.value as PlaygroundOptions["quality"])}>
                  {["draft", "standard", "final"].map((q) => <option key={q}>{q}</option>)}
                </select>
              </Field>
            )}
            {usesRes && (
              <Field label="Background">
                <div className="flex gap-2">
                  <select
                    className={SELECT}
                    value={opts.background.startsWith("#") ? "colour" : opts.background}
                    onChange={(e) => set("background", e.target.value === "colour" ? "#f4f4f2" : (e.target.value as "studio" | "transparent"))}
                  >
                    <option value="studio">studio HDRI</option>
                    <option value="transparent">transparent</option>
                    <option value="colour">colour</option>
                  </select>
                  {opts.background.startsWith("#") && (
                    <input aria-label="Background colour" type="color" className="h-8 w-10 rounded border" value={opts.background}
                      onChange={(e) => set("background", e.target.value as `#${string}`)} />
                  )}
                </div>
              </Field>
            )}
            {opts.action === "turntable" && (
              <Field label="Length">
                <select className={SELECT} value={opts.turntableSeconds} onChange={(e) => set("turntableSeconds", Number(e.target.value))}>
                  {[2, 3, 5, 8].map((s) => <option key={s} value={s}>{s} s · {s * opts.fps} frames</option>)}
                </select>
              </Field>
            )}
            {opts.action === "bake" && (
              <>
                <Field label="Bake mode">
                  <select className={SELECT} value={opts.bakeMode} onChange={(e) => set("bakeMode", e.target.value as PlaygroundOptions["bakeMode"])}>
                    <option value="unlit">unlit — full lighting in the colour</option>
                    <option value="ao">ao — ambient occlusion, PBR kept</option>
                    <option value="lightmap">lightmap — separate texture</option>
                  </select>
                </Field>
                <Field label="Texture size">
                  <select className={SELECT} value={opts.bakeSize} onChange={(e) => set("bakeSize", Number(e.target.value))}>
                    {[1024, 2048, 4096].map((s) => <option key={s} value={s}>{s}²</option>)}
                  </select>
                </Field>
              </>
            )}
            {opts.action === "splat" && (
              <Field label="Preset">
                <select className={SELECT} value={opts.splatPreset} onChange={(e) => set("splatPreset", e.target.value as PlaygroundOptions["splatPreset"])}>
                  <option value="fast">fast — seconds, colour only</option>
                  <option value="standard">standard — lighting baked in</option>
                  <option value="high">high — L40S, sharper</option>
                </select>
              </Field>
            )}
            <Field label="Priority">
              <select className={SELECT} value={opts.priority} onChange={(e) => setOpts((o) => ({ ...o, priority: e.target.value as Priority }))}>
                <option value="economy">economy — cheapest</option>
                <option value="standard">standard</option>
                <option value="rush">rush — fastest</option>
              </select>
            </Field>
          </div>
        ) : (
          <div className="p-4">
            <textarea
              aria-label="Package JSON"
              spellCheck={false}
              value={text}
              placeholder="Add a file to generate the package."
              onChange={(e) => setJsonText(e.target.value)}
              className="bg-muted/40 border-border h-72 w-full resize-y rounded-md border p-3 font-mono text-[12px] leading-relaxed"
            />
            <div className="mt-2 flex items-center gap-3 text-[11px]">
              {text && (parsedJson.issues.length ? (
                <ul className="text-destructive space-y-0.5" aria-live="polite">{parsedJson.issues.slice(0, 6).map((i) => <li key={i}>{i}</li>)}</ul>
              ) : (
                <span className="text-muted-foreground" aria-live="polite">valid package{n > 1 ? ` (first of ${n} jobs shown)` : ""}</span>
              ))}
              {jsonText !== null && (
                <button type="button" className="text-muted-foreground hover:text-foreground ml-auto underline" onClick={() => setJsonText(null)}>
                  reset to options
                </button>
              )}
            </div>
          </div>
        )}
      </div>

      {/* Estimate + add to queue */}
      <div className="flex flex-wrap items-end gap-4">
        <div className="min-w-0 basis-full sm:basis-0 sm:flex-1" aria-live="polite">
          {pe ? (
            <>
              {n > 1 && (
                <p className="text-muted-foreground mb-1 font-mono text-[11px]" data-testid="batch-summary">
                  {n} jobs{sceneCount > 1 ? ` · ${sceneCount} scenes` : ""}{extraEngines.length ? ` × ${1 + extraEngines.filter((e) => engines.includes(e)).length} engines` : ""}
                  {usesRes && extraRes.length ? ` × ${1 + extraRes.length} sizes` : ""} · total cost, slowest job
                </p>
              )}
              <div className="grid grid-cols-3 gap-2 font-mono text-[11px]">
                {(["economy", "standard", "rush"] as const).map((p) => (
                  <button
                    key={p}
                    type="button"
                    onClick={() => setOpts((o) => ({ ...o, priority: p }))}
                    className={cn("rounded-md border px-2 py-1.5 text-left", opts.priority === p ? "border-foreground" : "border-border text-muted-foreground")}
                  >
                    <span className="block uppercase tracking-wide">{p}</span>
                    <span className="text-foreground block">{usd(pe[p].estCostUsd[0])}–{usd(pe[p].estCostUsd[1])}</span>
                    <span className="block">{secs(pe[p].estWallSeconds[0])}–{secs(pe[p].estWallSeconds[1])}{pe[p].hardware ? ` · ${pe[p].hardware}` : ""}</span>
                  </button>
                ))}
              </div>
            </>
          ) : (
            <p className="text-muted-foreground text-xs">
              {!files.length ? "Add files to see the estimate." : hashing ? "Reading files…" : overCap ? `${batch!.total} jobs — the most per submit is ${MAX_BATCH}.` : estimateError ?? (valid ? "Estimating…" : "Fix the package to see the estimate.")}
            </p>
          )}
          {valid && estimate?.warnings?.length ? <p className="text-muted-foreground mt-1 text-[11px]">{estimate.warnings.slice(0, 2).join(" · ")}</p> : null}
        </div>
        <Button className="w-full sm:w-auto" onClick={enqueue} disabled={!valid || hashing || !sceneFile || overCap}>
          {n > 1 ? `Add ${n} to queue` : "Add to queue"}
        </Button>
      </div>
      {uploading > 0 && <p className="text-muted-foreground text-xs">Uploading files for {uploading} batch{uploading === 1 ? "" : "es"}… you can keep queueing.</p>}
      {error && <p className="text-destructive text-sm" role="alert">{error}</p>}

      {copies && (
        <details className="text-sm">
          <summary className="text-muted-foreground cursor-pointer text-xs">Copy as CLI · MCP · JSON{n > 1 ? " (first job)" : ""}</summary>
          <div className="mt-3 space-y-3">
            {([["CLI", copies.cli], ["MCP tool call", copies.mcp], [".renda.json", copies.json], ["API (curl, with the JSON as job.json)", copies.curl]] as const).map(([label, value]) => (
              <div key={label}>
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground text-[11px]">{label}</span>
                  <CopyButton text={value} />
                </div>
                <pre className="bg-muted/40 border-border mt-1 max-h-48 overflow-auto rounded-md border p-2 font-mono text-[11px]">{value}</pre>
              </div>
            ))}
          </div>
        </details>
      )}

      {queue.length > 0 && (
        <QueuePanel
          rows={queue}
          selected={selected}
          onSelect={(k) => setSelected((s) => (s === k ? null : k))}
          onCancel={cancelRow}
          onRetry={retryRow}
          onCancelAll={cancelAll}
          onClearFinished={() => setQueue((q) => q.filter((r) => !(r.job && TERMINAL.has(r.job.status)) && r.state !== "error"))}
        />
      )}

      {selectedRow?.job && <JobResult job={selectedRow.job} />}

      {finished.length > 0 && (
        <section aria-label="Finished" className="space-y-2">
          <h2 className="text-sm font-medium">Finished</h2>
          <ul className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
            {finished.map((r) => (
              <li key={r.key}>
                <button type="button" onClick={() => setSelected(r.key)} className="block w-full text-left">
                  <Thumb job={r.job!} />
                  <span className="text-muted-foreground mt-1 block truncate font-mono text-[11px]">{r.label}</span>
                </button>
                <a href={`/dashboard/jobs/${r.job!.id}`} className="text-muted-foreground font-mono text-[10px] underline-offset-2 hover:underline">job →</a>
              </li>
            ))}
          </ul>
          <a href="/dashboard/gallery" className="text-muted-foreground text-xs hover:underline">Gallery →</a>
        </section>
      )}
    </div>
  );
}

const SELECT = "border-border bg-background h-8 w-full rounded-md border px-2 text-sm";

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block space-y-1">
      <span className="text-muted-foreground block text-[11px]">{label}</span>
      {children}
    </label>
  );
}

/** Toggle chips for variations (each extra choice multiplies the batch). */
function Chips({ label, options, onToggle }: { label: string; options: { id: string; label: string; on: boolean }[]; onToggle: (id: string) => void }) {
  if (!options.length) return null;
  return (
    <div className="flex flex-wrap items-center gap-1 pt-1" role="group" aria-label={label}>
      <span className="text-muted-foreground mr-1 text-[10px]">{label}</span>
      {options.map((o) => (
        <button
          key={o.id}
          type="button"
          aria-pressed={o.on}
          onClick={() => onToggle(o.id)}
          className={cn(
            "rounded border px-1.5 py-0.5 font-mono text-[10px] transition-colors",
            o.on ? "border-foreground bg-foreground text-background" : "border-border text-muted-foreground hover:text-foreground",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

function rowStatus(r: Row): string {
  if (r.state === "uploading") return "uploading";
  if (r.state === "submitting") return "submitting";
  if (r.state === "error" && !r.job) return "failed";
  return r.job?.status ?? "queued";
}

function QueuePanel({ rows, selected, onSelect, onCancel, onRetry, onCancelAll, onClearFinished }: {
  rows: Row[];
  selected: string | null;
  onSelect: (key: string) => void;
  onCancel: (r: Row) => void;
  onRetry: (r: Row) => void;
  onCancelAll: () => void;
  onClearFinished: () => void;
}) {
  const active = rows.filter((r) => r.state !== "error" && !(r.job && TERMINAL.has(r.job.status))).length;
  const spent = rows.reduce((n, r) => n + (r.job?.costUsd ?? 0), 0);
  return (
    <section aria-label="Queue" className="space-y-2">
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="text-sm font-medium">Queue</h2>
        <span className="text-muted-foreground font-mono text-[11px]">{active} active · {rows.length} total · {usd(spent)} billed</span>
        <span className="ml-auto flex gap-3 text-xs">
          {active > 0 && <button type="button" onClick={onCancelAll} className="text-muted-foreground hover:text-foreground underline-offset-2 hover:underline">Cancel all</button>}
          <button type="button" onClick={onClearFinished} className="text-muted-foreground hover:text-foreground underline-offset-2 hover:underline">Clear finished</button>
        </span>
      </div>
      <ul className="divide-border border-border divide-y rounded-lg border text-sm" data-testid="queue">
        {rows.map((r) => {
          const status = rowStatus(r);
          const j = r.job;
          const running = j && !TERMINAL.has(j.status);
          const eta = running && r.estimate ? `~${secs(r.estimate.estWallSeconds[1])}` : "";
          const failed = status === "failed" || j?.status === "failed" || j?.status === "canceled";
          return (
            <li key={r.key} className={cn("px-3 py-2", selected === r.key && "bg-muted/50")} data-testid="queue-row">
              <div className="flex items-center gap-3">
                {j?.thumb ? <Thumb job={j} small /> : <span className="bg-muted size-8 shrink-0 rounded" aria-hidden />}
                <button type="button" onClick={() => j && onSelect(r.key)} className="min-w-0 flex-1 truncate text-left font-mono text-xs hover:underline" disabled={!j}>
                  {r.label}
                </button>
                {j ? <StatusBadge status={j.status} /> : <span className="text-muted-foreground font-mono text-[11px]">{status}</span>}
                {/* Cost slot: billed so far. Live running cost (LiveCost) drops in here. */}
                <span className="text-muted-foreground hidden w-16 text-right font-mono text-[11px] sm:inline" data-slot="cost">
                  {j ? <LiveCost job={j} suffix={false} /> : r.estimate ? `≤${usd(r.estimate.estCostUsd[1])}` : ""}
                </span>
                <span className="text-muted-foreground hidden w-12 text-right font-mono text-[11px] sm:inline">{eta}</span>
                <span className="flex w-14 justify-end gap-2 text-[11px]">
                  {running && <button type="button" onClick={() => onCancel(r)} className="text-muted-foreground hover:text-foreground" aria-label={`Cancel ${r.label}`}>cancel</button>}
                  {failed && r.pkg != null && <button type="button" onClick={() => onRetry(r)} className="text-muted-foreground hover:text-foreground" aria-label={`Retry ${r.label}`}>retry</button>}
                </span>
              </div>
              {(r.state === "uploading" || r.state === "submitting" || running) && (
                <Progress className="mt-2 h-1" value={Math.round((j?.progress ?? 0) * 100)} aria-label={`${r.label} progress`} />
              )}
              {(r.error || j?.error) && <p className="text-destructive mt-1 text-[11px]">{j?.error ?? r.error}</p>}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

/** Result thumbnail: the worker's thumb + blurred placeholder, reserving the aspect ratio. */
function Thumb({ job, small }: { job: QueueJob; small?: boolean }) {
  const t = job.thumb;
  if (small) {
    return t ? (
      // eslint-disable-next-line @next/next/no-img-element -- presigned thumbnail
      <img src={t.url} alt="" className="size-8 shrink-0 rounded object-cover" style={t.placeholder ? { backgroundImage: `url(${t.placeholder})`, backgroundSize: "cover" } : undefined} />
    ) : null;
  }
  const ratio = t?.width && t?.height ? `${t.width} / ${t.height}` : "4 / 3";
  return (
    <div className="bg-muted overflow-hidden rounded-md" style={{ aspectRatio: ratio, ...(t?.placeholder ? { backgroundImage: `url(${t.placeholder})`, backgroundSize: "cover" } : {}) }}>
      {t ? (
        // eslint-disable-next-line @next/next/no-img-element -- presigned thumbnail
        <img src={t.url} alt={job.name ?? job.id} loading="lazy" className="h-full w-full object-cover" />
      ) : (
        <span className="text-muted-foreground grid h-full place-items-center font-mono text-[10px]">{job.outputs[0]?.name ?? "output"}</span>
      )}
    </div>
  );
}

function JobResult({ job }: { job: Job }) {
  const done = job.status === "succeeded";
  const primary = job.outputs.find((o) => !/\/thumbs\//.test(o.url)) ?? job.outputs[0];
  return (
    <section aria-label="Result" className="border-border space-y-3 rounded-lg border p-4">
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <StatusBadge status={job.status} />
        <a href={`/dashboard/jobs/${job.id}`} className="font-mono text-xs underline-offset-2 hover:underline">{job.id}</a>
        <span className="text-muted-foreground font-mono text-[11px]">
          {[job.engine, job.hardware, job.billedSeconds ? `${job.billedSeconds.toFixed(1)} s billed` : null, job.cached ? "cached" : null].filter(Boolean).join(" · ")}
        </span>
        <a href="/dashboard/gallery" className="text-muted-foreground ml-auto text-xs underline-offset-2 hover:underline">Gallery →</a>
      </div>
      {!TERMINAL.has(job.status) && <Progress value={Math.round((job.progress ?? 0) * 100)} aria-label="Job progress" />}
      {job.error && <p className="text-destructive text-sm">{job.error}</p>}
      {job.warnings.length > 0 && <p className="text-muted-foreground text-[11px]">{job.warnings.slice(0, 3).join(" · ")}</p>}
      {done && primary && <OutputView output={primary} />}
      {done && job.outputs.length > 1 && (
        <ul className="text-muted-foreground flex flex-wrap gap-x-4 gap-y-1 font-mono text-[11px]">
          {job.outputs.map((o) => <li key={o.url}><a href={o.url} className="hover:text-foreground underline-offset-2 hover:underline">{o.name}</a></li>)}
        </ul>
      )}
    </section>
  );
}

function OutputView({ output }: { output: Job["outputs"][number] }) {
  const [zoom, setZoom] = useState(false);
  const name = output.name.toLowerCase();
  const box = "bg-muted relative overflow-hidden rounded-lg";
  if (output.contentType.startsWith("image/")) {
    return (
      <div className={cn(box, zoom ? "max-h-[80vh] overflow-auto" : "")}>
        {/* eslint-disable-next-line @next/next/no-img-element -- presigned output URL */}
        <img
          src={output.url}
          alt={output.name}
          onClick={() => setZoom((z) => !z)}
          className={cn(zoom ? "max-w-none cursor-zoom-out" : "mx-auto max-h-[70vh] w-auto cursor-zoom-in")}
        />
      </div>
    );
  }
  if (output.contentType.startsWith("video/")) {
    return <div className={box}><video src={output.url} controls autoPlay loop muted playsInline className="mx-auto max-h-[70vh]" /></div>;
  }
  if (name.endsWith(".glb")) return <div className={box} style={{ aspectRatio: "4 / 3" }}><GlbViewer src={output.url} alt={output.name} /></div>;
  if (/\.(spz|splat|ply)$/.test(name)) {
    const ext = name.split(".").pop() as "spz" | "splat" | "ply";
    return <div className={box} style={{ aspectRatio: "4 / 3" }}><SplatViewer src={output.url} fileType={ext} /></div>;
  }
  return <a href={output.url} className="text-sm underline">Download {output.name}</a>;
}
