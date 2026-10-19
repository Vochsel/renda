import { signOut } from "@workos-inc/authkit-nextjs";

export async function POST() {
  await signOut({ returnTo: "/" });
}
