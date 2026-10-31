"""W6（空白格 B7 公开 API 文档）：离线导出 OpenAPI 规范到 docs/openapi.json。

- 模式照抄 build/validate_team_prisma.py：sys.path 注入仓库根与 build/，
  一次性临时数据目录（绝不触碰已安装应用或研究数据目录），
  ``launcher.build_app()`` 构建应用，请求走 ``httpx.ASGITransport``
  （非 TestClient）。httpx 与 pytest 同为开发/验收依赖（README 开发节），
  不进 build/requirements-app.txt，打包运行时不受影响。
- 运行时红线不放松（r2 评审 [HIGH] 后双保险）：custom_backend/main.py 的
  ``FastAPI(openapi_url=None, docs_url=None, redoc_url=None)``（产品化
  不对外暴露接口文档）保持原样。本脚本不走 HTTP 拿文档——那些路由本就
  不存在——而是直接调用 ``app.openapi()``；每次运行仍用
  verify_runtime_dark() 核验两件事：
  ①属性层：app.openapi_url / docs_url / redoc_url 三者必须显式为 None
    （只探测 GET /openapi.json 会被「改个路径」绕过——评审实测
    openapi_url='/api/openapi.json' 时旧守卫全绿）；
  ②探测层：ASGITransport 实测 /openapi.json、/docs、/redoc 必须 404、
    /api/health 必须 200（手工注册同名路由也会被抓）。
  任一不满足即 SystemExit 非零退出。
- 确定性：统一 ``sort_keys + indent=2 + ensure_ascii=False``、LF 换行、
  文件以单个换行结尾；同一份源码重复运行字节稳定（钉子：
  build/tests/test_w6_openapi_export.py 以两个独立子进程连跑比对）。
- ``--check`` 的全部失败路径受控（r2 评审 [LOW]）：目标文件缺失→
  ``{"status": "missing"}``、非法 JSON/非对象→``{"status": "invalid"}``、
  内容过期→``{"status": "stale", ...双向路径差异}``，一律退出码 1 +
  JSON 摘要，绝不抛 traceback。
- 本模块导入无副作用（环境与 launcher 导入都发生在函数内），测试可以
  安全 import 并复用 render()/diff_paths()/verify_runtime_dark()。

用法::

    python build/scripts/export_openapi.py             # 写入 docs/openapi.json
    python build/scripts/export_openapi.py --out P     # 写入指定路径
    python build/scripts/export_openapi.py --check     # 只校验目标文件未过期
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = ROOT / "docs" / "openapi.json"

#: 产品化「三关闭」属性（custom_backend/main.py:180-186）与对应探测路径。
_DARK_ATTRS = ("openapi_url", "docs_url", "redoc_url")
_DARK_PROBES = (("openapi", "/openapi.json"), ("docs", "/docs"), ("redoc", "/redoc"))


def _prepare_environment(data_dir: str) -> None:
    """与 build/validate_team_prisma.py:11-16 相同的一次性隔离环境。"""
    os.environ["COBOOKSHELF_DATA_DIR"] = data_dir
    os.environ["REVIEWFLOW_MODE"] = "server"
    os.environ.pop("REVIEWFLOW_RUNTIME_OVERLAY", None)
    for path in (str(ROOT), str(ROOT / "build")):
        if path not in sys.path:
            sys.path.insert(0, path)


def _build_app():
    """在隔离环境就绪后导入并构建真实应用（测试用假应用替换本接缝）。"""
    from launcher import build_app

    return build_app()


def verify_runtime_dark(app) -> dict:
    """断言应用保持「不对外暴露接口文档」红线；违规即 SystemExit。

    返回探测结果摘要（供脚本 JSON 输出与钉子断言复用）。属性缺失与
    非 None 同罪：守卫无法确认关闭状态时不得放行。
    """
    for attribute in _DARK_ATTRS:
        value = getattr(app, attribute, object())  # 缺失 → 哨兵 ≠ None
        if value is not None:
            raise SystemExit(
                f"运行时红线被破坏：app.{attribute}={value!r}（预期 None）。"
                "custom_backend/main.py 的 FastAPI(openapi_url=None, "
                "docs_url=None, redoc_url=None)（产品化不对外暴露接口文档）"
                "不得改；导出必须继续走离线 app.openapi()。"
            )

    import httpx

    async def probe() -> dict:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://localhost",
        ) as client:
            results = {}
            for label, path in _DARK_PROBES:
                response = await client.get(path)
                results[f"{label}_http_status"] = response.status_code
            results["health_http_status"] = (
                await client.get("/api/health")).status_code
            return results

    checks = asyncio.run(probe())
    for label, path in _DARK_PROBES:
        status = checks[f"{label}_http_status"]
        if status != 404:
            raise SystemExit(
                f"运行时红线被破坏：GET {path} 返回 {status}（预期 404）。"
                "产品不得经任何路径对外提供接口文档；导出走离线 "
                "app.openapi()，不需要打开该路由。"
            )
    if checks["health_http_status"] != 200:
        raise SystemExit(
            f"离线应用不健康：GET /api/health 返回 "
            f"{checks['health_http_status']}（预期 200），导出中止。"
        )
    return checks


def export_schema() -> tuple[dict, dict]:
    """离线构建应用，返回 (OpenAPI schema, 运行时核验结果)。

    临时数据目录在 finally 中清理；环境变量改动只影响本进程（脚本进程
    或子进程），不写任何仓库/安装目录。
    """
    data_dir = tempfile.mkdtemp(prefix="reviewflow-openapi-export-")
    try:
        _prepare_environment(data_dir)
        app = _build_app()
        schema = app.openapi()  # 三关闭只关服务路由，不拦离线取文档
        checks = verify_runtime_dark(app)
        return schema, checks
    finally:
        shutil.rmtree(data_dir, ignore_errors=True)


def render(schema: dict) -> str:
    """确定性序列化：键排序稳定键序，LF 换行，文件以单个换行结尾。"""
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def diff_paths(committed: dict, fresh: dict) -> dict:
    """两个 schema 的 (method, path) 集合差异，供 --check 输出定位。

    形状容错（r3 评审 [LOW-MEDIUM]）：``paths`` 不是对象时（合法 JSON 但
    形状非法）按空表处理，绝不在 int/str 上迭代抛异常；形状问题由
    _load_committed 先行判为 invalid。
    """

    def flatten(schema: dict) -> set:
        result = set()
        paths = schema.get("paths") if isinstance(schema, dict) else None
        if not isinstance(paths, dict):
            return result
        for path, item in paths.items():
            if not isinstance(item, dict):
                continue
            for method in item:
                if method in {"get", "put", "post", "delete", "patch"}:
                    result.add(f"{method.upper()} {path}")
        return result

    old, new = flatten(committed), flatten(fresh)
    return {"only_in_committed": sorted(old - new), "only_in_fresh": sorted(new - old)}


def _summary(schema: dict, checks: dict) -> dict:
    methods = sum(
        1
        for item in (schema.get("paths") or {}).values()
        for method in item
        if method in {"get", "put", "post", "delete", "patch"}
    )
    return {
        "openapi": schema.get("openapi"),
        "title": (schema.get("info") or {}).get("title"),
        "api_version": (schema.get("info") or {}).get("version"),
        "paths": len(schema.get("paths") or {}),
        "operations": methods,
        **checks,
    }


def _load_committed(path: Path) -> tuple[dict | None, str]:
    """读取 --check 目标文件，返回 (schema 或 None, 状态)。

    状态：missing（文件不存在）/ invalid（非法 JSON、非对象，或 paths
    不是对象——r3 评审实测 {"paths": "oops"} 会在 diff_paths 里抛
    AttributeError）/ read（可读且形状合规，由调用方比对）。任何失败都
    受控返回，绝不抛 traceback。
    """
    if not path.is_file():
        return None, "missing"
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # 含 UnicodeDecodeError 等子类
        return None, "invalid"
    if not isinstance(parsed, dict):
        return None, "invalid"
    if not isinstance(parsed.get("paths", {}), dict):
        return None, "invalid"
    return parsed, "read"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="离线导出 OpenAPI 规范（运行时 openapi_url 保持 None）。")
    parser.add_argument(
        "--out", type=Path, default=DEFAULT_OUT,
        help=f"输出路径（默认 {DEFAULT_OUT}）")
    parser.add_argument(
        "--check", action="store_true",
        help="不写文件，只校验目标文件与当前源码的导出一致（过期则退出码 1）")
    args = parser.parse_args(argv)

    schema, checks = export_schema()
    text = render(schema)
    if args.check:
        committed, status = _load_committed(args.out)
        if status == "read":
            status = "fresh" if render(committed) == text else "stale"
        if status == "fresh":
            print(json.dumps({"status": "fresh", "output": str(args.out),
                              **_summary(schema, checks)}, indent=2))
            return 0
        payload = {"status": status, "output": str(args.out),
                   **_summary(schema, checks)}
        if committed is not None:
            payload.update(diff_paths(committed, schema))
        print(json.dumps(payload, indent=2))
        return 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8", newline="\n")
    print(json.dumps({"status": "exported", "output": str(args.out),
                      **_summary(schema, checks)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
