import { createHash } from "node:crypto";
import { readdir, readFile, stat } from "node:fs/promises";
import { basename, dirname, isAbsolute, join, relative, resolve } from "node:path";
import { zipSync } from "fflate";
import type { RendaPackageInput } from "@rendahq/spec";
import type { RendaClient } from "./client.js";

export const BUILTIN_HDRIS = new Set(["studio", "sunset", "night"]);

const CONTENT_TYPES: Record<string, string> = {
  ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
  ".hdr": "image/vnd.radiance", ".exr": "image/x-exr", ".json": "application/json",
  ".gltf": "model/gltf+json", ".glb": "model/gltf-binary", ".obj": "model/obj", ".stl": "model/stl",
  ".usdz": "model/vnd.usdz+zip", ".zip": "application/zip", ".mp4": "video/mp4",
};

export function contentTypeFor(path: string): string {
  const m = path.toLowerCase().match(/\.[a-z0-9]+$/);
  return (m && CONTENT_TYPES[m[0]]) || "application/octet-stream";
}

/** True when a uri points at the local filesystem (not https/asset/data). */
export function isLocalUri(uri: string): boolean {
  return !/^(https?|asset|data):/i.test(uri);
}

export function sha256(data: Uint8Array): string {
  return createHash("sha256").update(data).digest("hex");
}

/** Upload bytes (deduped by sha256 server-side). Returns `asset://<sha>`. */
export async function uploadBytes(client: RendaClient, filename: string, data: Uint8Array, contentType = contentTypeFor(filename)): Promise<{ assetUri: string; deduped: boolean; bytes: number }> {
  const res = await client.createUpload({ filename, size: data.byteLength, sha256: sha256(data), contentType });
  if (!res.exists && res.uploadUrl) await client.putUpload(res.uploadUrl, data, contentType);
  return { assetUri: res.assetUri, deduped: res.exists, bytes: data.byteLength };
}

export async function uploadFile(client: RendaClient, path: string) {
  const data = new Uint8Array(await readFile(path));
  return uploadBytes(client, basename(path), data);
}

export interface RewriteResult {
  package: RendaPackageInput;
  uploaded: { path: string; assetUri: string; deduped: boolean; bytes: number }[];
}

/**
 * Find every local path in a package (assets[*].uri, scene.root, scene.include[].asset,
 * environment.hdri), upload it, and rewrite to `asset://<sha>`. Relative paths resolve
 * against `baseDir` (the package file's directory). The input is not mutated.
 */
export async function uploadLocalPaths(
  client: RendaClient,
  pkg: RendaPackageInput,
  baseDir: string,
  upload: (absPath: string) => Promise<{ assetUri: string; deduped: boolean; bytes: number }> = (p) => uploadFile(client, p),
): Promise<RewriteResult> {
  const out: any = structuredClone(pkg);
  const uploaded: RewriteResult["uploaded"] = [];
  const cache = new Map<string, string>();
  const assetIds = new Set(Object.keys(out.assets ?? {}));

  const rewrite = async (uri: string): Promise<string> => {
    if (!isLocalUri(uri)) return uri;
    const abs = isAbsolute(uri) ? uri : resolve(baseDir, uri);
    const hit = cache.get(abs);
    if (hit) return hit;
    const r = await upload(abs);
    cache.set(abs, r.assetUri);
    uploaded.push({ path: abs, ...r });
    return r.assetUri;
  };

  for (const asset of Object.values<any>(out.assets ?? {})) {
    if (asset?.uri) asset.uri = await rewrite(asset.uri);
  }
  if (out.scene?.root && !assetIds.has(out.scene.root)) out.scene.root = await rewrite(out.scene.root);
  for (const inc of out.scene?.include ?? []) {
    if (inc.asset && !assetIds.has(inc.asset)) inc.asset = await rewrite(inc.asset);
  }
  for (const tex of out.scene?.textureOverrides ?? []) {
    if (tex.source && !assetIds.has(tex.source)) tex.source = await rewrite(tex.source);
  }
  const hdri = out.environment?.hdri;
  if (hdri && !assetIds.has(hdri) && !BUILTIN_HDRIS.has(hdri)) out.environment.hdri = await rewrite(hdri);

  return { package: out, uploaded };
}

const ZIP_SKIP = new Set(["node_modules", ".git", "renders", "dist", ".DS_Store", ".hyperframes"]);

/** Zip a directory (skipping node_modules/.git/renders) into memory. */
export async function zipDirectory(dir: string): Promise<Uint8Array> {
  const files: Record<string, Uint8Array> = {};
  const walk = async (d: string) => {
    for (const entry of await readdir(d, { withFileTypes: true })) {
      if (ZIP_SKIP.has(entry.name)) continue;
      const p = join(d, entry.name);
      if (entry.isDirectory()) await walk(p);
      else if (entry.isFile()) files[relative(dir, p).split("\\").join("/")] = new Uint8Array(await readFile(p));
    }
  };
  if (!(await stat(dir)).isDirectory()) throw new Error(`${dir} is not a directory`);
  await walk(dir);
  // deterministic so identical projects dedupe to the same sha256
  const sorted: Record<string, [Uint8Array, { mtime: Date }]> = {};
  for (const k of Object.keys(files).sort()) sorted[k] = [files[k], { mtime: new Date("2000-01-01T00:00:00Z") }];
  return zipSync(sorted, { level: 6 });
}

export function packageDir(packagePath?: string): string {
  return packagePath ? dirname(resolve(packagePath)) : process.cwd();
}
