import type { Metadata } from "next";
import schema from "@rendahq/spec/schema.json";
import minimal from "../../../../spec/examples/minimal.renda.json";
import hero from "../../../../spec/examples/hero-still.renda.json";
import turntable from "../../../../spec/examples/turntable.renda.json";
import threejs from "../../../../spec/examples/threejs-scene.renda.json";
import splatRoom from "../../../../spec/examples/splat-room-mixed.renda.json";
import stl from "../../../../spec/examples/stl-print-preview.renda.json";
import lensEffects from "../../../../spec/examples/lens-effects.renda.json";
import { SiteHeader } from "@/components/site-header";
import { installSnippets } from "@/lib/install-snippets";
import { Code } from "@/components/code";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

export const metadata: Metadata = { title: "Docs" };

const EXAMPLES = { minimal, "hero still": hero, turntable, "three.js": threejs, "stl preview": stl, "splats + mesh": splatRoom, "lens + post": lensEffects };

/** Mirrors core/renda/effects.py MATRIX. */
const EFFECTS: [string, ...string[]][] = [
  ["Depth of field", "native", "native", "native", "native (thin lens)", "emulated (depth)", "emulated (depth)"],
  ["Bokeh blades / anamorphic", "native", "native", "native", "round only", "emulated", "emulated"],
  ["Antialias filter", "native", "native", "native", "native", "approx.", "built-in"],
  ["Motion blur", "native", "native", "emulated", "emulated (same cost)", "emulated", "emulated"],
  ["Exposure", "native", "native", "native", "native", "native", "display colours"],
  ["Tonemap", "aces → filmic", "aces → filmic", "aces → filmic", "filmic approx.", "filmic approx.", "n/a"],
  ["Post (grade, vignette, CA, distortion, bloom, grain, sharpen)", "shared", "shared", "shared", "shared", "shared", "shared"],
];

type JsonSchema = { type?: string | string[]; description?: string; properties?: Record<string, JsonSchema>; enum?: unknown[]; anyOf?: JsonSchema[]; oneOf?: JsonSchema[]; default?: unknown; $ref?: string; items?: JsonSchema; required?: string[] };

function typeOf(s: JsonSchema): string {
  if (s.enum) return s.enum.map((e) => JSON.stringify(e)).join(" | ");
  if (s.anyOf ?? s.oneOf) return (s.anyOf ?? s.oneOf)!.map(typeOf).join(" | ");
  if (s.type === "array") return `${s.items ? typeOf(s.items) : "any"}[]`;
  if (Array.isArray(s.type)) return s.type.join(" | ");
  return s.type ?? "object";
}

/** Flatten the generated JSON Schema into dotted field rows (two levels deep). */
function rows(s: JsonSchema, prefix = "", depth = 0): { path: string; type: string; def?: string; desc?: string }[] {
  const out: { path: string; type: string; def?: string; desc?: string }[] = [];
  for (const [k, v] of Object.entries(s.properties ?? {})) {
    if (k === "$schema") continue;
    const path = prefix ? `${prefix}.${k}` : k;
    const isObj = v.properties && depth < 2;
    out.push({ path, type: isObj ? "object" : typeOf(v), def: v.default !== undefined && !isObj ? JSON.stringify(v.default) : undefined, desc: v.description });
    if (isObj) out.push(...rows(v, path, depth + 1));
  }
  return out;
}

