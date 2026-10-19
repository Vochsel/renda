import { writeFileSync } from "node:fs";
import { z } from "zod";
import { RendaPackage, JSON_SCHEMA_ID } from "../dist/index.js";

const schema = z.toJSONSchema(RendaPackage, { io: "input", unrepresentable: "any" });
schema.$id = JSON_SCHEMA_ID;
schema.title = "Renda package";
writeFileSync(new URL("../schema/renda.v1.json", import.meta.url), JSON.stringify(schema, null, 2) + "\n");
console.log("wrote schema/renda.v1.json");
