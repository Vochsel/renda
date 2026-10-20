/** Dev-only stand-in for the presigned R2 PUT (404 in production). */
export async function PUT(req: Request) {
  if (process.env.NODE_ENV === "production") return new Response(null, { status: 404 });
  await req.arrayBuffer();
  return new Response(null, { status: 200 });
}