export default function DocsPage() {
  const fields = rows(schema as unknown as JsonSchema);
  return (
    <>
      <SiteHeader />
      <main className="mx-auto w-full max-w-5xl flex-1 px-4 py-12">
        <h1 className="text-3xl font-semibold tracking-tight">Get started</h1>
        <p className="text-muted-foreground mt-3 max-w-3xl">
          Same account and commands from Node or Python. Agents: paste <span className="font-mono">{installSnippets.agentPrompt}</span>.
        </p>
        <Tabs defaultValue="npx" className="mt-4">
          <TabsList>
            <TabsTrigger value="npx">npx</TabsTrigger>
            <TabsTrigger value="uvx">uvx</TabsTrigger>
            <TabsTrigger value="mcp">MCP</TabsTrigger>
          </TabsList>
          <TabsContent value="npx"><Code>{installSnippets.npx.join("\n")}</Code></TabsContent>
          <TabsContent value="uvx"><Code>{installSnippets.uvx.join("\n")}</Code></TabsContent>
          <TabsContent value="mcp"><Code>{[installSnippets.mcp.claudeCode, `# remote: ${installSnippets.mcp.remoteUrl} (Authorization: Bearer <key>)`, installSnippets.skills, ...installSnippets.plugin].join("\n")}</Code></TabsContent>
        </Tabs>

        <h1 className="mt-14 text-3xl font-semibold tracking-tight">The Renda package</h1>
        <p className="text-muted-foreground mt-3 max-w-3xl">
          A small typed JSON file (<span className="font-mono">*.renda.json</span>) for what scene formats can&apos;t express: output, resolution,
          environment map, camera, animation, engine. Everything except <span className="font-mono">scene.root</span> is optional. JSON Schema:{" "}
          <a className="underline" href="/api/v1/schema.json">/api/v1/schema.json</a>.
        </p>

        <h2 className="mt-10 text-lg font-semibold">Examples</h2>
        <Tabs defaultValue="minimal" className="mt-3">
          <TabsList>
            {Object.keys(EXAMPLES).map((k) => <TabsTrigger key={k} value={k}>{k}</TabsTrigger>)}
          </TabsList>
          {Object.entries(EXAMPLES).map(([k, v]) => (
            <TabsContent key={k} value={k}><Code>{JSON.stringify(v, null, 2)}</Code></TabsContent>
          ))}
        </Tabs>

        <h2 className="mt-10 text-lg font-semibold">Uploading and rendering</h2>
        <Code className="mt-3">{`# 1. register each file by sha256; PUT bytes if the server doesn't have them
curl -X POST $RENDA/api/v1/uploads -H "authorization: Bearer $KEY" \\
  -d '{"filename":"robot.glb","size":48213,"sha256":"<hex>"}'
# → {"assetUri":"asset://<hex>","exists":false,"uploadUrl":"https://…"}
curl -X PUT --data-binary @robot.glb -H "content-type: application/octet-stream" "<uploadUrl>"

# 2. submit, using asset:// uris
curl -X POST $RENDA/api/v1/jobs -H "authorization: Bearer $KEY" \\
  -d '{"package":{"scene":{"root":"asset://<hex>"},"output":{"format":"png"}}}'

# 3. poll until status is succeeded, then download outputs[].url
curl $RENDA/api/v1/jobs/<id> -H "authorization: Bearer $KEY"`}</Code>
        <p className="text-muted-foreground mt-2 text-sm">The <span className="font-mono">renda</span> CLI and MCP server do the upload step for you. Full API: <a className="underline" href="/api/v1/openapi.json">OpenAPI</a>.</p>

        <h2 className="mt-10 text-lg font-semibold">Lens and camera effects</h2>
        <p className="text-muted-foreground mt-3 max-w-3xl text-sm">
          <span className="font-mono">camera.lens</span> (focal length, f-stop for depth of field, focus, bokeh),{" "}
          <span className="font-mono">render.antialias</span>, <span className="font-mono">render.motionBlur</span> (needs an animation) and{" "}
          <span className="font-mono">post</span> work on every engine: natively where the engine can, emulated where it can&apos;t (job warnings
          say which). Post effects are one shared stage, so they look identical across engines. Emulated motion blur costs{" "}
          <span className="font-mono">subframes</span> × the frame time.
        </p>
        <Table className="mt-3">
          <TableHeader>
            <TableRow>
              <TableHead />
              {["cycles", "eevee", "workbench", "mitsuba", "threejs", "gsplat"].map((e) => <TableHead key={e} className="font-mono text-xs">{e}</TableHead>)}
            </TableRow>
          </TableHeader>
          <TableBody>
            {EFFECTS.map(([name, ...cells]) => (
              <TableRow key={name}>
                <TableCell className="text-xs">{name}</TableCell>
                {cells.map((c, i) => <TableCell key={i} className="text-muted-foreground text-xs">{c}</TableCell>)}
              </TableRow>
            ))}
          </TableBody>
        </Table>

        <h2 className="mt-10 text-lg font-semibold">Fields</h2>
        <Table className="mt-3">
          <TableHeader>
            <TableRow><TableHead>Field</TableHead><TableHead>Type</TableHead><TableHead>Default</TableHead><TableHead>Notes</TableHead></TableRow>
          </TableHeader>
          <TableBody>
            {fields.map((f) => (
              <TableRow key={f.path}>
                <TableCell className="font-mono text-xs">{f.path}</TableCell>
                <TableCell className="text-muted-foreground max-w-64 truncate font-mono text-xs" title={f.type}>{f.type}</TableCell>
                <TableCell className="font-mono text-xs">{f.def}</TableCell>
                <TableCell className="text-muted-foreground text-xs">{f.desc}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </main>
    </>
  );
}
