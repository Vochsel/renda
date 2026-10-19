import schema from "@rendahq/spec/schema.json";

export const dynamic = "force-static";

export function GET() {
  return Response.json(schema);
}
