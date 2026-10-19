import { createHash } from "node:crypto";
import {
  CopyObjectCommand, DeleteObjectCommand, HeadObjectCommand, GetObjectCommand, PutObjectCommand, S3Client,
} from "@aws-sdk/client-s3";
import { getSignedUrl } from "@aws-sdk/s3-request-presigner";
import { env } from "./env";

let _s3: S3Client | undefined;

/** Cloudflare R2 via the S3 API (zero egress fees). */
export function s3(): S3Client {
  _s3 ??= new S3Client({
    region: "auto",
    endpoint: `https://${env("R2_ACCOUNT_ID")}.r2.cloudflarestorage.com`,
    credentials: { accessKeyId: env("R2_ACCESS_KEY_ID"), secretAccessKey: env("R2_SECRET_ACCESS_KEY") },
  });
  return _s3;
}

const bucket = () => env("R2_BUCKET");

export const assetKey = (sha256: string) => `assets/${sha256}`;
/** Where an org uploads bytes it hasn't proven yet; promoted to assetKey() after the hash checks out. */
export const stagingKey = (orgId: string, sha256: string) => `staging/${orgId}/${sha256}`;

/** sha256 (hex) of an object's bytes, streamed. */
export async function sha256OfObject(key: string): Promise<string> {
  const res = await s3().send(new GetObjectCommand({ Bucket: bucket(), Key: key }));
  const hash = createHash("sha256");
  for await (const chunk of res.Body as AsyncIterable<Uint8Array>) hash.update(chunk);
  return hash.digest("hex");
}

export async function copyObject(from: string, to: string): Promise<void> {
  await s3().send(new CopyObjectCommand({ Bucket: bucket(), CopySource: `${bucket()}/${from}`, Key: to }));
}

export async function deleteObject(key: string): Promise<void> {
  await s3().send(new DeleteObjectCommand({ Bucket: bucket(), Key: key }));
}
export const outputPrefix = (jobId: string) => `outputs/${jobId}/`;

export async function objectExists(key: string): Promise<boolean> {
  try {
    await s3().send(new HeadObjectCommand({ Bucket: bucket(), Key: key }));
    return true;
  } catch (e) {
    const status = (e as { $metadata?: { httpStatusCode?: number } }).$metadata?.httpStatusCode;
    if (status === 404 || (e as Error).name === "NotFound") return false;
    throw e;
  }
}

/** Read a small object as text, refusing anything larger than maxBytes. */
export async function getObjectText(key: string, maxBytes: number): Promise<string> {
  const res = await s3().send(new GetObjectCommand({ Bucket: bucket(), Key: key }));
  if ((res.ContentLength ?? 0) > maxBytes) throw new Error(`object is ${res.ContentLength} bytes (limit ${maxBytes})`);
  const text = (await res.Body?.transformToString("utf-8")) ?? "";
  if (text.length > maxBytes) throw new Error(`object exceeds ${maxBytes} bytes`);
  return text;
}

export function presignPut(key: string, contentType: string, size: number, expiresIn = 3600) {
  return getSignedUrl(
    s3(),
    new PutObjectCommand({ Bucket: bucket(), Key: key, ContentType: contentType, ContentLength: size }),
    { expiresIn },
  );
}

export function presignGet(key: string, expiresIn = Number(env("R2_OUTPUT_TTL_SECONDS")), filename?: string) {
  return getSignedUrl(
    s3(),
    new GetObjectCommand({
      Bucket: bucket(),
      Key: key,
      ResponseContentDisposition: filename ? `inline; filename="${filename.replace(/"/g, "")}"` : undefined,
    }),
    { expiresIn },
  );
}
