import { redirect } from "next/navigation";
import { getSignInUrl } from "@workos-inc/authkit-nextjs";

export async function GET(req: Request) {
  const returnTo = new URL(req.url).searchParams.get("returnTo") ?? undefined;
  redirect(await getSignInUrl(returnTo ? { returnTo } : undefined));
}
