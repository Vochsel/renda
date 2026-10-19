# Credits — public showcase assets

Renders on the Renda homepage are made from these third-party assets. They are **not** committed to
this repo; `fetch.py` downloads them into `assets/` (gitignored). Each license allows redistributing
renders with attribution.

| Asset | Author | Source | License | Used in |
|---|---|---|---|---|
| FREE 1975 Porsche 911 (930) Turbo | Lionsharp Studios | [Sketchfab](https://sketchfab.com/models/8568d9d14a994b9cae59499f0dbed21e) | CC BY 4.0 | `porsche-cycles` |
| Cyber Samurai | KhoaMinh | [Sketchfab](https://sketchfab.com/models/26ccafaddb2745ceb56ae5cfc65bfed5) | CC BY 4.0 | `samurai-eevee` |
| Canon AT-1 Retro Camera | AleixoAlonso | [Sketchfab](https://sketchfab.com/models/9de66868d0f240e985da00c9480bfc82) | CC BY 4.0 | `camera-mitsuba` |
| The Noble Craftsman | olmopotums | [Sketchfab](https://sketchfab.com/models/0e8ff87ffaa24731b2474badebca870d) | CC BY 4.0 | `craftsman-threejs` |
| Venus de Milo (Aphrodite of Milos) | SMK – National Gallery of Denmark | [Sketchfab](https://sketchfab.com/models/53082b5d6cef4c34a9701a2a24f58075) | CC0 1.0 | `venus-turntable` |
| Monstera Deliciosa Potted Mid-Century plant | Giora | [Sketchfab](https://sketchfab.com/models/1ab9bf841df04c07b1819be596327629) | CC BY 4.0 | `courtyard-mixed-cycles` |
| SA3D_R&D_XP47 (courtyard apartment) | Stephane Agullo ([sa3d.fr](https://sa3d.fr)) | [SuperSplat](https://superspl.at/view?id=cdcec084), via [PlayCanvas engine examples](https://github.com/playcanvas/engine/tree/main/examples/assets/splats) | CC BY 4.0 | `courtyard-mixed-cycles` |
| Knock Community Hall | scbenoit | [SuperSplat](https://superspl.at/scene/0ff2e6dc), via PlayCanvas engine examples | CC BY 4.0 | `hall-turntable` |
| Horned lizard | Niantic | [nianticlabs/spz `samples/`](https://github.com/nianticlabs/spz) | MIT (repository license; no separate asset license) | `lizard-gsplat` |
| Raccoon family | Niantic | [nianticlabs/spz `samples/`](https://github.com/nianticlabs/spz) | MIT (repository license; no separate asset license) | `raccoon-gsplat` |

Notes
- The two SuperSplat captures ship as PlayCanvas `.sog`; `fetch.py` converts them to `.spz` with
  [`@playcanvas/splat-transform`](https://www.npmjs.com/package/@playcanvas/splat-transform). The converted
  files keep COLMAP axes, so their packages set `"upAxis": "-Y"`.
- Renders are unretouched output of the packages in this directory, made on the production pipeline.
