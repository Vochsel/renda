"use client";

import { useEffect, useRef, useState } from "react";

/** Orbitable Gaussian splat (.spz/.ply/.splat) with Spark, framed to the splat's bounds. */
export function SplatViewer({ src, fileType }: { src: string; fileType?: "spz" | "ply" | "splat" }) {
  const host = useRef<HTMLDivElement>(null);
  const [state, setState] = useState<"loading" | "ready" | "unsupported" | "error">("loading");

  useEffect(() => {
    if (!document.createElement("canvas").getContext("webgl2")) {
      queueMicrotask(() => setState("unsupported"));
      return;
    }
    let dispose: (() => void) | undefined;
    let cancelled = false;
    (async () => {
      const THREE = await import("three");
      const { OrbitControls } = await import("three/addons/controls/OrbitControls.js");
      const { SparkRenderer, SplatMesh } = await import("@sparkjsdev/spark");
      const el = host.current;
      if (!el || cancelled) return;
      const renderer = new THREE.WebGLRenderer({ antialias: false, alpha: true });
      renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.75));
      renderer.outputColorSpace = THREE.SRGBColorSpace;
      el.appendChild(renderer.domElement);
      renderer.domElement.style.cssText = "width:100%;height:100%;display:block;touch-action:none";
      const scene = new THREE.Scene();
      const camera = new THREE.PerspectiveCamera(35, 1, 0.01, 1000);
      const controls = new OrbitControls(camera, renderer.domElement);
      controls.enableDamping = true;
      scene.add(new SparkRenderer({ renderer }));
      // Presigned URLs end in ?X-Amz-…, so tell Spark the format instead of letting it guess from the URL.
      const splat = new SplatMesh({ url: src, ...(fileType ? { fileType: fileType as never } : {}) });
      scene.add(splat);
      await splat.initialized;
      if (cancelled) return;
      const box = splat.getBoundingBox(true);
      const center = box.getCenter(new THREE.Vector3());
      const radius = Math.max(box.getSize(new THREE.Vector3()).length() / 2, 1e-3);
      const dist = (radius / Math.sin(THREE.MathUtils.degToRad(camera.fov / 2))) * 0.95;
      camera.position.copy(center).addScaledVector(new THREE.Vector3(1, 0.55, 1.3).normalize(), dist);
      camera.near = dist / 100;
      camera.far = dist * 20;
      camera.updateProjectionMatrix();
      controls.target.copy(center);
      const resize = () => {
        renderer.setSize(el.clientWidth, el.clientHeight, false);
        camera.aspect = el.clientWidth / Math.max(el.clientHeight, 1);
        camera.updateProjectionMatrix();
      };
      resize();
      const ro = new ResizeObserver(resize);
      ro.observe(el);
      renderer.setAnimationLoop(() => {
        controls.update();
        renderer.render(scene, camera);
      });
      setState("ready");
      dispose = () => {
        renderer.setAnimationLoop(null);
        ro.disconnect();
        splat.dispose();
        renderer.dispose();
        renderer.domElement.remove();
      };
    })().catch((err) => {
      console.error("splat viewer", err);
      if (!cancelled) setState("error");
    });
    return () => {
      cancelled = true;
      dispose?.();
    };
  }, [src, fileType]);

  return (
    <div className="relative h-full w-full">
      <div ref={host} className="absolute inset-0" />
      {state !== "ready" && (
        <p className="text-muted-foreground absolute inset-0 grid place-items-center text-xs">
          {state === "loading" ? "Loading splat…" : state === "unsupported" ? "Needs WebGL2 to view the splat." : "Couldn't load the splat."}
        </p>
      )}
    </div>
  );
}
