/** Short package guide served as the `renda://docs/package` resource. Keep in sync with skills/renda-package. */
export const PACKAGE_GUIDE = `# Renda package (*.renda.json) — quick guide

A Renda package is typed JSON that says *how* to render a scene file: camera, lighting,
resolution, engine, output format, animation. Only \`scene.root\` is required.

\`\`\`json
{
  "version": 1,
  "assets": { "model": { "uri": "./model.glb" }, "sky": { "uri": "./studio.hdr" } },
  "scene": { "root": "model", "upAxis": "Y", "materialOverrides": { "*": { "roughness": 0.4 } } },
  "camera": { "mode": "auto", "fovDeg": 35, "elevationDeg": 20, "azimuthDeg": 35, "padding": 0.1 },
  "environment": { "hdri": "sky", "intensity": 1, "background": "hdri" },
  "render": { "engine": "auto", "resolution": [1920, 1080], "quality": "standard" },
  "output": { "format": "png" }
}
\`\`\`

## Fields
- assets: id → { uri, format?, sha256? }. uri = relative path, https://…, or asset://<sha256>.
  Local paths are uploaded and rewritten to asset:// automatically by renda_render.
- scene: root (asset id or uri), include[] ({asset, transform{translate,rotateDeg,scale}}),
  upAxis "Y"|"Z", unitScale, materialOverrides { name|"*": {baseColor, metallic, roughness,
  emission, emissionStrength, opacity, transmission, ior} }.
- camera: mode "auto" (frames bounds; fovDeg, elevationDeg, azimuthDeg, padding) |
  "scene" (name? — use a camera from the file) | "lookat" (position, target, up?, fovDeg).
- environment: hdri (asset id/uri or built-in "studio" | "sunset" | "night"), intensity,
  rotationDeg, background "hdri" | "transparent" | "#rrggbb".
- lights[]: sun{direction} | point{position} | spot{position,target,angleDeg} | area{position,target,size}.
- render: engine auto | blender-cycles | blender-eevee | blender-workbench | mitsuba | threejs | gsplat;
  resolution [w,h]; quality draft | standard | final; samples (overrides quality); denoise; seed;
  hardware auto | cpu | gpu.
- animation: frames [start,end] inclusive, fps, step, turntable {revolutions, axis}.
- output: format png | jpg | webp | exr | mp4 | webm | gif | mov | glb; passes [beauty, alpha, depth,
  normal, albedo, mask]; transparent; video {codec h264|h265|vp9|prores|gif, crf}; keepFrames.

## Rules
- Video formats need \`animation\`. jpg/mp4/gif can't be transparent.
- glb: plain conversion; add \`bake: {mode: unlit|ao|lightmap, size: 2048, uvs: auto|existing}\` to bake the environment + lights into textures (engines blender-cycles | mitsuba | threejs; use the renda_bake tool).
- Formats: blend, usd/usda/usdc/usdz, gltf/glb, three.js JSON (.json, ObjectLoader format 4),
  fbx, obj(+mtl), stl, ply, dae, 3mf, abc, and Gaussian splats: 3DGS .ply, .splat, .spz (.ksplat: threejs only).
- Gaussian splats: splat-only scenes → engine "gsplat" (CUDA, cheapest; auto picks it). Mix splats with
  meshes via scene.include (e.g. a product .glb inside a captured .ply room): threejs renders both natively
  (Spark); blender-*/mitsuba render the meshes and composite the splats by depth (splats keep their baked
  lighting — no shadows/reflections from the engine). Splat .ply/.splat captures use COLMAP axes: upAxis
  defaults to "-Y" (Y down); set scene.upAxis "Y" if a render comes out upside down. .spz is Y-up.
- Unsupported fields for the chosen engine become job warnings, never silent drops.
- Render a "draft" first (cheap), check it, then "final".
`;
