import { neon } from "@neondatabase/serverless";
import { drizzle, type NeonHttpDatabase } from "drizzle-orm/neon-http";
import { env } from "../env";
import * as schema from "./schema";

let _db: NeonHttpDatabase<typeof schema> | undefined;

/** Lazily-created Drizzle client (no connection at import/build time). */
export function db(): NeonHttpDatabase<typeof schema> {
  _db ??= drizzle(neon(env("DATABASE_URL")), { schema });
  return _db;
}

export { schema };
