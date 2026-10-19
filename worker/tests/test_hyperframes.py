from pathlib import Path

from renda_worker.hyperframes import build_render_command
from renda_worker.models import HyperframesPackage

from .conftest import URI


def test_minimal_command():
    cmd, warnings = build_render_command(HyperframesPackage(project=URI), Path("/p"), Path("/o/video.mp4"))
    assert cmd[:3] == ["hyperframes", "render", "/p"]
    assert cmd[cmd.index("--output") + 1] == "/o/video.mp4"
    assert cmd[cmd.index("--format") + 1] == "mp4"
    assert cmd[cmd.index("--quality") + 1] == "looks"
    assert "--no-browser-gpu" in cmd and warnings == []


def test_full_command_and_preset_resolution():
    pkg = HyperframesPackage(project=URI, composition="compositions/intro.html", width=3840, height=2160, fps=60,
                             format="webm", quality="delivery", variables={"title": "Hi"})
    cmd, warnings = build_render_command(pkg, Path("/p"), Path("/o/v.webm"))
    assert cmd[cmd.index("--composition") + 1] == "compositions/intro.html"
    assert cmd[cmd.index("--resolution") + 1] == "landscape-4k"
    assert cmd[cmd.index("--fps") + 1] == "60"
    assert cmd[cmd.index("--variables") + 1] == '{"title":"Hi"}'
    assert warnings == []


def test_odd_size_warns_and_gif_caps_fps():
    cmd, warnings = build_render_command(HyperframesPackage(project=URI, width=1000, height=500, fps=60, format="gif"), Path("/p"), Path("/o/v.gif"))
    assert "--resolution" not in cmd
    assert cmd[cmd.index("--fps") + 1] == "30"
    assert len(warnings) == 2
