# Example packages

## Quick draft of any file
```json
{ "version": 1, "scene": { "root": "./model.glb" }, "render": { "quality": "draft" } }
```

## Product hero still, transparent, photoreal
```json
{
  "version": 1,
  "name": "headphones-hero",
  "assets": { "hp": { "uri": "./headphones.usdz" }, "sky": { "uri": "./studio_small_09_4k.hdr" } },
  "scene": { "root": "hp" },
  "camera": { "mode": "auto", "fovDeg": 30, "elevationDeg": 12, "azimuthDeg": 40, "padding": 0.15 },
  "environment": { "hdri": "sky", "intensity": 1.1, "rotationDeg": 120, "background": "transparent" },
  "render": { "engine": "blender-cycles", "resolution": [3000, 3000], "quality": "final" },
  "output": { "format": "png", "transparent": true }
}
```

## STL print preview (mm, Z-up, coloured)
```json
{
  "version": 1,
  "scene": {
    "root": "./bracket.stl", "upAxis": "Z", "unitScale": 0.001,
    "materialOverrides": { "*": { "baseColor": "#f97316", "roughness": 0.55 } }
  },
  "lights": [{ "type": "sun", "direction": [-1, -1, -2], "intensity": 3 }],
  "environment": { "hdri": "studio", "background": "#f5f5f4" },
  "render": { "quality": "draft", "resolution": [1200, 900] },
  "output": { "format": "jpg" }
}
```

## Blender file with the artist's camera
```json
{
  "version": 1,
  "scene": { "root": "./shot_010.blend" },
  "camera": { "mode": "scene", "name": "CAM_main" },
  "render": { "engine": "blender-cycles", "quality": "final", "resolution": [3840, 2160] },
  "output": { "format": "exr", "passes": ["beauty", "depth", "normal"] }
}
```
(The `.blend`'s own world is used unless you set `environment.hdri` explicitly.)

## Same .blend in Mitsuba
```json
{
  "version": 1,
  "scene": { "root": "./teapot.blend" },
  "camera": { "mode": "scene" },
  "render": { "engine": "mitsuba", "quality": "standard", "resolution": [1600, 1200] }
}
```

## three.js scene exported with `scene.toJSON()`
```json
{
  "version": 1,
  "assets": { "scene": { "uri": "./scene.json", "format": "threejs" } },
  "scene": { "root": "scene", "upAxis": "Y" },
  "camera": { "mode": "scene" },
  "render": { "engine": "threejs", "resolution": [1280, 720], "quality": "draft" },
  "output": { "format": "webp" }
}
```

## Exact camera with lookat
```json
{
  "version": 1,
  "scene": { "root": "./room.glb" },
  "camera": { "mode": "lookat", "position": [4.2, 1.6, 3.1], "target": [0, 1, 0], "fovDeg": 50 },
  "render": { "engine": "blender-eevee", "quality": "standard" }
}
```

## Turntable MP4
```json
{
  "version": 1,
  "scene": { "root": "./sneaker.glb" },
  "camera": { "mode": "auto", "elevationDeg": 15 },
  "environment": { "hdri": "studio", "background": "#f4f4f2" },
  "render": { "engine": "blender-eevee", "resolution": [1080, 1080] },
  "animation": { "frames": [1, 120], "fps": 30, "turntable": { "revolutions": 1 } },
  "output": { "format": "mp4", "video": { "codec": "h264", "crf": 20 } }
}
```

## Scene assembly
```json
{
  "version": 1,
  "assets": {
    "car": { "uri": "./car.fbx" },
    "floor": { "uri": "https://example.com/assets/floor_tile.glb" }
  },
  "scene": {
    "root": "car",
    "include": [{ "asset": "floor", "transform": { "scale": 20 } }],
    "unitScale": 0.01
  },
  "render": { "engine": "blender-cycles", "quality": "standard" }
}
```
