import Link from "next/link";
import { cn } from "@/lib/utils";
import { PRIVACY_PATH, TERMS_PATH } from "@/lib/legal";

/** "By continuing you agree to the Terms and Privacy Policy", shown next to every button that creates or takes over an account. */
export function TermsNotice({ action = "continuing", className }: { action?: string; className?: string }) {
  return (
    <p className={cn("text-muted-foreground text-xs", className)}>
      By {action} you agree to the{" "}
      <Link href={TERMS_PATH} className="hover:text-foreground underline underline-offset-2">Terms</Link> and{" "}
      <Link href={PRIVACY_PATH} className="hover:text-foreground underline underline-offset-2">Privacy Policy</Link>.
    </p>
  );
}
