"""W6（空白格 B7 公开 API 文档）钉子：导出稳定性 + 运行时红线 + 文档↔路由一致性。

六件事（全部经真实离线脚本/真实 schema/真实守卫函数，不做静态替身）：

1. `build/scripts/export_openapi.py` 以两个**独立子进程**连跑两次，产出必须
   字节一致（可重复运行产出稳定）。
2. 运行时红线破坏即红（r2 评审 [HIGH]：只探测 GET /openapi.json 可被
   「改个路径」绕过——openapi_url='/api/openapi.json' 时旧守卫全绿）。
   现在守卫是双保险：verify_runtime_dark() 断言 app.openapi_url/
   docs_url/redoc_url 三者显式为 None，且 ASGI 实测 /openapi.json、
   /docs、/redoc 必须 404、/api/health 必须 200。钉子用假 FastAPI 应用
   逐项证明：任一属性被打开、或手工注册 /openapi.json 路由 → SystemExit；
   三关闭的暗应用 → 通过。真实应用侧由脚本每次运行自带该核验。
3. docs/API_REFERENCE.md 里出现的每一个 `METHOD /api/...` 记号都必须存在于
   当前源码的真实路由表（文档写了已被删除/改名的端点 → 红）。
4. 反向：稳定核心子集（a080fde 基线 55 条 auth/tasks/import/decisions/
   fulltext/exports/AL 路由，见 EXPECTED_STABLE_CORE 显式清单）每条都
   必须仍在路由表且写进文档。同前缀下其他泳道的实验性新增（如
   fulltext/fetch*、study-designs）不在此列，不触发。
5. 入库的 docs/openapi.json 必须与当前源码的离线导出**等值**（r2 评审
   [LOW-MEDIUM]：提交员门禁只跑 pytest 不跑 --check，故新鲜度必须是
   pytest 硬门；其他泳道新增路由后忘记重导出 → 红，修复=重跑
   `python build/scripts/export_openapi.py`，一条命令）。**重导出必须是
   合并前的最后一步**：多泳道各自改 API 面，任何早于最后一次合入的导出
   在定义上就是过期的；刷新后再复跑本文件（`--check` 或整文件 pytest）
   确认转绿，别把「导出过了」当成结论。
6. `--check` 全部失败路径受控（r2 评审 [LOW]）：missing/stale/invalid
   （非法 JSON 或非对象）一律退出码 1 + JSON 摘要，不抛 traceback；
   fresh 退出 0。钉子逐一覆盖。

测试不在 pytest 进程内 import custom_backend/launcher（会按 main.py 模块级
环境变量固定数据目录，污染同进程其它测试）；真实导出一律走子进程，守卫
单测用独立假应用（只 import fastapi），--check 状态机单测用猴补桩。
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "build" / "scripts" / "export_openapi.py"
DOC = REPO / "docs" / "API_REFERENCE.md"
OPENAPI_JSON = REPO / "docs" / "openapi.json"

_METHODS = {"get", "put", "post", "delete", "patch"}
_ENDPOINT_TOKEN_RE = re.compile(r"`(GET|POST|PUT|DELETE|PATCH) (/api/[^`]+)`")

#: API_REFERENCE.md「Known limits」段声明的三项计数：操作总数、占位 2xx 响应数、
#: 带 authorization 头的操作数。r14 之前这些数字只按下界钉（≥200），路由增长后
#: 文档从 289/209/284 漂到实测 296/216/291 而无人察觉；现在从文档实读，与 schema
#: 实算做**等值**比对——改文档不改 schema、或加/删路由不改文档，两个方向都会红。
_DOC_PLACEHOLDER_RE = re.compile(
    r"Of the (\d+) operations, (\d+) have a 2xx `application/json` schema")
_DOC_AUTH_RE = re.compile(
    r"(\d+) of (\d+) operations instead expose `authorization`")


def _documented_openapi_counts() -> tuple[int, int, int]:
    """返回 (操作总数, 占位 2xx 响应数, 带 authorization 头的操作数)，取自文档。"""
    text = DOC.read_text(encoding="utf-8")
    placeholder_match = _DOC_PLACEHOLDER_RE.search(text)
    auth_match = _DOC_AUTH_RE.search(text)
    assert placeholder_match, (
        "API_REFERENCE.md 的 Known limits 段缺少「Of the N operations, M have a 2xx …」句")
    assert auth_match, (
        "API_REFERENCE.md 的 Known limits 段缺少「N of M operations … authorization」句")
    total, placeholders = int(placeholder_match.group(1)), int(placeholder_match.group(2))
    auth_count, auth_total = int(auth_match.group(1)), int(auth_match.group(2))
    assert total == auth_total, (
        f"API_REFERENCE.md 两处操作总数不一致（{total} vs {auth_total}）")
    return total, placeholders, auth_count

#: API_REFERENCE.md 声明的「稳定核心子集」= a080fde 基线上的 55 条路由
#: （auth/tasks/import/decisions/fulltext/exports/AL + 公开面 health/
#: runtime/app-update/check——后两条 r3 评审指出此前正文并入了 public
#: surface 却没进清单，删改不会被抓）。反向钉子用**显式清单**而非前缀族：空白格批次的其他泳道
#: （如 W2 的 fulltext/fetch*、W4 的 study-designs）会在同一前缀下新增
#: 实验性端点——它们按文档口径「实验性端点不在此列」，不该逼本钉子
#: 变红；而基线路由被删除/改名必须红。
EXPECTED_STABLE_CORE = frozenset({
    "GET /api/health",
    "GET /api/runtime",
    "GET /api/app-update/check",
    "POST /api/auth/register",
    "POST /api/auth/login",
    "GET /api/auth/me",
    "GET /api/tasks",
    "POST /api/tasks",
    "PUT /api/tasks/{task_id}",
    "DELETE /api/tasks/{task_id}",
    "GET /api/tasks/{task_id}/progress",
    "GET /api/tasks/{task_id}/articles",
    "POST /api/tasks/{task_id}/import",
    "POST /api/tasks/{task_id}/import-batch",
    "GET /api/tasks/{task_id}/import-progress/{job_id}",
    "GET /api/tasks/{task_id}/import-history",
    "GET /api/tasks/{task_id}/import-history/{batch_id}/files/{file_id}",
    "GET /api/tasks/{task_id}/import-review-summary",
    "GET /api/tasks/{task_id}/import-batches",
    "GET /api/tasks/{task_id}/import-batches/{batch_id}",
    "GET /api/tasks/{task_id}/import-batches/{batch_id}/candidates",
    "POST /api/tasks/{task_id}/import-batches/{batch_id}/candidates/{review_id}",
    "POST /api/tasks/{task_id}/import-batches/{batch_id}/metadata/{record_id}",
    "POST /api/tasks/{task_id}/import-batches/{batch_id}/bulk-review",
    "POST /api/tasks/{task_id}/import-batches/{batch_id}/activate",
    "POST /api/tasks/{task_id}/import-batches/{batch_id}/undo",
    "GET /api/tasks/{task_id}/import-batches/{batch_id}/export",
    "GET /api/tasks/{task_id}/title-duplicate-reviews",
    "POST /api/tasks/{task_id}/title-duplicate-reviews/{review_id}",
    "POST /api/tasks/{task_id}/title-duplicate-undo",
    "GET /api/tasks/{task_id}/title-duplicate-reviews/export",
    "GET /api/tasks/{task_id}/decisions",
    "POST /api/tasks/{task_id}/decisions",
    "GET /api/tasks/{task_id}/stage2/decisions",
    "POST /api/tasks/{task_id}/stage2/decisions",
    "GET /api/tasks/{task_id}/stage2/queue",
    "GET /api/tasks/{task_id}/fulltext/{zotero_key}",
    "POST /api/tasks/{task_id}/fulltext/{zotero_key}/upload",
    "GET /api/tasks/{task_id}/highlights/{zotero_key}",
    "POST /api/tasks/{task_id}/highlights",
    "DELETE /api/tasks/{task_id}/highlights/{highlight_id}",
    "GET /api/tasks/{task_id}/export/decisions",
    "GET /api/tasks/{task_id}/export/stage2",
    "GET /api/tasks/{task_id}/export/matrix",
    "GET /api/tasks/{task_id}/export/ris",
    "GET /api/tasks/{task_id}/export/highlights",
    "GET /api/tasks/{task_id}/al/rank",
    "GET /api/tasks/{task_id}/al/status",
    "GET /api/tasks/{task_id}/al/settings",
    "PUT /api/tasks/{task_id}/al/settings",
    "GET /api/tasks/{task_id}/al/profiles",
    "PUT /api/tasks/{task_id}/al/profile",
    "GET /api/tasks/{task_id}/al/shared-ranking",
    "GET /api/tasks/{task_id}/al/stop-rules",
    "GET /api/tasks/{task_id}/al/stop-certificate/check",
})


def _flatten(schema: dict) -> set[tuple[str, str]]:
    """schema['paths'] -> {(METHOD, path)}；与 API_REFERENCE.md 的记号同构。"""
    result = set()
    for path, item in (schema.get("paths") or {}).items():
        for method in item:
            if method in _METHODS:
                result.add((method.upper(), path))
    return result


def _load_module():
    """按文件路径加载导出脚本为模块（其模块级无副作用，可安全导入）。"""
    spec = importlib.util.spec_from_file_location("rf_w6_export_openapi", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_export(out_path: Path) -> dict:
    """跑一次真实导出脚本，返回其 JSON 摘要；非零退出直接 fail。"""
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--out", str(out_path)],
        capture_output=True, text=True, encoding="utf-8", timeout=600,
    )
    assert proc.returncode == 0, (
        f"export_openapi.py 失败（退出码 {proc.returncode}）：\n{proc.stderr[-2000:]}"
    )
    return json.loads(proc.stdout)


@pytest.fixture(scope="module")
def exported(tmp_path_factory):
    """两个独立子进程各导出一次：schema + 摘要 + 两份产物路径。"""
    first = tmp_path_factory.mktemp("w6-export") / "openapi.1.json"
    second = tmp_path_factory.mktemp("w6-export-2") / "openapi.2.json"
    summary = _run_export(first)
    _run_export(second)
    schema = json.loads(first.read_text(encoding="utf-8"))
    return schema, summary, first, second


@pytest.fixture
def restore_process_env():
    """export_schema() 会设置进程环境变量；用例结束后恢复原样。

    守卫单测在 pytest 进程内调用它（走猴补 _build_app 接缝），必须保证
    COBOOKSHELF_DATA_DIR/REVIEWFLOW_MODE 不泄漏给同进程其它测试。
    """
    saved = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(saved)


# ---------------------------------------------------------------------------
# 钉子 1：可重复运行产出稳定
# ---------------------------------------------------------------------------

def test_w6_export_repeatable_byte_identical(exported):
    """两个独立子进程的导出字节一致（可重复运行产出稳定）。"""
    schema, summary, first, second = exported
    assert first.read_bytes() == second.read_bytes(), (
        "同一份源码两次导出字节不一致——export_openapi.py 的序列化引入了"
        "不确定性（键序/浮点/时间戳？），见脚本的 render() 约定"
    )
    assert summary["status"] == "exported"
    version = (REPO / "VERSION").read_text(encoding="ascii").strip()
    assert summary["api_version"] == version, "导出版本应来自 VERSION 文件"
    assert summary["paths"] >= 200 and summary["operations"] >= 240


# ---------------------------------------------------------------------------
# 钉子 2：运行时红线（属性层 + 探测层）破坏即红
# ---------------------------------------------------------------------------

def test_w6_real_export_reports_dark_runtime(exported):
    """真实子进程导出摘要：三条文档路由全部 404 + health 200。"""
    _schema, summary, _f, _s = exported
    assert summary["openapi_http_status"] == 404
    assert summary["docs_http_status"] == 404
    assert summary["redoc_http_status"] == 404
    assert summary["health_http_status"] == 200


def test_w6_runtime_stays_dark_no_openapi_route(exported):
    """schema 侧：paths 里绝不能出现自描述路由（三关闭只关服务不开后门）。"""
    schema, _summary, _f, _s = exported
    paths = schema.get("paths") or {}
    assert "/openapi.json" not in paths
    assert "/docs" not in paths
    assert "/redoc" not in paths
    assert "/api/health" in paths


def _fake_dark_app(**fastapi_kwargs):
    """守卫单测用最小 FastAPI 应用：默认三关闭 + /api/health 200。

    注意 FastAPI() 构造默认是**三开**（openapi_url='/openapi.json'、
    docs_url='/docs'、redoc_url='/redoc'）——与 custom_backend/main.py
    的显式三 None 相反；这里默认三 None，fastapi_kwargs 用于注入单项
    破坏（openapi_url='/api/openapi.json' 等）。
    """
    from fastapi import FastAPI

    kwargs = {"openapi_url": None, "docs_url": None, "redoc_url": None}
    kwargs.update(fastapi_kwargs)
    app = FastAPI(**kwargs)

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok"}

    return app


@pytest.mark.parametrize("violation", [
    {"openapi_url": "/api/openapi.json"},  # r2 评审实测的绕过路径
    {"openapi_url": "/openapi.json"},
    {"docs_url": "/docs"},
    {"redoc_url": "/redoc"},
])
def test_w6_guard_rejects_enabled_doc_switches(monkeypatch, restore_process_env,
                                               violation):
    """破坏即红：任一文档开关被打开（无论路径是什么）→ SystemExit。

    修前形态（只探测 GET /openapi.json）对 openapi_url='/api/openapi.json'
    全绿——属性层断言现在先行拦截。
    """
    module = _load_module()
    monkeypatch.setattr(module, "_build_app", lambda: _fake_dark_app(**violation))
    attribute = next(iter(violation))
    with pytest.raises(SystemExit, match=rf"app\.{attribute}="):
        module.export_schema()


def test_w6_guard_rejects_manual_openapi_route(monkeypatch, restore_process_env):
    """破坏即红：三属性全 None 但手工注册了 /openapi.json 路由 → 探测层红。"""
    module = _load_module()

    def make_app():
        app = _fake_dark_app()

        @app.get("/openapi.json")
        def leaked() -> dict:
            return {"openapi": "3.1.0"}

        return app

    monkeypatch.setattr(module, "_build_app", make_app)
    with pytest.raises(SystemExit, match="/openapi\\.json"):
        module.export_schema()


def test_w6_guard_accepts_dark_app(monkeypatch, restore_process_env):
    """对照组：三关闭的暗应用顺利通过并返回可用的 schema 与探测摘要。"""
    module = _load_module()
    monkeypatch.setattr(module, "_build_app", lambda: _fake_dark_app())
    schema, checks = module.export_schema()
    assert checks == {
        "openapi_http_status": 404,
        "docs_http_status": 404,
        "redoc_http_status": 404,
        "health_http_status": 200,
    }
    assert schema.get("openapi") and schema.get("paths")


# ---------------------------------------------------------------------------
# 钉子 3+4：文档 ↔ 路由表 双向一致性
# ---------------------------------------------------------------------------

def test_w6_api_reference_endpoints_exist_in_route_table(exported):
    """文档写出的每个 METHOD /api/... 都在真实路由表中。"""
    schema, _summary, _f, _s = exported
    live = {f"{m} {p}" for m, p in _flatten(schema)}
    documented = {
        f"{match.group(1)} {match.group(2)}"
        for match in _ENDPOINT_TOKEN_RE.finditer(DOC.read_text(encoding="utf-8"))
    }
    assert documented, "API_REFERENCE.md 里一个端点记号都没解析到——解析器坏了"
    ghost = sorted(documented - live)
    assert not ghost, (
        f"API_REFERENCE.md 记载了路由表中不存在的端点（已删除/改名未同步文档）：{ghost}"
    )


def test_w6_api_reference_covers_stable_scope(exported):
    """稳定核心子集的每条基线路由都必须仍在路由表且写进文档。"""
    schema, _summary, _f, _s = exported
    live = {f"{m} {p}" for m, p in _flatten(schema)}
    documented = {
        f"{match.group(1)} {match.group(2)}"
        for match in _ENDPOINT_TOKEN_RE.finditer(DOC.read_text(encoding="utf-8"))
    }
    removed = sorted(EXPECTED_STABLE_CORE - live)
    assert not removed, (
        f"稳定核心子集的基线路由已从应用中消失/改名，文档与清单需同步修订：{removed}"
    )
    undocumented = sorted(EXPECTED_STABLE_CORE - documented)
    assert not undocumented, (
        f"稳定核心子集内有未写进 docs/API_REFERENCE.md 的路由（漏表）：{undocumented}"
    )


# ---------------------------------------------------------------------------
# 钉子 5：入库 openapi.json 必须与当前源码导出等值（新鲜度硬门）
# ---------------------------------------------------------------------------

def test_w6_committed_openapi_json_matches_fresh_export(exported):
    """入库文件 ≡ 当前源码导出（canonical 渲染比对，对 EOL/空白稳健）。

    r2 评审 [LOW-MEDIUM]：提交员门禁只跑 pytest/node/校验器、不跑
    ``--check``，新鲜度必须由本测试承担——否则其他泳道加路由后
    docs/openapi.json 会静默失真。红了就重跑
    ``python build/scripts/export_openapi.py``（README 构建节有该命令），
    修好后本测试自然回绿。
    """
    schema, _summary, _f, _s = exported
    assert OPENAPI_JSON.is_file(), "docs/openapi.json 未入库"
    try:
        committed = json.loads(OPENAPI_JSON.read_text(encoding="utf-8"))
    except ValueError as exc:
        pytest.fail(f"docs/openapi.json 不是合法 JSON：{exc}")
    module = _load_module()
    assert module.render(committed) == module.render(schema), (
        "docs/openapi.json 与当前源码的离线导出不一致"
        f"（差异：{module.diff_paths(committed, schema)}）——"
        "路由有增删/改名后请重跑 python build/scripts/export_openapi.py 再提交。"
    )
    version = (REPO / "VERSION").read_text(encoding="ascii").strip()
    assert (committed.get("info") or {}).get("version") == version, (
        "docs/openapi.json 的 info.version 与 VERSION 文件不一致——"
        "版本号变更后需重跑导出脚本"
    )


# ---------------------------------------------------------------------------
# 钉子 6：--check 的受控失败路径
# ---------------------------------------------------------------------------

def test_w6_check_mode_fresh_via_real_script(exported):
    """端到端：--check 对刚导出的文件退出 0、status=fresh、无 traceback。"""
    _schema, _summary, first, _second = exported
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--check", "--out", str(first)],
        capture_output=True, text=True, encoding="utf-8", timeout=600,
    )
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    assert json.loads(proc.stdout)["status"] == "fresh"
    assert "Traceback" not in proc.stderr


def test_w6_check_mode_stale_missing_invalid(monkeypatch, capsys, tmp_path,
                                             exported):
    """--check 状态机：stale/missing/invalid 一律退出 1 + JSON 摘要。

    用猴补桩替换 export_schema（真实构建路径已由其它钉子覆盖），聚焦
    main() 的失败处理本身：不许抛异常、不许空 stdout、退出码必须 1。
    """
    schema, summary, _f, _s = exported
    checks = {k: v for k, v in summary.items() if k.endswith("_http_status")}
    module = _load_module()
    monkeypatch.setattr(module, "export_schema", lambda: (schema, checks))

    # stale：入库副本删一条路径（→only_in_fresh 指认）再加一条幽灵路径
    # （→only_in_committed 指认），双向差异都受控呈现
    tampered = json.loads(OPENAPI_JSON.read_text(encoding="utf-8"))
    victim = sorted(tampered["paths"])[0]
    del tampered["paths"][victim]
    ghost_path = "/api/__w6_stale_probe__"
    tampered["paths"][ghost_path] = {"get": {"responses": {}}}
    stale_path = tmp_path / "stale.json"
    stale_path.write_text(json.dumps(tampered), encoding="utf-8")
    assert module.main(["--check", "--out", str(stale_path)]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "stale"
    assert any(victim in item for item in payload["only_in_fresh"]), payload
    assert any(ghost_path in item for item in payload["only_in_committed"]), payload

    # missing：目标文件不存在
    assert module.main(["--check", "--out", str(tmp_path / "nope.json")]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "missing"

    # invalid：非法 JSON（修前形态是裸 JSONDecodeError traceback）
    broken = tmp_path / "broken.json"
    broken.write_text("{", encoding="utf-8")
    assert module.main(["--check", "--out", str(broken)]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "invalid"

    # invalid：合法 JSON 但不是对象（diff_paths 的 .get 修前会抛）
    array = tmp_path / "array.json"
    array.write_text("[1, 2]", encoding="utf-8")
    assert module.main(["--check", "--out", str(array)]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "invalid"

    # invalid：合法对象但 paths 形状非法（r3 评审实测：
    # {"paths": "oops"} 修前在 diff_paths 里抛 'str' has no attribute 'items'）
    bad_paths = tmp_path / "bad-paths.json"
    bad_paths.write_text('{"paths": "oops"}', encoding="utf-8")
    assert module.main(["--check", "--out", str(bad_paths)]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "invalid"


# ---------------------------------------------------------------------------
# 钉子 7：文档「Known limits of openapi.json」段与 schema 实测一致
# ---------------------------------------------------------------------------

def test_w6_documented_openapi_limits_stay_true(exported):
    """r3 评审 [MEDIUM]：文档必须如实说明 openapi.json 不是完整契约。

    三个事实钉在真实 schema 上——任何一个变了（补了 securitySchemes、
    出现带字段的 2xx schema、screener 不再是 query 参数），文档就必须
    同步修订，否则红。
    """
    schema, _summary, _f, _s = exported

    # ① 无 securitySchemes：鉴权以普通 header 参数暴露（文档已披露）。
    #    公开面 5 条不带 authorization，其余全部带——两个方向都钉。
    components = schema.get("components") or {}
    assert "securitySchemes" not in components, (
        "schema 现在有 securitySchemes 了——API_REFERENCE.md 的 Known limits "
        "第 2 条需修订（并考虑补 security 声明语义说明）"
    )
    public_surface = {
        "GET /api/health",
        "GET /api/runtime",
        "GET /api/app-update/check",
        "POST /api/auth/register",
        "POST /api/auth/login",
    }
    operations = {
        f"{method.upper()} {path}": op
        for path, item in schema["paths"].items()
        for method, op in item.items()
        if method in _METHODS
    }
    with_auth_header = {
        key for key, op in operations.items()
        if any(param.get("name") == "authorization"
               for param in op.get("parameters", []))
    }
    assert with_auth_header == set(operations) - public_surface, (
        "authorization 头的分布与已知公开面不一致——Known limits 第 2 条与 "
        "Conventions/Authentication 段需修订。多出："
        f"{sorted(with_auth_header & public_surface)}；缺失："
        f"{sorted(set(operations) - public_surface - with_auth_header)}"
    )

    # ① 的操作数、② 的占位响应数、authorization 头数量都必须与文档所写**相等**
    # （r14 起由下界钉升级为等值钉；文档数字变化而 schema 未变、或路由增减而文档
    # 未更新，两个方向都红）。
    documented_total, documented_placeholders, documented_auth = (
        _documented_openapi_counts())
    assert documented_total == len(operations), (
        f"API_REFERENCE.md 写 {documented_total} 个操作，schema 实算 "
        f"{len(operations)} 个——路由增减后请同步刷新 Known limits 段的三项计数")
    assert documented_auth == len(with_auth_header), (
        f"API_REFERENCE.md 写 {documented_auth} 个操作带 authorization 头，schema 实算 "
        f"{len(with_auth_header)} 个——鉴权头分布变化后请同步刷新计数")

    # ② 2xx 响应普遍是无字段的 additionalProperties 占位（含 title，无 properties）
    def is_placeholder(op) -> bool:
        for code, response in (op.get("responses") or {}).items():
            if not str(code).startswith("2"):
                continue
            content = (response.get("content") or {}).get("application/json")
            body = ((content or {}).get("schema") or {})
            return (body.get("type") == "object"
                    and body.get("additionalProperties") is True
                    and not body.get("properties"))
        return False

    placeholders = sum(1 for op in operations.values() if is_placeholder(op))
    assert documented_placeholders == placeholders, (
        f"API_REFERENCE.md 写 {documented_placeholders} 条占位 2xx 响应，schema 实算 "
        f"{placeholders} 条——若已有泳道补齐 response_model，Known limits 段需整体修订")

    # ③ import 的 screener 是 query 参数而非 multipart 字段（文档已更正）
    import_op = schema["paths"]["/api/tasks/{task_id}/import"]["post"]
    assert "multipart/form-data" in import_op["requestBody"]["content"]
    assert any(param.get("name") == "screener"
               for param in import_op.get("parameters", [])), (
        "import 的 screener 不再出现在 query 参数里——API_REFERENCE.md "
        "import 表关于 query/form 的说明需修订"
    )
