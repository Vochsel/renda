import { RendaApiError } from "@rendahq/mcp";
import { LayerError } from "@rendahq/spec";
import { UsageError } from "./args.js";

/** gh-style exit codes, shared with the Python CLI. */
export const EXIT = { ok: 0, error: 1, usage: 2, auth: 3, payment: 4 } as const;

export interface Io {
  stdout: (s: string) => void;
  stderr: (s: string) => void;
  isTTY: boolean;
}

export const processIo: Io = {
  stdout: (s) => process.stdout.write(s),
  stderr: (s) => process.stderr.write(s),
  isTTY: !!process.stdout.isTTY,
};

/** Thrown by commands to end with a structured result + exit code. */
export class CliError extends Error {
  constructor(
    public code: string,
    message: string,
    public exitCode: number = EXIT.error,
    public hint?: string,
    public next: string[] = [],
    public extra: Record<string, unknown> = {},
  ) {
    super(message);
  }
}

export function exitCodeFor(e: RendaApiError): number {
  if (e.status === 402 || e.code === "payment_required") return EXIT.payment;
  if (e.status === 401 || ["unauthorized", "unauthenticated", "trial_expired"].includes(e.code)) return EXIT.auth;
  return EXIT.error;
}

/** Never echo API keys into next[]/hints (cf. the Vercel CLI token-in-next[] CVE). */
export function redact(s: string): string {
  return s.replace(/rnd_live_[A-Za-z0-9_-]{8,}/g, "rnd_live_…");
}

export class Out {
  constructor(public io: Io, public json: boolean) {}

  /** Progress/info for humans; silent in JSON mode so stdout stays one JSON object. */
  info(msg: string) {
    if (!this.json) this.io.stderr(msg + "\n");
  }

  /**
   * A progress event. JSON mode: one NDJSON line on stderr (stdout stays the single final JSON object), so
   * agents with short command timeouts learn the job id + resume command immediately. Human mode: `human` on stderr.
   */
  event(data: Record<string, unknown>, human?: string) {
    if (this.json) this.io.stderr(JSON.stringify(data) + "\n");
    else if (human) this.io.stderr(human + "\n");
  }

  /** Final success: one JSON object `{status:"ok", ...data, next[]}` or human text. */
  ok(data: Record<string, unknown>, human: string | (() => string), next: string[] = []) {
    const safeNext = next.map(redact);
    if (this.json) {
      this.io.stdout(JSON.stringify({ status: "ok", ...data, ...(safeNext.length ? { next: safeNext } : {}) }, null, 2) + "\n");
      return;
    }
    const text = typeof human === "function" ? human() : human;
    if (text) this.io.stdout(text.endsWith("\n") ? text : text + "\n");
    if (safeNext.length) this.io.stdout(`\nNext:\n${safeNext.map((n) => `  • ${n}`).join("\n")}\n`);
  }

  /** Map any error to `{status:"error", reason, message, hint, next[]}` and an exit code. */
  fail(e: unknown): number {
    let code = "error", message = String(e), hint: string | undefined, next: string[] = [], exit: number = EXIT.error, extra = {};
    if (e instanceof CliError) {
      ({ code, message, hint, next, exitCode: exit, extra } = e);
    } else if (e instanceof RendaApiError) {
      code = e.code;
      message = e.message;
      hint = e.hint;
      exit = exitCodeFor(e);
      if (exit === EXIT.auth) next = ["renda trial   # no signup, $0 credit", "renda login   # browser sign-in"];
      if (exit === EXIT.payment) next = ["renda billing   # prints a checkout link for the human to buy credit"];
    } else if (e instanceof UsageError) {
      code = "usage";
      message = e.message;
      exit = EXIT.usage;
      hint = "Run `renda --help` for usage.";
    } else if (e instanceof LayerError) {
      code = e.code;
      message = e.message;
      hint = "Fix that field in that layer; `renda flatten <pkg> --provenance` shows how layers combine.";
      extra = { layer: e.layer, ...(e.field ? { field: e.field } : {}) };
    } else if (e instanceof Error) {
      message = e.message;
    }
    message = redact(message);
    hint = hint && redact(hint);
    next = next.map(redact);
    if (this.json) {
      this.io.stdout(JSON.stringify({ status: "error", reason: code, message, ...(hint ? { hint } : {}), ...(next.length ? { next } : {}), ...extra }, null, 2) + "\n");
    } else {
      this.io.stderr(`error: ${message}${hint ? `\nhint: ${hint}` : ""}${next.length ? `\nnext:\n${next.map((n) => `  • ${n}`).join("\n")}` : ""}\n`);
    }
    return exit;
  }
}
