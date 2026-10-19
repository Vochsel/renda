import { writeFileSync } from "node:fs";
import { z } from "zod";
import { RendaPackageSource, JSON_SCHEMA_ID } from "../dist/index.js";

// The authoring schema: also accepts `extends` and package references, and lets a layer omit `scene`.
// Runtime validation (parsePackage) still requires a flattened package with a scene.
const schema = z.toJSONSchema(RendaPackageSource, { io: "input", unrepresentable: "any" });
schema.$id = JSON_SCHEMA_ID;
schema.title = "Renda package";
writeFileSync(new URL("../schema/renda.v1.json", import.meta.url), JSON.stringify(schema, null, 2) + "\n");
console.log("wrote schema/renda.v1.json");
