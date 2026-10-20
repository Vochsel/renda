"""Thumbnails for the bake task: each run's GLB in model-viewer (same camera, neutral environment), since a plain
glTF viewer is how people look at a baked GLB.

    uv run --no-project --with playwright --with pillow python bench/agents/preview_glb.py <workdir>
"""
import http.server, threading, functools, shutil, sys
from pathlib import Path
from playwright.sync_api import sync_playwright
from PIL import Image

S = Path(sys.argv[1])
OUT = Path(__file__).resolve().parents[2] / "web" / "public" / "benchmarks" / "agents"
serve = S / "_mv"
serve.mkdir(exist_ok=True)
for c in ["claude-bare", "claude-renda", "pi-bare", "pi-renda"]:
    shutil.copy(S / f"bake-{c}" / "out" / "baked.glb", serve / f"{c}.glb")
(serve / "index.html").write_text("""<!doctype html><html><body style="margin:0;background:#888">
<script type="module" src="https://unpkg.com/@google/model-viewer@4.0.0/dist/model-viewer.min.js"></script>
<model-viewer id="mv" style="width:480px;height:360px;background:#8a8a8a" camera-orbit="35deg 70deg auto" environment-image="neutral" exposure="1" shadow-intensity="0"></model-viewer>
</body></html>""")
h = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(serve))
srv = http.server.ThreadingHTTPServer(("127.0.0.1", 8765), h)
threading.Thread(target=srv.serve_forever, daemon=True).start()
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome")
    pg = b.new_page(viewport={"width": 480, "height": 360})
    pg.goto("http://127.0.0.1:8765/index.html")
    pg.wait_for_function("() => !!customElements.get('model-viewer')")
    for c in ["claude-bare", "claude-renda", "pi-bare", "pi-renda"]:
        pg.evaluate(f"""() => new Promise(r => {{ const m=document.getElementById('mv'); m.addEventListener('load', () => setTimeout(r, 800), {{once:true}}); m.src='{c}.glb'; }})""")
        png = S / f"_mv-{c}.png"
        pg.locator("#mv").screenshot(path=str(png))
        Image.open(png).convert("RGB").save(OUT / f"bake-{c}.webp", "WEBP", quality=82)
        print("shot", c)
    b.close()
srv.shutdown()
