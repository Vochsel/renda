import { redirect } from "next/navigation";
import { TERMS_PATH } from "@/lib/legal";

export default function LegalIndex() {
  redirect(TERMS_PATH);
}
