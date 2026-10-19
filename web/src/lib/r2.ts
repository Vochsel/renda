import { HeadObjectCommand, GetObjectCommand, PutObjectCommand, S3Client } from "@aws-sdk/client-s3";
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
