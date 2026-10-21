import type { Instrumentation } from "next";

// Uncaught errors in pages, server components, server actions and route handlers that escape `api()`.
export const onRequestError: Instrumentation.onRequestError = async (error, request, context) => {
  if (process.env.NEXT_RUNTIME !== "nodejs") return;
  const { captureError } = await import("@/lib/posthog");
  captureError(error, {
    route: context.routePath,
    routeType: context.routeType,
    method: request.method,
    digest: (error as { digest?: string }).digest,
  });
};
