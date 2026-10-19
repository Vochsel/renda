import { authkitProxy } from "@workos-inc/authkit-nextjs";

// Session handling for human-facing pages only. /api/* uses API keys (see lib/auth.ts).
export default authkitProxy({
  middlewareAuth: { enabled: true, unauthenticatedPaths: [] },
});

export const config = { matcher: ["/dashboard/:path*", "/device", "/claim"] };
