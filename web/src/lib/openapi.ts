/** Hand-written OpenAPI 3.1 for the public API. Keep in sync with app/api/v1/**. */
export function openapi(baseUrl: string) {
  const ref = (name: string) => ({ $ref: `#/components/schemas/${name}` });
  const json = (schema: object) => ({ content: { "application/json": { schema } } });
  const err = { description: "Error", ...json(ref("Error")) };
  const body = (schema: object) => ({ required: true, ...json(schema) });
  const ok = (schema: object, description = "OK") => ({ description, ...json(schema) });
  const authed = { security: [{ bearer: [] }] };

  return {
    openapi: "3.1.0",
    info: {
      title: "Renda API",
      version: "1.0.0",
      description: "Agent-first cloud rendering for 3D files and scenes. Every error includes a `hint` describing the next step.",
    },
    servers: [{ url: `${baseUrl}/api/v1` }],
    components: {
      securitySchemes: { bearer: { type: "http", scheme: "bearer", description: "API key: rnd_live_…" } },
      schemas: {
        Error: {
          type: "object",
          required: ["error"],
          properties: {
            error: {
              type: "object",
              required: ["code", "message", "hint"],
              properties: { code: { type: "string" }, message: { type: "string" }, hint: { type: "string" }, issues: { type: "array", items: { type: "object" } } },
            },
          },
        },
        RendaPackage: { $ref: `${baseUrl}/api/v1/schema.json` },
        HyperframesPackage: {
          type: "object",
          required: ["project"],
          properties: {
            project: { type: "string", description: "asset://<sha256> of a zipped HyperFrames project, or https URL" },
            name: { type: "string" }, composition: { type: "string" },
            width: { type: "integer" }, height: { type: "integer" }, fps: { type: "number" },
            format: { enum: ["mp4", "webm", "mov", "gif"] }, quality: { enum: ["draft", "looks", "delivery"] },
            durationSec: { type: "number" }, variables: { type: "object" },
          },
        },
        JobRequest: {
          type: "object",
          required: ["package"],
          properties: {
            package: { oneOf: [ref("RendaPackage"), ref("HyperframesPackage")] },
            kind: { enum: ["scene", "hyperframes"], default: "scene" },
            priority: { enum: ["economy", "standard", "rush"], default: "standard" },
            idempotencyKey: { type: "string" },
            maxCostUsd: { type: "number", description: "Refuse the job if its estimate is above this (422 job_over_budget), and stop it if it runs past it." },
          },
        },
        Job: {
          type: "object",
          properties: {
            id: { type: "string" }, kind: { enum: ["scene", "hyperframes"] }, name: { type: ["string", "null"] },
            status: { enum: ["queued", "planning", "rendering", "encoding", "succeeded", "failed", "canceled"] },
            priority: { enum: ["economy", "standard", "rush"] }, progress: { type: "number", minimum: 0, maximum: 1 },
            engine: { type: "string" }, hardware: { enum: ["cpu", "gpu-l4", "gpu-l40s"] },
            createdAt: { type: "string", format: "date-time" }, startedAt: { type: "string", format: "date-time" }, finishedAt: { type: "string", format: "date-time" },
            billedSeconds: { type: "number" }, costUsd: { type: "number" }, cached: { type: "boolean" },
            warnings: { type: "array", items: { type: "string" } }, error: { type: "string" }, estimate: ref("Estimate"),
            outputs: { type: "array", items: { type: "object", properties: { name: { type: "string" }, url: { type: "string", format: "uri" }, contentType: { type: "string" }, bytes: { type: "integer" } } } },
            live: {
              type: "object",
              description: "Only while the job is running: billed seconds so far plus running containers, at list price (the final bill can be lower, e.g. free minutes). Interpolate with costUsd + ratePerSecond × (now − updatedAt).",
              properties: {
                seconds: { type: "object", properties: { cpu: { type: "number" }, "gpu-l4": { type: "number" }, "gpu-l40s": { type: "number" } } },
                costUsd: { type: "number" }, activeContainers: { type: "integer" }, ratePerSecond: { type: "number" },
                updatedAt: { type: "string", format: "date-time" },
              },
            },
          },
        },
        Estimate: {
          type: "object",
          properties: {
            engine: { type: "string" }, hardware: { type: "string" }, frames: { type: "integer" },
            estSeconds: { type: "array", items: { type: "number" }, minItems: 2, maxItems: 2 },
            estWallSeconds: { type: "array", items: { type: "number" } },
            estCostUsd: { type: "array", items: { type: "number" }, minItems: 2, maxItems: 2 },
            warnings: { type: "array", items: { type: "string" } },
          },
        },
      },
    },
    paths: {
      "/auth/trial": {
        post: { summary: "Anonymous claimable trial: API key + ~5 free render-minutes, no signup. Give claimUrl to the human.", responses: { 201: ok({ type: "object", properties: { apiKey: { type: "string" }, claimUrl: { type: "string" }, freeSecondsLeft: { type: "number" }, expiresAt: { type: "string", format: "date-time" }, limits: { type: "object" }, next: { type: "array", items: { type: "string" } }, terms: { type: "object", description: "Terms of Service / Privacy Policy links and version: tell the user", properties: { version: { type: "string" }, termsUrl: { type: "string" }, privacyUrl: { type: "string" }, notice: { type: "string" } } } } }, "Created"), 429: err } },
      },
      "/auth/magic/start": {
        post: { summary: "Email the user a 6-digit sign-in code (creates the account if new)", requestBody: body({ type: "object", required: ["email"], properties: { email: { type: "string", format: "email" } } }), responses: { 200: ok({ type: "object", properties: { ok: { type: "boolean" }, next: { type: "string" } } }), 400: err } },
      },
      "/auth/magic/verify": {
        post: { summary: "Exchange the emailed code for an API key", requestBody: body({ type: "object", required: ["email", "code"], properties: { email: { type: "string" }, code: { type: "string", pattern: "^\\d{6}$" }, keyName: { type: "string" } } }), responses: { 200: ok({ type: "object", properties: { apiKey: { type: "string" }, user: { type: "object" }, org: { type: "object" }, terms: { type: "object", description: "Terms of Service / Privacy Policy links and version: tell the user", properties: { version: { type: "string" }, termsUrl: { type: "string" }, privacyUrl: { type: "string" }, notice: { type: "string" } } } } }), 400: err } },
      },
      "/auth/device": {
        post: { summary: "Start device login (CLI)", responses: { 200: ok({ type: "object", properties: { deviceCode: { type: "string" }, userCode: { type: "string" }, verificationUri: { type: "string" }, verificationUriComplete: { type: "string" }, interval: { type: "integer" }, expiresIn: { type: "integer" } } }) } },
      },
      "/auth/device/token": {
        post: { summary: "Poll for the device login result", requestBody: body({ type: "object", required: ["deviceCode"], properties: { deviceCode: { type: "string" } } }), responses: { 200: ok({ type: "object", properties: { apiKey: { type: "string" } } }), 428: err, 400: err } },
      },
      "/me": {
        get: { ...authed, summary: "Current user, org, free balance, billing status", responses: { 200: ok({ type: "object" }), 401: err } },
        delete: { ...authed, summary: "Delete the whole account (only when the user asks). Revokes all keys, cancels running jobs, deletes outputs and uploads, cancels the subscription with a final invoice. Billing records are kept, anonymised.", requestBody: body({ type: "object", required: ["confirm"], properties: { confirm: { type: "string", description: "The org.id from GET /me" } } }), responses: { 200: ok({ type: "object", properties: { deleted: { type: "boolean" }, orgId: { type: "string" }, keysRevoked: { type: "integer" }, jobsCanceled: { type: "integer" }, subscriptionsCanceled: { type: "integer" }, storagePurged: { type: "boolean" } } }), 400: err, 401: err } },
      },
      "/uploads": {
        post: { ...authed, summary: "Register a content-addressed upload; PUT bytes to uploadUrl unless exists. exists is only true once *your account* has uploaded these bytes and they were hash-verified (first use in a job); another account having the same file doesn't count", description: "contentType must be a plain MIME type (no parameters). Bytes are checked against sha256 on first use; a mismatch fails the job with asset_mismatch.", requestBody: body({ type: "object", required: ["filename", "size", "sha256"], properties: { filename: { type: "string" }, size: { type: "integer" }, sha256: { type: "string" }, contentType: { type: "string" } } }), responses: { 200: ok({ type: "object", properties: { assetUri: { type: "string" }, exists: { type: "boolean" }, uploadUrl: { type: "string" }, uploadMethod: { const: "PUT" }, uploadHeaders: { type: "object" } } }), 401: err } },
      },
      "/uploads/{sha256}": {
        delete: { ...authed, summary: "Delete your upload of a file (asset://<sha256>). The stored bytes are deleted unless another account uploaded the same file. 409 while a running job uses it.", parameters: [{ name: "sha256", in: "path", required: true, schema: { type: "string", pattern: "^[0-9a-f]{64}$" } }], responses: { 200: ok({ type: "object", properties: { assetUri: { type: "string" }, deleted: { type: "boolean" }, storageDeleted: { type: "boolean" } } }), 404: err, 409: err } },
      },
      "/estimate": { post: { ...authed, summary: "Estimate hardware, time and cost (free)", requestBody: body({ type: "object", required: ["package"], properties: { package: { type: "object" }, kind: { enum: ["scene", "hyperframes"] }, priority: { enum: ["economy", "standard", "rush"] } } }), responses: { 200: ok(ref("Estimate")), 422: err } } },
      "/packages/flatten": { post: { ...authed, summary: "Resolve `extends` layers and package references (https:// or your asset:// uploads) into one flat package, with field → layer provenance", requestBody: body({ type: "object", required: ["package"], properties: { package: { type: "object" } } }), responses: { 200: ok({ type: "object", properties: { package: { type: "object" }, provenance: { type: "object", additionalProperties: { type: "string" } }, layers: { type: "array", items: { type: "string" } }, valid: { type: "boolean" }, issues: { type: "array" } } }), 422: err } } },
      "/jobs": {
        post: { ...authed, summary: "Submit a render job (layered packages are flattened first; see /packages/flatten)", parameters: [{ name: "Idempotency-Key", in: "header", schema: { type: "string" } }], requestBody: body(ref("JobRequest")), responses: { 201: ok(ref("Job"), "Created"), 402: err, 422: err, 503: err } },
        get: { ...authed, summary: "List jobs (newest first)", parameters: [{ name: "limit", in: "query", schema: { type: "integer", maximum: 200 } }, { name: "before", in: "query", schema: { type: "string", format: "date-time" } }, { name: "status", in: "query", schema: { type: "string" } }], responses: { 200: ok({ type: "object", properties: { jobs: { type: "array", items: ref("Job") } } }) } },
      },
      "/jobs/{id}": {
        get: { ...authed, summary: "Get a job (outputs carry presigned download URLs)", parameters: [{ name: "id", in: "path", required: true, schema: { type: "string" } }], responses: { 200: ok(ref("Job")), 404: err } },
        delete: { ...authed, summary: "Delete a finished job's outputs from storage and clear them on the job (the job record and its billing stay). 409 while running.", parameters: [{ name: "id", in: "path", required: true, schema: { type: "string" } }], responses: { 200: ok({ type: "object", properties: { id: { type: "string" }, outputsDeleted: { type: "boolean" }, objectsDeleted: { type: "integer" }, objectsKept: { type: "integer", description: "Files still listed by a cached copy of this job" } } }), 404: err, 409: err } },
      },
      "/jobs/{id}/cancel": { post: { ...authed, summary: "Cancel a job", parameters: [{ name: "id", in: "path", required: true, schema: { type: "string" } }], responses: { 200: ok(ref("Job")), 409: err } } },
      "/outputs": {
        get: {
          ...authed,
          summary: "All render outputs of your org, newest first (what the dashboard gallery shows). Cursor-paginated: pass nextCursor back until it is null. Depth/normal passes and kept video frames are hidden unless all=1.",
          parameters: [
            { name: "cursor", in: "query", schema: { type: "string" } },
            { name: "limit", in: "query", schema: { type: "integer", minimum: 1, maximum: 100, default: 30 } },
            { name: "kind", in: "query", schema: { enum: ["image", "video", "model"] } },
            { name: "all", in: "query", schema: { enum: ["0", "1"] } },
          ],
          responses: {
            200: ok({
              type: "object",
              properties: {
                items: {
                  type: "array",
                  items: {
                    type: "object",
                    properties: {
                      id: { type: "string" }, jobId: { type: "string" }, jobName: { type: ["string", "null"] }, name: { type: "string" },
                      kind: { enum: ["image", "video", "model"] }, url: { type: "string", description: "Presigned download URL" },
                      thumbUrl: { type: ["string", "null"], description: "≤480px webp (video: poster)" }, contentType: { type: "string" },
                      width: { type: ["integer", "null"] }, height: { type: ["integer", "null"] }, bytes: { type: "integer" },
                      placeholder: { type: ["string", "null"], description: "Tiny blurred data: URI for progressive loading" },
                      pass: { type: ["string", "null"] }, engine: { type: ["string", "null"] }, hardware: { type: ["string", "null"] },
                      billedSeconds: { type: "number" }, costUsd: { type: "number" }, createdAt: { type: "string", format: "date-time" },
                    },
                  },
                },
                nextCursor: { type: ["string", "null"] },
              },
            }),
            400: err,
            401: err,
          },
        },
      },
      "/usage": { get: { ...authed, summary: "Usage and cost for a period (default: this month)", parameters: [{ name: "from", in: "query", schema: { type: "string", format: "date-time" } }, { name: "to", in: "query", schema: { type: "string", format: "date-time" } }], responses: { 200: ok({ type: "object" }) } } },
      "/billing/limit": {
        get: { ...authed, summary: "Monthly spending limit and spend so far this month (UTC)", responses: { 200: ok({ type: "object", properties: { monthlyLimitUsd: { type: "number" }, spentThisMonthUsd: { type: "number" } } }) } },
        put: { ...authed, summary: "Set the monthly spending limit (null = default). New jobs are refused, and running ones stopped, once it's reached.", requestBody: body({ type: "object", required: ["monthlyLimitUsd"], properties: { monthlyLimitUsd: { type: ["number", "null"] } } }), responses: { 200: ok({ type: "object", properties: { monthlyLimitUsd: { type: "number" }, spentThisMonthUsd: { type: "number" } } }), 400: err } },
      },
      "/billing/checkout": { post: { ...authed, summary: "Stripe Checkout link (setup mode) for the user to add a card", responses: { 200: ok({ type: "object", properties: { url: { type: "string" } } }) } } },
      "/billing/portal": { get: { ...authed, summary: "Stripe customer portal link", responses: { 200: ok({ type: "object", properties: { url: { type: "string" } } }) } } },
      "/pricing": { get: { summary: "Price table", responses: { 200: ok({ type: "object" }) } } },
      "/schema.json": { get: { summary: "Renda package JSON Schema", responses: { 200: ok({ type: "object" }) } } },
    },
  };
}
