"use server";

import { and, eq, gt } from "drizzle-orm";
import { redirect } from "next/navigation";
import { db, schema } from "@/lib/db";
import { requireAccount } from "@/lib/auth";
import { rateLimit } from "@/lib/ratelimit";

const { deviceCodes } = schema;

export async function decideDevice(formData: FormData) {
  const { user, org } = await requireAccount();
  // Codes are short; stop a signed-in user from guessing other people's pending codes.
  try {
    await rateLimit("device_approve_user", user.id);
  } catch {
    redirect("/device?result=rate_limited");
  }
  const userCode = String(formData.get("userCode") ?? "").trim().toUpperCase();
  const approve = formData.get("decision") === "approve";
  const updated = await db().update(deviceCodes)
    .set(approve ? { status: "approved", orgId: org.id, userId: user.id } : { status: "denied" })
    .where(and(eq(deviceCodes.userCode, userCode), eq(deviceCodes.status, "pending"), gt(deviceCodes.expiresAt, new Date())))
    .returning();
  redirect(`/device?result=${updated.length ? (approve ? "approved" : "denied") : "invalid"}`);
}
