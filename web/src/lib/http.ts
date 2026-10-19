import { ZodError } from "zod";
import { ApiError } from "./errors";

type Handler<C> = (req: Request, ctx: C) => Promise<Response>;

/** Wrap a route handler: ApiError/ZodError → `{error:{code,message,hint}}` JSON. */
export function api<C>(handler: Handler<C>): Handler<C> {
  return async (req, ctx) => {
    try {
      return await handler(req, ctx);
    } catch (e) {
      if (e instanceof ApiError) {
        const retryAfter = e.extra.retryAfter;
        const headers = typeof retryAfter === "number" ? { "retry-after": String(retryAfter) } : undefined;
        return Response.json(e.toBody(), { status: e.status, headers });
      }
      if (e instanceof ZodError) {
        const err = new ApiError("invalid_request", undefined, { issues: zodIssues(e) });
        return Response.json(err.toBody(), { status: err.status });
      }
      console.error(e);
      const err = new ApiError("internal");
      return Response.json(err.toBody(), { status: 500 });
    }
  };
}

export function zodIssues(e: ZodError) {
  return e.issues.map((i) => ({ path: i.path.join("."), message: i.message }));
}

export async function readJson(req: Request): Promise<unknown> {
  try {
    return await req.json();
  } catch {
    throw new ApiError("invalid_request", "Body must be JSON.");
  }
}
