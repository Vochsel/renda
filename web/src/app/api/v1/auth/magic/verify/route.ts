import { z } from "zod";
import { api, readJson } from "@/lib/http";
import { ApiError } from "@/lib/errors";
import { env } from "@/lib/env";
import { workos } from "@/lib/workos";
import { clientIp } from "@/lib/clientip";
import { rateLimit } from "@/lib/ratelimit";
import { createApiKey, ensureAccount, type WorkosUserLike } from "@/lib/accounts";

const Body = z.object({
  email: z.email(),
  code: z.string().regex(/^\d{6}$/, "6-digit code"),
  keyName: z.string().max(100).optional(),
});

type RawUser = { id: string; email: string; first_name?: string | null; last_name?: string | null };

/** Exchange the emailed code for an API key (creating user + org on first sign-in). */
export const POST = api(async (req) => {
  const { email, code, keyName } = Body.parse(await readJson(req));
  // 6-digit codes: cap guesses per network and per address (WorkOS also limits attempts per code).
  const ip = clientIp(req);
  await rateLimit("magic_verify_ip", ip);
  await rateLimit("magic_verify_email", email.toLowerCase());
  let user: WorkosUserLike;
  let workosOrgId: string | undefined;
  try {
    const res = await workos().userManagement.authenticateWithMagicAuth({
      clientId: env("WORKOS_CLIENT_ID"),
      email: email.toLowerCase(),
      code,
      ipAddress: ip,
      userAgent: req.headers.get("user-agent") ?? undefined,
    });
    user = res.user;
    workosOrgId = res.organizationId;
  } catch (e) {
    // Users in several orgs get `organization_selection_required`; default to the first one.
    const raw = (e as { rawData?: { code?: string; user?: RawUser; organizations?: { id: string }[] } }).rawData;
    if (raw?.code === "organization_selection_required" && raw.user) {
      user = { id: raw.user.id, email: raw.user.email, firstName: raw.user.first_name, lastName: raw.user.last_name };
      workosOrgId = raw.organizations?.[0]?.id;
    } else {
      throw new ApiError("invalid_code");
    }
  }
  const account = await ensureAccount(user, workosOrgId);
  const key = await createApiKey(account.org.id, account.user.id, keyName ?? "agent (magic auth)");
  return Response.json({
    apiKey: key.apiKey,
    user: { id: account.user.id, email: account.user.email },
    org: { id: account.org.id, name: account.org.name },
    next: "Store apiKey securely (it is shown once) and send it as `Authorization: Bearer <apiKey>`.",
  });
});
