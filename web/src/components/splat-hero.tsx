"use client";

import { useEffect, useRef, useState } from "react";

/**
 * Homepage hero: the Porsche baked into a Gaussian splat by `renda splat --preset high` (production job, 2026-10-20), shown
 * with Spark. Slow orbit until the visitor grabs it. Bounds come from the core loader so we don't load the mesh.
 */
const HERO = {
  src: "/splats/porsche-hero-v2.spz",
  center: [0.0, 0.78, 0.22] as const,
  radius: 2.55,
};

export function SplatHero() {
  const box = useRef<HTMLDivElement>(null);
  const [state, setState] = useState<"idle" | "loading" | "ready" | "unsupported" | "error">("idle");

  useEffect(() => {
    const host = box.current;
    if (!host) return;
    let dispose: (() => void) | undefined;
    let cancelled = false;
    (async () => {
      if (!document.createElement("canvas").getContext("webgl2")) return setState("unsupported");
      const THREE = await import("three");
      const { OrbitControls } = await import("three/addons/controls/OrbitControls.js");
      const { SparkRenderer, SplatMesh } = await import("@sparkjsdev/spark");
      if (cancelled) return;

      const renderer = new THREE.WebGLRenderer({ antialias: false, alpha: true });
      renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.75));
      renderer.outputColorSpace = THREE.SRGBColorSpace;
      host.appendChild(renderer.domElement);
      renderer.domElement.style.cssText = "width:100%;height:100%;display:block;touch-action:pan-y";

      const scene = new THREE.Scene();
      const camera = new THREE.PerspectiveCamera(28, 1, 0.01, 1000);
      const controls = new OrbitControls(camera, renderer.domElement);
      controls.enableDamping = true;
      controls.dampingFactor = 0.08;
      controls.enableZoom = false; // don't hijack page scroll
      controls.enablePan = false;
      controls.autoRotate = true;
      controls.autoRotateSpeed = 0.6;
      controls.minPolarAngle = Math.PI * 0.2;
      controls.maxPolarAngle = Math.PI * 0.49;
      const stop = () => (controls.autoRotate = false);
      renderer.domElement.addEventListener("pointerdown", stop, { once: true });

      scene.add(new SparkRenderer({ renderer }));
      const splat = new SplatMesh({ url: HERO.src });
      scene.add(splat);
      splat.initialized.then(() => !cancelled && setState("ready"));

      const center = new THREE.Vector3(...HERO.center);
      const dist = (HERO.radius / Math.sin(THREE.MathUtils.degToRad(camera.fov / 2))) * 0.74;
      camera.position.copy(center).addScaledVector(new THREE.Vector3(0.95, 0.32, 1).normalize(), dist);
      controls.target.copy(center);
      controls.update();

      let visible = true;
      const resize = () => {
        const w = host.clientWidth;
        const h = host.clientHeight;
        renderer.setSize(w, h, false);
        camera.aspect = w / h;
        camera.updateProjectionMatrix();
      };
      const ro = new ResizeObserver(resize);
      ro.observe(host);
      resize();
      const vis = new IntersectionObserver(([e]) => (visible = e.isIntersecting));
      vis.observe(host);
      renderer.setAnimationLoop(() => {
        if (!visible) return;
        controls.update();
        renderer.render(scene, camera);
      });

      dispose = () => {
        renderer.setAnimationLoop(null);
        ro.disconnect();
        vis.disconnect();
        controls.dispose();
        splat.dispose();
        renderer.dispose();
        renderer.domElement.remove();
      };
    })().catch((err) => {
      console.error("splat hero", err);
      setState("error");
    });

    return () => {
      cancelled = true;
      dispose?.();
    };
  }, []);

  return (
    <figure>
      <div ref={box} className="relative w-full" style={{ aspectRatio: "16 / 8" }} data-splat-hero>
        {state !== "ready" && (
          <p className="text-muted-foreground absolute inset-0 grid place-items-center font-mono text-xs">
            {state === "unsupported" ? "Needs WebGL2 to view the splat." : state === "error" ? "Couldn't load the splat." : "loading splat…"}
          </p>
        )}
      </div>
      <figcaption className="text-muted-foreground mt-2 font-mono text-[11px] tracking-tight">
        Gaussian splat baked from a .glb by <span className="text-foreground">renda splat</span> · 200 Cycles views · 267k Gaussians · SH 3 · PSNR 35.8 dB ·
        589 s billed · ≈$0.61 · drag to orbit ·{" "}
        <a href="https://sketchfab.com/models/8568d9d14a994b9cae59499f0dbed21e" className="hover:text-foreground underline-offset-2 hover:underline">
          1975 Porsche 911 Turbo
        </a>{" "}
        by Lionsharp Studios · CC BY 4.0
      </figcaption>
    </figure>
  );
}
