import { flattenPackage, isLayered, LAYER_LIMITS, LayerError, type FlattenResult } from "@rendahq/spec";
import { safeFetchText } from "@rendahq/spec/node";
import { ApiError } from "./errors";
import { getObjectText } from "./r2";
import { ensureVerifiedAssets } from "./assets";

/**
 * Flatten a submitted package's `extends` / package references on the server. Layers may be
 * https:// URLs (fetched with SSRF protections: public addresses only, https only, size + time
 * limits, re-validated redirects) or asset:// URIs the org has uploaded. Local paths can't be read
 * here — clients flatten those before upload. Unlayered packages pass through untouched.
 */
export async function flattenForOrg(orgId: string, input: unknown): Promise<FlattenResult> {
  if (!isLayered(input)) return { package: input as Record<string, unknown>, provenance: {}, layers: [] };
  try {
    return await flattenPackage(input, {
      base: null,
      label: "<submitted>",
      fetchHttps: (url) => safeFetchText(url, { maxBytes: LAYER_LIMITS.maxBytes }),
      readAsset: async (sha) => {
        // Same rule as job inputs: only this account's uploads, hash-verified.
        let key: string;
        try {
          [{ key }] = await ensureVerifiedAssets(orgId, [sha]);
        } catch {
          throw new Error(`asset://${sha} isn't one of this account's verified uploads`);
        }
        return getObjectText(key, LAYER_LIMITS.maxBytes);
      },
    });
  } catch (e) {
    if (e instanceof LayerError) {
      const local = e.code === "layer_not_allowed" || (e.code === "layer_ref" && /relative ref/.test(e.message));
      throw new ApiError(local ? "local_uri" : "layer_error", e.message, { layer: e.layer, field: e.field, reason: e.code });
    }
    throw e;
  }
}
