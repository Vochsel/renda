# @rendahq/spec

The typed schema for Renda render packages (`*.renda.json`): Zod schemas, TypeScript types and a JSON Schema.

```ts
import { safeParsePackage } from "@rendahq/spec";

const r = safeParsePackage(JSON.parse(text));
if (!r.success) console.error(r.error.issues);
```

The JSON Schema ships at `@rendahq/spec/schema.json` (file `schema/renda.v1.json`). A Python mirror lives in the
[`renda`](https://pypi.org/project/renda/) package (`renda.spec`).

Docs: https://renda.vochsel.com · Source: https://github.com/Vochsel/renda · Apache-2.0
