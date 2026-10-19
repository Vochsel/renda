import { pricingTable } from "@/lib/pricing";

export const dynamic = "force-static";

export function GET() {
  return Response.json(pricingTable());
}
