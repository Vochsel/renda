import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { parsePackage } from "@rendahq/spec";
import { estimateScene, type Priority } from "@/lib/estimate";

type Case = {
  package: unknown;
  priority: Priority;
  engine: string;
  hardware: string;
  chunks: number;
  estSeconds: [number, number];
  estWallSeconds: [number, number];
};

const fixture = JSON.parse(readFileSync(join(__dirname, "../../spec/fixtures/estimator/hardware.json"), "utf8")) as { cases: Case[] };

// Core rounds with Python's round(); allow a hair for that and float noise.
const close = (a: number, b: number) => Math.abs(a - b) <= Math.max(0.11, Math.abs(b) * 0.005);

describe("estimateScene reproduces the core planner (spec/fixtures/estimator/hardware.json)", () => {
  it(`matches engine, hardware, chunks and seconds for all ${fixture.cases.length} cases`, () => {
    const misses: string[] = [];
    for (const c of fixture.cases) {
      const e = estimateScene(parsePackage(c.package), c.priority);
      const ok = e.engine === c.engine && e.hardware === c.hardware && e.chunks === c.chunks
        && close(e.estSeconds[0], c.estSeconds[0]) && close(e.estSeconds[1], c.estSeconds[1])
        && close(e.estWallSeconds[0], c.estWallSeconds[0]) && close(e.estWallSeconds[1], c.estWallSeconds[1]);
      if (!ok) {
        misses.push(JSON.stringify({
          want: [c.engine, c.hardware, c.chunks, c.estSeconds, c.estWallSeconds],
          got: [e.engine, e.hardware, e.chunks, e.estSeconds, e.estWallSeconds],
          pkg: c.package, priority: c.priority,
        }));
      }
    }
    expect(misses.slice(0, 5)).toEqual([]);
  });

  it("prices a small Cycles still on the GPU it actually runs on (regression: was estimated on CPU)", () => {
    const pkg = parsePackage({ version: 1, scene: { root: "./m.glb" }, render: { engine: "blender-cycles", resolution: [640, 480] } });
    expect(estimateScene(pkg).hardware).toBe("gpu-l4");
  });
});
