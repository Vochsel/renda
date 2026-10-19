/** Tiny argv parser: `cmd sub pos --flag value --bool --no-wait -o dir`. No dependencies. */
export interface Parsed {
  positionals: string[];
  flags: Record<string, string | boolean>;
}

/** Flags that never take a value. */
const BOOLEAN = new Set([
  "json", "yes", "y", "trial", "wait", "no-wait", "local", "remote", "help", "h", "cancel", "transparent", "turntable", "force", "version",
]);

const ALIASES: Record<string, string> = { o: "output", y: "yes", h: "help", v: "version" };

export function parseArgs(argv: string[]): Parsed {
  const positionals: string[] = [];
  const flags: Record<string, string | boolean> = {};
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--") {
      positionals.push(...argv.slice(i + 1));
      break;
    }
    if (a.startsWith("--") || (a.startsWith("-") && a.length === 2 && !/^-\d/.test(a))) {
      let name = a.replace(/^-+/, "");
      let value: string | boolean | undefined;
      const eq = name.indexOf("=");
      if (eq >= 0) {
        value = name.slice(eq + 1);
        name = name.slice(0, eq);
      }
      name = ALIASES[name] ?? name;
      if (value === undefined) {
        if (BOOLEAN.has(name)) value = true;
        else if (i + 1 < argv.length && !argv[i + 1].startsWith("--")) value = argv[++i];
        else value = true;
      }
      if (name === "no-wait") {
        flags.wait = false;
        continue;
      }
      // repeatable flags (e.g. --agent a --agent b) collect as comma-joined
      flags[name] = typeof flags[name] === "string" && typeof value === "string" ? `${flags[name]},${value}` : value;
    } else {
      positionals.push(a);
    }
  }
  return { positionals, flags };
}

export function str(flags: Parsed["flags"], name: string): string | undefined {
  const v = flags[name];
  return typeof v === "string" ? v : undefined;
}

export function num(flags: Parsed["flags"], name: string): number | undefined {
  const v = str(flags, name);
  if (v === undefined) return undefined;
  const n = Number(v);
  if (!Number.isFinite(n)) throw new UsageError(`--${name} must be a number`);
  return n;
}

export function bool(flags: Parsed["flags"], name: string): boolean {
  return flags[name] === true || flags[name] === "true";
}

export class UsageError extends Error {}

/** `1920x1080` → [1920, 1080] */
export function parseRes(s: string): [number, number] {
  const m = s.match(/^(\d+)[xX×](\d+)$/);
  if (!m) throw new UsageError(`--res must look like 1920x1080 (got "${s}")`);
  return [Number(m[1]), Number(m[2])];
}

/** `1-120` → [1, 120]; `24` → [1, 24] */
export function parseFrames(s: string): [number, number] {
  const m = s.match(/^(\d+)(?:-(\d+))?$/);
  if (!m) throw new UsageError(`--frames must look like 1-120 (got "${s}")`);
  return m[2] ? [Number(m[1]), Number(m[2])] : [1, Number(m[1])];
}
