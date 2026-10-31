# -*- coding: utf-8 -*-
"""R8 · W5 PWA 离线壳钉子（静态守卫 + node 冒烟接线）——撤销任一接线即红。

W5 交付（泳道文件域：custom_frontend/sw.js、manifest.webmanifest、icons/*、
offline_banner.js、index.html 最末注册行）：
- sw.js：带 ?v= 令牌的同源 JS/CSS cache-first；index.html 网络优先＋缓存兜底；
  离线只读快照提示（不做离线编辑）；scope 相对化兼容根路径与 /reviewflow/ 子路径。
- index.html：<head> manifest 链接（带 r8-W5PWA1 令牌）＋ 文档最末 SW 注册行
  （本组独占锚点；相对 './sw.js' + scope './'）。

治理红线对应：
- 联网默认关 / 零额外出站：sw.js 无 install 钩子、无 importScripts、无 precache
  清单——只被动缓存页面自身请求（同源 GET 且仅 ?v= JS/CSS 与文档导航）。
- 优雅降级：注册失败仅 console.warn；未缓存资产离线回 503 空体；无快照不伪造。
- 十语言：offline_banner.js 两条词条各 10 列非空（列序 zh-CN/en/fr/ru/es/ja/pt/de/sr/ko）。
- 回环/同源限定：SW 只触碰同源请求，跨源与非 GET 完全放行且永不缓存
  （行为级断言在 build/tests/w5_pwa_sw.test.cjs，由本文件以 CI 同款命令实跑）。
- MM5-01 兼容：index.html 全部 ?v= 计数仍与 src/href 正则捕获数相等（新增引用
  不得让令牌守卫出现无人看守的形态）。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
FRONTEND = REPO / "custom_frontend"
INDEX = FRONTEND / "index.html"

TOKEN = "20261030-r8-W5PWA1"

NODE = (
    os.environ.get("RF_NODE")
    or shutil.which("node")
    or r"C:\Users\16073\AppData\Local\hermes\node\node.exe"
)

SW_FILES = (
    "sw.js",
    "manifest.webmanifest",
    "offline_banner.js",
    "icons/icon-192.png",
    "icons/icon-512.png",
    "icons/icon-maskable-512.png",
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _find_row(text: str, key: str):
    """按 zh 键定位 locale 行并提取全部字符串字面量（十语言列）。

    与 test_ux_frontend_static_guards.py 同款字符串感知扫描器（行内无嵌套数组）。
    """
    idx = -1
    for quote in ('"', "'"):
        idx = text.find(quote + key + quote)
        if idx >= 0:
            break
    if idx < 0:
        return None
    start = text.rfind("[", 0, idx)
    if start < 0:
        return None
    depth = 0
    i = start
    end = -1
    while i < len(text):
        c = text[i]
        if c in ('"', "'"):
            i += 1
            while i < len(text):
                if text[i] == "\\":
                    i += 2
                    continue
                if text[i] == c:
                    break
                i += 1
        elif c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                end = i
                break
        i += 1
    if end < 0:
        return None
    row = text[start:end + 1]
    values = []
    j = 0
    while j < len(row):
        c = row[j]
        if c in ('"', "'"):
            q = c
            j += 1
            buf = []
            while j < len(row):
                if row[j] == "\\":
                    buf.append(row[j + 1])
                    j += 2
                    continue
                if row[j] == q:
                    break
                buf.append(row[j])
                j += 1
            values.append("".join(buf))
        j += 1
    return values


# ---------------------------------------------------------------- 文件与接线

def test_w5_files_exist():
    for rel in SW_FILES:
        assert (FRONTEND / rel).is_file(), f"W5 缺少文件：custom_frontend/{rel}"


def test_manifest_link_in_head_with_group_token():
    html = _read(INDEX)
    link = f'<link rel="manifest" href="./manifest.webmanifest?v={TOKEN}">'
    assert link in html, "index.html <head> 缺少带 r8-W5PWA1 令牌的 manifest 链接"
    assert html.index(link) < html.index("</head>"), "manifest 链接必须在 <head> 内"


def test_registration_line_is_the_last_script_and_relative():
    """SW 注册行 = index.html 最末脚本块（本组独占锚点）。

    撤销相对化（写成绝对路径）、挪位（不再最后）、或把令牌塞进内联脚本
    （会破坏 MM5-01 的 ?v= 计数守卫）都会变红。
    """
    html = _read(INDEX)
    last_script = html.rfind("<script")
    assert last_script >= 0
    block = html[last_script:]
    assert "src=" not in block.split(">")[0], "最末脚本必须是内联注册块，不是外链"
    assert re.search(r"navigator\.serviceWorker\.register\('\./sw\.js',\s*\{\s*scope:\s*'\./'", block), \
        "注册必须用相对 './sw.js' + scope './'（根路径与 /reviewflow/ 子路径同构）"
    assert html.count("serviceWorker.register") == 1, "注册行必须唯一"
    assert html.rindex("<script src=") < last_script, "注册块必须位于全部外链脚本之后（文档最末锚点）"
    assert block.index("</script>") < html.index("</body>", last_script)
    assert "updateViaCache: 'none'" in block, "SW 主脚本不得吃 HTTP 缓存（launcher 对非 HTML 下发 max-age=86400）"
    assert "?v=" not in block, "内联注册块不得含 ?v= 字面量（MM5-01 计数等式会被打破）"
    assert '"/sw.js"' not in block and "'/sw.js'" not in block, "不得使用绝对路径注册"


def test_offline_startup_note_moved_into_module_and_keeps_auth_behavior():
    """第 3 轮评审 #2/#3：登录页离线说明由 offline_banner.js 统一创建/清理。

    评审实测：内联 static 块被 position:fixed;z-index:0 的 .login-bg 盖住（该
    区域 0 个可见像素），且触发条件 navigator.onLine === false 在后端挂掉时恒
    不成立；同时它永不清理。修复=模块侧按「是否由离线快照交付」判定 + 显式
    position:relative;z-index:6 + 三处清理。钉子保证：内联块不得回归、认证
    行为不变、模块侧三要素（可见样式/清理函数/健康探测）都在。
    """
    html = _read(INDEX)
    assert "navigator.onLine === false" not in html.split("</body>")[0], (
        "旧内联说明（onLine 守卫）不得回归：本地后端挂掉时 onLine 恒为 true"
    )
    assert "offlineNoteText" not in html, "旧内联说明块不得回归"
    assert "$('#authScreen').style.display = 'flex';" in html, "登录屏显示语句必须保留（认证行为不变）"

    src = _read(FRONTEND / "offline_banner.js")
    # 可见性：必须在 .login-bg（z-index:0）之上，且随层一起开关
    assert "z-index:6" in src, "说明必须显式 position:relative;z-index:6（否则被 .login-bg 盖住）"
    assert "position:relative" in src
    assert "getElementById('authScreen')" in src, "说明应挂进 #authScreen 随层开关"
    # 触发：按快照交付事实判定，不依赖 onLine
    assert "snapshotDelivered" in src, "须按「本次文档是否由快照交付」判定"
    # 清理：两条恢复路径都要清掉，且清除先于改样式（否则观察器重建）
    assert "clearLoginNote" in src
    assert "clearOfflineUi" in src
    clear_body = src[src.index("function clearOfflineUi"):]
    clear_body = clear_body[:clear_body.index("}")]
    stmt = [ln.strip() for ln in clear_body.splitlines()
            if ln.strip() and not ln.strip().startswith(("/*", "*", "//"))]
    assert stmt.index("snapshotDelivered = false;") < stmt.index("hide();"), (
        "clearOfflineUi 必须先清状态再改 DOM：hide() 改 padding 会触发观察器，"
        "状态未清则刚删的说明会被重建"
    )
    # 恢复探测：只打同源 /api/health，且 404 不算恢复
    assert "fetch('/api/health'" in src
    assert "resp.status >= 200 && resp.status < 400" in src, (
        "恢复判定只接受 2xx/3xx：静态托管对任意路径回 404，不得据此收起"
    )
    # 布局：fixed 细条，禁止被 body flex 拉伸
    assert "position:fixed" in src and "flex:0 0 auto" in src and "height:auto" in src
    assert "getComputedStyle" in src, "让位补偿必须读计算样式（内联值为空会把 CSS 内边距覆盖掉）"



def test_index_v_token_accounting_keeps_mm5_01_green():
    """MM5-01 兼容钉：页面每一个 ?v= 都必须是 src/href 属性形态。"""
    html = _read(INDEX)
    pairs = re.findall(r'(?:src|href)="(?:\./)?([^"?]+)\?v=([^"]+)"', html)
    assert pairs, "index.html 未解析到任何 ?v= 令牌"
    assert len(pairs) == html.count("?v="), (
        f"令牌解析不完整：页面共有 {html.count('?v=')} 处 ?v=，src/href 正则只捕获 {len(pairs)}"
    )
    per_asset = {}
    for asset, token in pairs:
        per_asset.setdefault(asset, set()).add(token)
    double = {a: t for a, t in per_asset.items() if len(t) > 1}
    assert not double, f"同一文件被引用了多个不同令牌：{double!r}"
    missing = [a for a in per_asset if not (FRONTEND / a).is_file()]
    assert not missing, f"index.html 引用了不存在的文件：{missing!r}"
    assert ("manifest.webmanifest", TOKEN) in pairs, "manifest 链接令牌形态漂移"
    assert ("offline_banner.js", TOKEN) in pairs, "offline_banner.js 引用令牌形态漂移"


# ---------------------------------------------------------------- manifest 形态

def test_manifest_json_shape_is_scope_relative():
    m = json.loads(_read(FRONTEND / "manifest.webmanifest"))
    assert m["start_url"] == "./", "start_url 必须相对（scope 相对化红线）"
    assert m["scope"] == "./", "scope 必须相对（兼容根路径与 /reviewflow/ 子路径）"
    assert not str(m.get("start_url", "")).startswith("/")
    assert m.get("name") and m.get("short_name")
    icons = m.get("icons")
    assert isinstance(icons, list) and len(icons) >= 3
    purposes = {i.get("purpose") for i in icons}
    assert "maskable" in purposes and "any" in purposes
    for icon in icons:
        rel = icon["src"]
        assert not rel.startswith("/"), "图标路径必须相对"
        assert (FRONTEND / rel).is_file(), f"manifest 图标缺失：{rel}"
        assert icon["type"] == "image/png"
        size = icon["sizes"]
        w = int(size.split("x")[0])
        assert w >= 192, "至少一枚 ≥192px 图标（可安装性门槛）"


# ---------------------------------------------------------------- offline_banner 模块

def test_offline_banner_module_shape_and_ten_languages():
    src = _read(FRONTEND / "offline_banner.js")
    assert re.search(r"^\s*(?:/\*[\s\S]*?\*/\s*)*\(function\s*\(\s*\)", src), "必须是 IIFE 自包含模块"
    assert src.rstrip().endswith("})();")
    assert "RFLang.register" in src, "必须自带十语言注册"
    assert "innerHTML" not in src, "红线：不得使用 innerHTML"
    assert src.count("textContent") >= 2, "两条文案都必须经 textContent 渲染"
    rows_start = src.index("var ROWS = [")
    rows_end = src.index("];", rows_start)
    rows_region = src[rows_start:rows_end]
    row1 = _find_row(rows_region, "当前离线：显示的是上次在线时的页面外壳，任务与文献数据需恢复连接后加载。")
    row2 = _find_row(rows_region, "页面外壳来自最近一次在线时的缓存；离线期间不提供编辑与同步。")
    row3 = _find_row(rows_region, "当前离线，无法校验登录状态；页面外壳来自上次在线时的缓存，数据与登录需恢复连接。")
    row4 = _find_row(rows_region, "隐藏此提示")
    for row, where in ((row1, "快照主提示"), (row2, "快照副提示"), (row3, "启动登录态说明"), (row4, "关闭按钮")):
        assert row is not None, f"{where}词条未注册"
        assert len(row) == 10, f"{where}应为十语言列，实际 {len(row)} 列"
        assert all(row), f"{where}存在空列"
    assert row4[1] == "Hide this notice", "关闭按钮英文形态漂移"
    assert "BANNER_ID + 'Close'" in src, "提示条必须渲染自助关闭按钮（r3：不能只能靠刷新）"
    assert row1[1].startswith("You are offline"), "英文列形态漂移"
    # 第 2 轮评审 finding #3：不得再过度承诺「缓存页面可浏览」
    assert "可查看最近缓存的页面" not in src, "文案不得声称缓存页面可完整浏览（离线时数据仍来自 /api/）"
    assert "页面外壳" in row1[0] and "数据" in row1[0], "主提示须如实说明：壳可看、数据需联网"
    # 协议对端：查询/直推/清旗三形态都要接
    for msg_type in ("rf-offline-snapshot-state", "rf-offline-snapshot", "rf-leave-offline-snapshot"):
        assert msg_type in src, f"offline_banner 必须处理 {msg_type}"


def test_banner_yields_instead_of_occluding_login_controls():
    """第 4 轮评审 medium：条盖住登录页顶部三控件，与「不拦截任何操作」不符。

    修复口径＝让位而不是遮挡，且必须覆盖两个层：
    - 登录态：`#authScreen .site-header` 是 position:absolute;top:30px，给
      #authScreen 加 padding 挪不动它（top 相对 padding box）；必须按 header
      真实 rect 现算位移，并把基线还原来。
    - 应用态：#appShell / #appMain 加顶部内边距（.top 控制条在 #appMain 内）；
      再加顶部贴合的浮层入口 #rfNavigationSheet（fixed;top:8px，层级低于条），
      下移而不是抬高层级去压。
    静态钉只保证「让位机制还在、且没退回给 #authScreen 加内边距的旧形态」；
    「三控件/侧栏开关真的点得到」由 w5_pwa_banner_browser.cjs 在真浏览器里钉。
    """
    src = _read(FRONTEND / "offline_banner.js")
    # 让位的两个层都要在名单里，且登录层不得再走 padding 补偿
    reserved = re.search(r"var RESERVED = \[([^\]]*)\];", src)
    assert reserved, "应用态让位名单 RESERVED 必须存在"
    names = [n.strip().strip("'\"") for n in reserved.group(1).split(",") if n.strip()]
    assert names == ["appShell", "appMain"], (
        f"应用态让位名单必须是 appShell + appMain，实际 {names!r}；"
        "登录层不得回到「给 #authScreen 加内边距」的旧形态（padding 挪不动 absolute 的 .site-header）"
    )
    # 顶部贴合的浮层入口（侧栏开关）也必须让位
    anchored = re.search(r"var TOP_ANCHORED = \[([^\]]*)\];", src)
    assert anchored, "顶部贴合浮层让位名单 TOP_ANCHORED 必须存在"
    anchored_names = [n.strip().strip("'\"") for n in anchored.group(1).split(",") if n.strip()]
    assert "rfNavigationSheet" in anchored_names, (
        "侧栏开关 #rfNavigationSheet 是 fixed;top:8px、层级低于提示条，必须让位（否则展开侧栏的入口被整块盖住）"
    )
    assert "reserveTopAnchored" in src, "浮层让位必须有独立分支（改内联 top，不抬 z-index）"
    assert "topAnchoredBase" in src, "浮层基线必须保存以便收起时精确复原"
    # 登录态：按 rect 现算位移 + 存/还基线
    assert "reserveHeader" in src, "登录页顶部控制条必须有让位分支（r4 修复核心）"
    assert "querySelector('.site-header')" in src, "让位必须作用在 #authScreen .site-header 上"
    assert "getBoundingClientRect" in src and "BAR_HEIGHT_PX + GAP_PX" in src, (
        "位移量必须由条的真实高度现算，不得写死像素"
    )
    assert "headerBase" in src, "header 基线必须保存以便收起时精确复原"
    assert "translateY(" in src, "header 让位必须用 transform（不触发布局/不碰 #authScreen 内联样式）"
    # 层切换与视口变化都要重算
    assert "function relayout" in src and "addEventListener('resize', relayout)" in src, (
        "视口变化必须重算让位（条换行会变高）"
    )
    assert "relayout();" in src.split("function init")[1], (
        "登录层 style 变化（进入应用/登出）必须重算让位"
    )
    # 幂等：重复预留不得叠加
    assert "Object.prototype.hasOwnProperty.call(reservedBase, id)" in src, (
        "重复预留必须幂等（hasOwnProperty 守卫），否则 padding 会越加越高"
    )


# ---------------------------------------------------------------- sw.js 源码钉

def test_sw_source_policy_pins():
    src = _read(FRONTEND / "sw.js")
    # 令牌制运行时资产判定
    assert "searchParams.has('v')" in src
    assert "dest !== 'script' && dest !== 'style'" in src, "只允许 JS/CSS 进入 runtime 缓存"
    # 导航网络优先 + 快照兜底；且只有「壳」导航（非 API / 在 scope 内）才走该分支
    assert "request.mode === 'navigate'" in src
    assert "'network-first-shell'" in src and "'cache-first-runtime'" in src and "'network-only'" in src
    assert "isShellNavigation(request)" in src, (
        "导航分支必须走 isShellNavigation（r2 finding #2：任一 navigate 当壳会让 /api/ 响应顶掉快照）"
    )
    assert "isNavigation(request)) return 'network-first-shell'" not in src, "旧形态（任一导航即壳）不得回归"
    assert "isApiPath(url.pathname)" in src, "壳导航必须按 URL 排除 API 路径"
    assert "isHtmlResponse(fresh)" in src, "第二道闸：只有 text/html 响应才可写快照键"
    assert "registration.scope" in src, "快照键必须由 scope 相对推导（子路径部署同构）"
    # 第 2 轮 finding #1：提示标记键必须是新文档 id
    assert "event.resultingClientId || event.clientId" in src, (
        "标记键必须优先 resultingClientId（导航 fetch 的 clientId 是旧文档，回执恒 false）"
    )
    assert "snapshotClients[event.clientId]" not in src, "旧键位（event.clientId）不得回归"
    # 第 2 轮 finding #5：标记表须有活性对账
    assert "pruneSnapshotClients" in src and "matchAll(" in src, "标记表必须按存活 client 对账，防止无界增长"
    # 第 3 轮 finding #4（顺序竞态）：对账必须在离线兜底分支内、且在 mark 之后，
    # 并把本事件交付的 id 作为 keepId 排除；不得回到「fetch 监听里先对账」的旧形态。
    assert "pruneSnapshotClients(snapshotClientId(event))" in src, (
        "对账必须带 keepId（本事件交付的文档）——否则 matchAll 先返回时会误删新标记"
    )
    mark_at = src.index("markSnapshotClient(event);")
    prune_at = src.index("pruneSnapshotClients(snapshotClientId(event))")
    assert mark_at < prune_at, "必须先前 mark、后对账（旧形态对账早于 mark，实测回执为 false）"
    fetch_listener = src[src.index("addEventListener('fetch'"):]
    fetch_listener = fetch_listener[:fetch_listener.index("});")]
    assert "pruneSnapshotClients" not in fetch_listener, (
        "对账不得留在 fetch 监听入口（那里必然早于 mark）：这是第 3 轮实测失败的旧形态"
    )

    # 同源/GET 红线
    assert "location.origin" in src
    assert "== 'GET'" in src
    # 零额外出站红线
    assert "addEventListener('install'" not in src, "零 precache：不得有 install 拉取钩子"
    assert "importScripts" not in src
    # 优雅降级
    assert "503" in src, "未缓存资产离线必须回诚实 503，不得伪造成功"
    assert "throw e;" in src, "无快照时必须放行原始异常（浏览器原生离线页，不伪造内容）"
    # 提示协议与版本化缓存
    for needle in ("rf-query-offline-snapshot", "rf-offline-snapshot-state",
                   "rf-leave-offline-snapshot", "'rf-offline-snapshot'",
                   "rf-runtime-", "rf-shell-", TOKEN):
        assert needle in src, f"sw.js 缺少 {needle}"
    assert "caches.keys()" in src, "activate 必须清理旧版本缓存"


# ---------------------------------------------------------------- node 冒烟（CI 同款命令）

@pytest.mark.skipif(not os.path.exists(NODE), reason="node runtime not found")
def test_node_smoke_suite_green_under_the_ci_command():
    """行为钉：以 CI 同款命令（cwd=仓库根）实跑 W5 node 冒烟套件。

    25 个用例（语法/策略/fetch 行为/DOM 布局与恢复/让位几何/manifest/十语言）
    必须全绿；策略被撤销（缓存 /api/、跨源缓存、离线伪造）或让位被撤回
    （条重新压住登录三控件）都会让退出码或计数变化而变红。"""
    proc = subprocess.run(
        [NODE, "--test", "build/tests/w5_pwa_sw.test.cjs"],
        cwd=str(REPO), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=300,
    )
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out[-3000:]
    fail_m = re.search(r"#\s*fail\s+(\d+)", out)
    assert fail_m is None or int(fail_m.group(1)) == 0, out[-3000:]
    counts = [int(m) for m in re.findall(r"#\s*(?:pass|tests)\s+(\d+)", out)]
    assert counts and max(counts) >= 25, (
        f"25 个用例的计数不得缩水，实际汇总：{counts}"
    )


# ---------------------------------------------------------------- 真实 DOM（playwright）

@pytest.mark.skipif(
    not (os.environ.get("REVIEWFLOW_PLAYWRIGHT") and os.environ.get("PLAYWRIGHT_BROWSERS_PATH")),
    reason="playwright runtime not configured (set REVIEWFLOW_PLAYWRIGHT and PLAYWRIGHT_BROWSERS_PATH)",
)
@pytest.mark.skipif(not os.path.exists(NODE), reason="node runtime not found")
def test_login_controls_are_clickable_while_banner_is_up_real_dom():
    """第 4 轮评审 medium 原话要求「补真实 DOM 钉子（登录页三控件可点击）」。

    真浏览器（playwright/Chromium）里断言：条出现后，语言 / 深色浅色 /
    暂停动效三控件的中心点不在条内、能真实点击并各自生效；应用态侧栏与
    主区顶部控制条也不被压；关闭后 header 精确回到原位。
    skipif 与 MM7-03 同款（CI 的 Provision playwright 步骤提供这两个变量），
    本地未配置时跳过——不伪造通过。"""
    proc = subprocess.run(
        [NODE, "build/tests/w5_pwa_banner_browser.cjs"],
        cwd=str(REPO), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=600,
    )
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out[-4000:]
    assert "PASS W5 offline banner never occludes login controls" in proc.stdout, out[-4000:]

