import { WorkOS } from "@workos-inc/node";
import { env } from "./env";

let _workos: WorkOS | undefined;

export function workos(): WorkOS {
  _workos ??= new WorkOS(env("WORKOS_API_KEY"), { clientId: env("WORKOS_CLIENT_ID") });
  return _workos;
}
