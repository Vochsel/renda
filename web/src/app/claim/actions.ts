"use server";

import { redirect } from "next/navigation";
import { withAuth } from "@workos-inc/authkit-nextjs";
import { upsertUser } from "@/lib/accounts";
import { claimTrial } from "@/lib/trials";
import { ApiError } from "@/lib/errors";

export async function claimAction(formData: FormData) {
  const { user } = await withAuth({ ensureSignedIn: true });
  const token = String(formData.get("token") ?? "");
  // Only the local user row: the claimed trial org becomes this user's workspace.
  const local = await upsertUser(user);
  try {
    await claimTrial(token, { id: local.id, workosUserId: user.id, email: user.email });
  } catch (e) {
    if (e instanceof ApiError) redirect(`/claim?result=invalid`);
    throw e;
  }
  redirect("/dashboard?claimed=1");
}
