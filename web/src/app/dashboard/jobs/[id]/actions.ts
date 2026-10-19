"use server";

import { revalidatePath } from "next/cache";
import { requireAccount } from "@/lib/auth";
import { cancelJob } from "@/lib/jobs";

export async function cancelJobAction(jobId: string) {
  const { org } = await requireAccount();
  await cancelJob(org.id, jobId).catch(() => {});
  revalidatePath(`/dashboard/jobs/${jobId}`);
}
