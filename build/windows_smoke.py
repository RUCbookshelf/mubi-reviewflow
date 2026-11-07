"""Build gate using the installed runtime, including lazy native export/AI paths.

Runs from the staged payload with the bundled runtime (`build_windows.bat`
step [5/6]). Every gate is an explicit check that raises `SmokeFailure`, never
a bare `assert`: `python -O` / `PYTHONOPTIMIZE` in the environment used to turn
the whole gate into a no-op that still exited 0.

Each check names the step it belongs to and prints a one-line verdict, so the
first failing step is readable in `build_windows_smoke.log` without a debugger.
"""
from __future__ import annotations

import os
import struct
import sys
import tempfile
from pathlib import Path

STEP = "startup"


def _bootstrap_path() -> None:
    """Make the packaged layout and the repo layout importable alike.

    The staged payload keeps `coscreen/` and `launcher.py` next to this script;
    in the repo checkout they live one level up (`build/` is the script dir).
    The old script relied on the build running with the staging dir as cwd.
    """
    here = Path(__file__).resolve().parent
    for candidate in (here.parent, here):
        if (candidate / "coscreen").is_dir() and str(candidate) not in sys.path:
            sys.path.insert(0, str(candidate))


_bootstrap_path()


class SmokeFailure(Exception):
    """A gate failed; the message is the user-facing reason."""


def check(condition, message):
    if not condition:
        raise SmokeFailure(f"[{STEP}] {message}")


def _png_size(data: bytes) -> tuple[int, int]:
    """(width, height) from the IHDR chunk of a PNG, or raise SmokeFailure."""
    check(isinstance(data, (bytes, bytearray)),
          f"PNG 输出类型应为 bytes，实得 {type(data).__name__}")
    check(bytes(data[:8]) == b"\x89PNG\r\n\x1a\n",
          f"PNG 魔数不符：{bytes(data[:8])!r}")
    check(len(data) >= 24 and bytes(data[12:16]) == b"IHDR",
          f"PNG 缺少 IHDR 块（长度 {len(data)}）")
    return struct.unpack(">II", bytes(data[16:24]))


def _isolate_data_dir() -> Path:
    """Give the backend import an absolute, disposable data dir.

    `custom_backend/main.py` reads COBOOKSHELF_DATA_DIR at import time and
    defaults to the relative `data/`. The smoke test previously set neither
    that variable nor REVIEWFLOW_MODE, so it exercised an import-time path the
    installed app never uses. Mirror the desktop entry point here.
    """
    root = Path(tempfile.mkdtemp(prefix="reviewflow-smoke-"))
    os.environ["COBOOKSHELF_DATA_DIR"] = str(root)
    os.environ["REVIEWFLOW_MODE"] = "desktop"
    # Keep the import-time migration/control files inside the same sandbox.
    os.environ["REVIEWFLOW_STORAGE_CONFIG"] = str(root / "local-storage.json")
    return root


def check_runtime_dependencies() -> None:
    from windows_start import check_dependencies

    failures = check_dependencies()
    check(not failures, f"运行依赖缺失：{failures}")


def check_clustering() -> None:
    from sklearn.cluster import KMeans

    labels = KMeans(n_clusters=1, n_init=1, random_state=0).fit_predict([[0.0], [1.0]])
    check(len(labels) == 2,
          f"KMeans 对 2 个样本应返回 2 个标签，实得 {len(labels)}")
    check(set(labels.tolist()) == {0},
          f"单簇 KMeans 的标签应全为 0，实得 {sorted(set(labels.tolist()))}")


def check_svg_png() -> None:
    from coscreen.svg_render import svg_to_png

    width = 20
    data = svg_to_png(
        b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">'
        b'<rect width="10" height="10" fill="red"/></svg>', width)
    size = _png_size(data)
    # A correct rasterisation is square here: 10x10 viewBox at width 20 -> 20x20.
    check(size == (width, width),
          f"SVG 栅格化尺寸应为 {width}x{width}，实得 {size[0]}x{size[1]}")


def check_activity_exports() -> None:
    from coscreen.research_trace_export import _activity_png, _activity_svg

    empty_svg = _activity_svg([], {}, "zh-CN")
    check(b"<rect" in empty_svg, "空活动数据 SVG 缺少画布背景/图例矩形")
    empty_png = _activity_png([], {}, "zh-CN")
    empty_size = _png_size(empty_png)
    check(empty_size[0] > 0 and empty_size[1] > 0,
          f"空活动数据导出 PNG 尺寸非法：{empty_size}")

    days = ["2024-01-01", "2024-01-02"]
    values = {
        days[0]: {"screen": 2, "fulltext": 1, "coding": 1},
        days[1]: {"screen": 1, "fulltext": 3, "coding": 1},
    }
    svg = _activity_svg(days, values, "zh-CN")
    for day in days:
        check(day.encode("utf-8") in svg, f"非空活动 SVG 缺少日期标签 {day}")
    check(svg.count(b"<rect") > empty_svg.count(b"<rect"),
          "非空活动数据未画出任何柱体")
    filled_png = _activity_png(days, values, "zh-CN")
    filled_size = _png_size(filled_png)
    check(filled_size == empty_size,
          f"活动图 PNG 画布尺寸应恒为 {empty_size}，实得 {filled_size}")
    check(filled_png != empty_png,
          "非空活动数据的 PNG 与空数据完全相同（柱体/标签分支未执行）")


def check_app() -> None:
    import launcher
    from fastapi.routing import Mount

    app = launcher.build_app()
    check(app is not None, "launcher.build_app() 返回 None")
    mounts = [route for route in getattr(app, "routes", []) if isinstance(route, Mount)]
    static = next((m for m in mounts
                   if m.name == "static" and getattr(m, "path", None) in {"", "/"}), None)
    check(static is not None,
          f"前端静态目录未挂载：现有 mount={[(m.path, m.name) for m in mounts]}")
    directory = Path(getattr(static.app, "directory", ""))
    check((directory / "index.html").is_file(),
          f"挂载目录内没有 index.html：{directory}")
    check(any(getattr(route, "path", None) == "/api/health" for route in app.routes),
          "应用未注册 /api/health")


CHECKS = (
    ("runtime dependencies", check_runtime_dependencies),
    ("AI clustering", check_clustering),
    ("SVG rasterisation", check_svg_png),
    ("activity PNG/SVG export", check_activity_exports),
    ("app startup", check_app),
)


def run() -> int:
    global STEP
    sandbox = _isolate_data_dir()
    try:
        for name, probe in CHECKS:
            STEP = name
            probe()
            print(f"smoke: {name}: OK")
    except SmokeFailure as exc:
        print(f"smoke: FAILED: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - any crash is a gate failure with context
        import traceback

        traceback.print_exc()
        print(f"smoke: FAILED: [{STEP}] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        import shutil

        shutil.rmtree(sandbox, ignore_errors=True)
    print("Windows runtime, AI clustering, PNG exports and app startup: OK")
    return 0


if __name__ == "__main__":
    sys.exit(run())
