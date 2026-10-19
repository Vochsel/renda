"use client";

import { useEffect, useRef, useState } from "react";
import { SPLAT_DEMOS, type SplatDemo as Demo } from "@/lib/splat-demos";
import { cn } from "@/lib/utils";

type Mode = "mesh" | "splat";

function mb(bytes: number) {
  return bytes >= 1e6 ? `${(bytes / 1e6).toFixed(1)} MB` : `${Math.round(bytes / 1e3)} KB`;
}

/**
 * One three.js scene holding both the source mesh and the baked splat, with a single camera and
 * OrbitControls. Switching Mesh/Splat only flips visibility, so the viewpoint never changes.
 */
function SplatCard({ demo }: { demo: Demo }) {
  const box = useRef<HTMLDivElement>(null);
  const canvasHost = useRef<HTMLDivElement>(null);
  const setModeRef = useRef<(m: Mode) => void>(() => {});
  const [mode, setMode] = useState<Mode>("splat");
  const [state, setState] = useState<"idle" | "loading" | "ready" | "unsupported" | "error">("idle");
  const [loaded, setLoaded] = useState<Record<Mode, boolean>>({ mesh: false, splat: false });

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    let dispose: (() => void) | undefined;
    let cancelled = false;
    const io = new IntersectionObserver(
      ([e]) => {
        if (!e.isIntersecting || dispose || cancelled) return;
        io.disconnect();
        const probe = document.createElement("canvas").getContext("webgl2");
        if (!probe) return setState("unsupported");
        setState("loading");
        start().then((d) => (cancelled ? d() : (dispose = d))).catch((err) => {
          console.error("splat demo", err);
          setState("error");
        });
      },
      { rootMargin: "300px" },
    );
    io.observe(el);

    async function start() {
      const THREE = await import("three");
      const { OrbitControls } = await import("three/addons/controls/OrbitControls.js");
      const { GLTFLoader } = await import("three/addons/loaders/GLTFLoader.js");
      const { SparkRenderer, SplatMesh } = await import("@sparkjsdev/spark");
      const host = canvasHost.current!;

      const renderer = new THREE.WebGLRenderer({ antialias: false, alpha: true });
      renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.75));
      renderer.outputColorSpace = THREE.SRGBColorSpace;
      renderer.toneMapping = THREE.NeutralToneMapping;
      host.appendChild(renderer.domElement);
      renderer.domElement.style.cssText = "width:100%;height:100%;display:block;touch-action:none";

      const scene = new THREE.Scene();
      const camera = new THREE.PerspectiveCamera(35, 1, 0.01, 1000);
      const controls = new OrbitControls(camera, renderer.domElement);
      controls.enableDamping = true;
      controls.dampingFactor = 0.1;

      // The mesh is shown unlit-by-baking, so give it the same neutral studio light the splat was baked under.
      const { RoomEnvironment } = await import("three/addons/environments/RoomEnvironment.js");
      const pmrem = new THREE.PMREMGenerator(renderer);
      scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;

      const spark = new SparkRenderer({ renderer });
      scene.add(spark);

      const gltf = await new GLTFLoader().loadAsync(demo.mesh);
      const mesh = gltf.scene;
      scene.add(mesh);
      setLoaded((l) => ({ ...l, mesh: true }));

      const splat = new SplatMesh({ url: demo.splat });
      scene.add(splat);
      splat.initialized.then(() => setLoaded((l) => ({ ...l, splat: true })));

      // Frame the mesh bounds (the splat was baked from it, so they share one frame).
      const bounds = new THREE.Box3().setFromObject(mesh);
      const center = bounds.getCenter(new THREE.Vector3());
      const radius = bounds.getSize(new THREE.Vector3()).length() / 2;
      const dist = radius / Math.sin(THREE.MathUtils.degToRad(camera.fov / 2)) * 0.95;
      const dir = new THREE.Vector3(1, 0.55, 1.3).normalize();
      camera.position.copy(center).addScaledVector(dir, dist);
      camera.near = dist / 100;
      camera.far = dist * 20;
      camera.updateProjectionMatrix();
      controls.target.copy(center);
      controls.update();

      // Render only while on screen.
      let visible = true;
      let dirty = true;
      const apply = (m: Mode) => {
        mesh.visible = m === "mesh";
        splat.visible = m === "splat";
        dirty = true;
      };
      setModeRef.current = apply;
      apply("splat");

      const resize = () => {
        const w = host.clientWidth;
        const h = host.clientHeight;
        renderer.setSize(w, h, false);
        camera.aspect = w / h;
        camera.updateProjectionMatrix();
        dirty = true;
      };
      const ro = new ResizeObserver(resize);
      ro.observe(host);
      resize();

      const vis = new IntersectionObserver(([e]) => (visible = e.isIntersecting));
      vis.observe(host);
      controls.addEventListener("change", () => (dirty = true));
      // Spark sorts splats asynchronously and streams them in, so it needs frames while the splat shows;
      // the mesh view only redraws on change.
      renderer.setAnimationLoop(() => {
        if (!visible) return;
        const moving = controls.update();
        if (dirty || moving || splat.visible) {
          renderer.render(scene, camera);
          dirty = false;
        }
      });
      (host as HTMLDivElement & { __renda?: unknown }).__renda = { camera, controls };
      setState("ready");

      return () => {
        renderer.setAnimationLoop(null);
        ro.disconnect();
        vis.disconnect();
        controls.dispose();
        pmrem.dispose();
        splat.dispose();
        renderer.dispose();
        renderer.domElement.remove();
      };
    }

    return () => {
      cancelled = true;
      io.disconnect();
      dispose?.();
    };
  }, [demo.mesh, demo.splat]);

  const select = (m: Mode) => {
    setMode(m);
    setModeRef.current(m);
  };

  const stats = [
    `${(demo.gaussians / 1000).toFixed(0)}k Gaussians`,
    `${demo.views} views · ${demo.steps.toLocaleString("en-US")} steps`,
    `PSNR ${demo.psnr.toFixed(1)} dB`,
    `${Math.round(demo.billedSeconds)}s billed`,
    `$${demo.costUsd.toFixed(2)}`,
    `.spz ${mb(demo.splatBytes)}`,
  ];
  return (
    <figure>
      <div ref={box} className="bg-muted relative overflow-hidden rounded-xl" style={{ aspectRatio: "4 / 3" }}>
        <div ref={canvasHost} className="absolute inset-0" data-splat-demo={demo.id} />
        {(state === "unsupported" || state === "error") && (
          <p className="text-muted-foreground absolute inset-0 grid place-items-center text-xs">
            {state === "unsupported" ? "Needs WebGL2 to view the splat." : "Couldn't load this demo."}
          </p>
        )}
        <div className="bg-background/80 absolute top-3 left-3 flex gap-0.5 rounded-md p-0.5 backdrop-blur">
          {(["mesh", "splat"] as const).map((m) => (
            <button
              key={m}
              type="button"
              onClick={() => select(m)}
              aria-pressed={m === mode}
              className={cn(
                "flex items-center gap-1 rounded px-2 py-0.5 font-mono text-[11px] capitalize transition-colors",
                m === mode ? "bg-foreground text-background" : "text-muted-foreground hover:text-foreground",
              )}
            >
              {m}
              {state === "loading" && !loaded[m] && <span className="size-1 animate-pulse rounded-full bg-current opacity-60" aria-label="loading" />}
            </button>
          ))}
        </div>
      </div>
      <figcaption className="mt-3">
        <p className="text-sm">
          <span className="font-mono text-xs">{demo.input}</span>
          <span className="text-muted-foreground"> → </span>
          Gaussian splat (.spz)
        </p>
        <p className="text-muted-foreground mt-1 font-mono text-[11px] tracking-tight">{stats.join("  ·  ")}</p>
        {demo.credit && (
          <p className="text-muted-foreground mt-0.5 truncate text-[11px]">
            <a href={demo.credit.url} className="hover:text-foreground underline-offset-2 hover:underline">
              {demo.credit.title}
            </a>{" "}
            by {demo.credit.author} · {demo.credit.license}
          </p>
        )}
      </figcaption>
    </figure>
  );
}

export function SplatDemo() {
  return (
    <div className="grid gap-x-8 gap-y-12 sm:grid-cols-2">
      {SPLAT_DEMOS.map((d) => (
        <SplatCard key={d.id} demo={d} />
      ))}
    </div>
  );
}
