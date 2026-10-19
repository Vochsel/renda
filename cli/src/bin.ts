#!/usr/bin/env node
import { run } from "./cli.js";

const code = await run(process.argv.slice(2));
// `renda mcp` keeps running (stdio server); everything else exits with its code.
if (!(process.argv[2] === "mcp" && (process.argv.length === 3 || process.argv[3] === "serve"))) process.exit(code);
