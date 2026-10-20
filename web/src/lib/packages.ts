import { z } from "zod";
import { safeParsePackage, type RendaPackage } from "@rendahq/spec";
import { ApiError } from "./errors";
import { zodIssues } from "./http";

export const HyperframesPackage = z.object({
  project: z.string().min(1).describe("asset://<sha256> of a zipped HyperFrames project, or an https URL"),
  name: z.string().max(100).optional().describe("Output file stem"),
  composition: z.string().optional().describe("Path inside the project, default index.html"),
  width: z.number().int().min(16).max(7680).optional(),
  height: z.number().int().min(16).max(7680).optional(),
  fps: z.number().positive().max(120).optional(),
  format: z.enum(["mp4", "webm", "mov", "gif"]).optional(),
  quality: z.enum(["draft", "looks", "delivery"]).optional(),
  durationSec: z.number().positive().max(3600).optional().describe("Hint for estimates only"),
  variables: z.record(z.string(), z.unknown()).optional(),
});
export type HyperframesPackage = z.infer<typeof HyperframesPackage>;

const SHA_RE = /^asset:\/\/([0-9a-f]{64})$/;

export type UriCheck = { assetShas: string[]; localUris: string[]; badUris: string[] };

/** Classify every uri in a package. Server only accepts asset://<sha256> and https://. */
export function checkUris(uris: string[]): UriCheck {
  const out: UriCheck = { assetShas: [], localUris: [], badUris: [] };
  for (const uri of uris) {
    const m = uri.match(SHA_RE);
    if (m) out.assetShas.push(m[1]);
    else if (uri.startsWith("https://")) continue;
    else if (uri.startsWith("asset://")) out.badUris.push(uri);
    else out.localUris.push(uri);
  }
  out.assetShas = [...new Set(out.assetShas)];
  return out;
}

const BUILTIN_HDRIS = new Set(["studio", "sunset", "night"]);

/** All uris a scene package depends on (asset table + inline refs that aren't asset ids). */
export function sceneUris(pkg: RendaPackage): string[] {
  const uris = Object.values(pkg.assets).map((a) => a.uri);
  const refs = [pkg.scene.root, ...pkg.scene.include.map((i) => i.asset), ...pkg.scene.textureOverrides.map((t) => t.source)];
  if (pkg.environment.hdri && !BUILTIN_HDRIS.has(pkg.environment.hdri)) refs.push(pkg.environment.hdri);
  for (const r of refs) if (!(r in pkg.assets)) uris.push(r);
  return uris;
}

/**
 * Uris a HyperFrames job depends on: the project zip plus any variable whose value is an uploaded asset
 * (e.g. a turntable clip for a video slot). Other strings are ordinary variable values, not paths.
 */
export function hyperframesUris(pkg: HyperframesPackage): string[] {
  const vars = Object.values(pkg.variables ?? {}).filter((v): v is string => typeof v === "string" && v.startsWith("asset://"));
  return [pkg.project, ...vars];
}

export type ParsedJob =
  | { kind: "scene"; pkg: RendaPackage; raw: unknown; uris: UriCheck }
  | { kind: "hyperframes"; pkg: HyperframesPackage; raw: unknown; uris: UriCheck };

/** Parse + validate a submitted package; throws ApiError with agent hints. Asset existence is checked by the caller. */
/** `allowLocal`: estimates never touch the files, so local paths are fine there (jobs must upload first). */
export function parseJobPackage(kind: "scene" | "hyperframes", input: unknown, opts: { allowLocal?: boolean } = {}): ParsedJob {
  let parsed: ParsedJob;
  if (kind === "hyperframes") {
    const r = HyperframesPackage.safeParse(input);
    if (!r.success) throw new ApiError("invalid_package", undefined, { issues: zodIssues(r.error) });
    parsed = { kind, pkg: r.data, raw: r.data, uris: checkUris(hyperframesUris(r.data)) };
  } else {
    const r = safeParsePackage(input);
    if (!r.success) throw new ApiError("invalid_package", undefined, { issues: zodIssues(r.error) });
    // Forward the package as written: core distinguishes explicit fields from defaults
    // (e.g. a .blend's own camera/world are used unless camera/environment are set).
    parsed = { kind, pkg: r.data, raw: input, uris: checkUris(sceneUris(r.data)) };
  }
  if (parsed.uris.localUris.length && !opts.allowLocal) {
    throw new ApiError("local_uri", undefined, { uris: parsed.uris.localUris });
  }
  if (parsed.uris.badUris.length) {
    throw new ApiError("invalid_package", "asset:// uris must be asset://<64 hex sha256>.", { uris: parsed.uris.badUris });
  }
  return parsed;
}
