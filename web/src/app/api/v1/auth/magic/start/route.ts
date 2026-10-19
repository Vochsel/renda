import { z } from "zod";
import { api, readJson } from "@/lib/http";
import { workos } from "@/lib/workos";
import { ApiError } from "@/lib/errors";
import { clientIp } from "@/lib/clientip";
import { rateLimit } from "@/lib/ratelimit";

const Body = z.object({ email: z.email() });

/** Email a 6-digit Magic Auth code. Works for new and existing users. */
export const POST = api(async (req) => {
  const { email } = Body.parse(await readJson(req));
  const ip = clientIp(req);
  await rateLimit("magic_start_ip", ip);
  await rateLimit("magic_start_email", email.toLowerCase());
  try {
    await workos().userManagement.createMagicAuth({
      email: email.toLowerCase(),
      ipAddress: ip,
      userAgent: req.headers.get("user-agent") ?? undefined,
    });
  } catch (err) {
    // Magic Auth switched off in the WorkOS environment: point agents at the device flow instead of a bare 500.
    if ((err as { error?: string }).error === "authentication_method_not_allowed") throw new ApiError("signup_unavailable");
    throw err;
  }
  return Response.json({
    ok: true,
    next: "Ask the user for the 6-digit code emailed to them, then POST /api/v1/auth/magic/verify {email, code}.",
  });
});
