import { db, schema } from "@/lib/db";
import { api } from "@/lib/http";
import { appUrl } from "@/lib/env";
import { generateDeviceCode, generateUserCode, hashApiKey } from "@/lib/apikeys";

const EXPIRES_IN = 900;
const INTERVAL = 5;

/** OAuth-style device flow for CLIs: show userCode + verificationUri to the human, poll /auth/device/token. */
export const POST = api(async () => {
  const deviceCode = generateDeviceCode();
  const userCode = generateUserCode();
  await db().insert(schema.deviceCodes).values({
    deviceCodeHash: hashApiKey(deviceCode),
    userCode,
    expiresAt: new Date(Date.now() + EXPIRES_IN * 1000),
  });
  const verificationUri = `${appUrl()}/device`;
  return Response.json({
    deviceCode,
    userCode,
    verificationUri,
    verificationUriComplete: `${verificationUri}?code=${userCode}`,
    interval: INTERVAL,
    expiresIn: EXPIRES_IN,
  });
});
