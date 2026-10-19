// Copy ../skills/<name>/ into cli/skills/ so `renda init` can install skills offline.
import { cpSync, existsSync, readdirSync, rmSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
const src = fileURLToPath(new URL("../../skills/", import.meta.url));
const dst = fileURLToPath(new URL("../skills/", import.meta.url));
rmSync(dst, { recursive: true, force: true });
const names = readdirSync(src, { withFileTypes: true })
  .filter((d) => d.isDirectory() && existsSync(join(src, d.name, "SKILL.md")))
  .map((d) => d.name);
for (const name of names) cpSync(join(src, name), join(dst, name), { recursive: true });
console.log(`bundled ${names.length} skills into cli/skills/`);
