# -*- coding: utf-8 -*-
"""r13「批次统计显示」泳道静态钉（同型坏模式全库归零 + 统计行参数形态）。

红钉来源：``custom_frontend/unified_import_workspace.js:111`` 把 ``U.literal`` 返回的
``<span>`` 元素塞进 ``T()`` 参数，而 ``custom_frontend/index.html:5151`` 的
``RFLang.t`` 对每个 ``{n}`` 参数做 ``String()`` → 批次卡片五个统计数显示为
``[object HTMLSpanElement]``，十语言同坏；2.1.3（a080fde）传纯数字，属回归。
行为面由 ``build/tests/test_r13_import_counts_browser.cjs`` 用真实 Chromium 钉住
（数字可见、无 ``[object``、en/es/zh 三种语言模板与单位）。本文件钉住静态面：

1. **全库同型坏模式归零**：``custom_frontend/*.js`` 与 ``index.html`` 内联脚本里，
   凡 ``T(...)`` / ``RFLang.t(...)`` 的非模板参数，都不得是元素构造调用。
   元素构造器按各文件实际绑定解析（``n=U.node`` / ``n=UI.node`` / ``l=U.literal``
   等别名 + ``literal`` / ``el`` / ``node`` / ``document.createElement``），
   因此 ``index.html:6632`` 里同名的**数值** ``n(k)`` 计数函数不会被误判。
2. **批次统计行本身**：模板五个占位不变；五个参数都是原始值（``String(...)`` 包裹的
   数字+单位），不是元素；单位仍在（前四项 records、末项候选组 groups）——
   即 2.1.3 的「数字可见」与 r10 导入12项 #7-A 的「每项带单位」同时保留。

运行：``python -m pytest build/tests/test_r13_import_counts_static.py -q``
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
FRONTEND = REPO / "custom_frontend"
LANE_FILE = FRONTEND / "unified_import_workspace.js"
COUNTS_TEMPLATE = (
    "Source records: {0} · Activated: {1} · Staged: {2} · "
    "Metadata proposals: {3} · Pending candidates: {4}"
)

# 无条件视为元素构造器的名字（全库同名定义都是返回 DOM 元素的工厂；
# 见 import_export_workspace.js:8、report_evidence.js:6、workflow_controls.js:31）。
STATIC_ELEMENT_HELPERS = {"literal", "el", "node"}
# 形如 n=U.node / l=UI.literal / box=ImportExportWorkspace.node 的别名。
HELPER_BINDING = re.compile(
    r"\b([A-Za-z_$][\w$]*)\s*=\s*(?:U|UI|ImportExportWorkspace)\s*\.\s*(?:node|literal)\b"
)
CREATE_ELEMENT = re.compile(r"^document\s*\.\s*createElement\b")


def _mask_literals(source: str) -> str:
    """把注释 / 字符串 / 模板 / 正则字面量内容替换成空格（等长、保留换行），
    使括号与逗号扫描不被字面量内部字符干扰。"""
    out = list(source)
    i, n = 0, len(source)
    prev = ""
    while i < n:
        ch = source[i]
        nxt = source[i + 1] if i + 1 < n else ""
        if ch == "/" and nxt == "/":
            j = source.find("\n", i)
            j = n if j < 0 else j
            for k in range(i, j):
                out[k] = " "
            i = j
            continue
        if ch == "/" and nxt == "*":
            j = source.find("*/", i + 2)
            j = n if j < 0 else j + 2
            for k in range(i, j):
                if source[k] != "\n":
                    out[k] = " "
            i = j
            continue
        if ch in "'\"":
            quote, j = ch, i + 1
            while j < n:
                if source[j] == "\\":
                    j += 2
                    continue
                if source[j] == quote:
                    j += 1
                    break
                j += 1
            for k in range(i, j):
                if source[k] != "\n":
                    out[k] = " "
            i, prev = j, quote
            continue
        if ch == "`":
            j, depth = i + 1, 0
            while j < n:
                if source[j] == "\\":
                    j += 2
                    continue
                if source[j] == "$" and j + 1 < n and source[j + 1] == "{":
                    depth, j = depth + 1, j + 2
                    continue
                if source[j] == "}" and depth:
                    depth, j = depth - 1, j + 1
                    continue
                if source[j] == "`" and depth == 0:
                    j += 1
                    break
                j += 1
            for k in range(i, j):
                if source[k] != "\n":
                    out[k] = " "
            i, prev = j, "`"
            continue
        if ch == "/" and (prev == "" or prev in "(,=:[!&|?{};+-*%<>~^"):
            j, in_class = i + 1, False
            while j < n:
                c = source[j]
                if c == "\\":
                    j += 2
                    continue
                if c == "[":
                    in_class = True
                elif c == "]":
                    in_class = False
                elif c == "/" and not in_class:
                    j += 1
                    break
                elif c == "\n":
                    break
                j += 1
            for k in range(i, j):
                if source[k] != "\n":
                    out[k] = " "
            i, prev = j, "/"
            continue
        if not ch.isspace():
            prev = ch
        i += 1
    return "".join(out)


def _top_level_args(masked: str, open_paren: int) -> list[tuple[int, int]]:
    """open_paren 指向 '('；返回括号内按顶层逗号切分的参数区间。"""
    depth, end = 0, None
    for i in range(open_paren, len(masked)):
        if masked[i] == "(":
            depth += 1
        elif masked[i] == ")":
            depth -= 1
            if depth == 0:
                end = i
                break
    assert end is not None, "unbalanced call"
    spans, last, depth = [], open_paren + 1, 0
    for i in range(open_paren + 1, end):
        c = masked[i]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == "," and depth == 0:
            spans.append((last, i))
            last = i + 1
    if masked[last:end].strip() or spans:
        spans.append((last, end))
    return spans


def _t_calls(masked: str) -> list[int]:
    """返回所有 T( / RFLang.t( 调用中 '(' 的下标（跳过成员访问与标识符内部）。"""
    positions = []
    for pattern in (r"(?<![\w$.])T\s*\(", r"\bRFLang\s*\.\s*t\s*\("):
        positions.extend(m.end() - 1 for m in re.finditer(pattern, masked))
    return sorted(positions)


def _element_helper_names(source: str) -> set[str]:
    return STATIC_ELEMENT_HELPERS | set(HELPER_BINDING.findall(source))


def _element_arg_hits(source: str) -> list[tuple[int, int, str]]:
    """返回 (行号, 参数序号, 参数源码) —— 参数是元素构造调用的全部位置。"""
    masked = _mask_literals(source)
    helpers = _element_helper_names(source)
    ctor = re.compile(
        r"^(?:" + "|".join(sorted(map(re.escape, helpers))) + r")\s*\("
    )
    hits = []
    for open_paren in _t_calls(masked):
        spans = _top_level_args(masked, open_paren)
        for index, (a, b) in enumerate(spans):
            if index == 0:
                continue
            arg = masked[a:b].strip()
            if not arg:
                continue
            if ctor.match(arg) or CREATE_ELEMENT.match(arg):
                hits.append((source.count("\n", 0, a) + 1, index, source[a:b].strip()))
    return hits


def _frontend_sources() -> list[tuple[str, str]]:
    """custom_frontend/*.js 全量 + index.html 的内联 <script> 块。"""
    sources = [(p.relative_to(REPO).as_posix(), p.read_text(encoding="utf-8"))
               for p in sorted(FRONTEND.glob("*.js"))]
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    for block in re.finditer(r"<script\b[^>]*>(.*?)</script>", html, re.S):
        body = block.group(1)
        if "T(" not in body:
            continue
        base_line = html.count("\n", 0, block.start(1)) + 1
        # 行号按块内偏移还原为 index.html 的绝对行号，便于定位。
        shifted = "\n".join([""] * (base_line - 1)) + body
        sources.append(("custom_frontend/index.html", shifted))
    return sources


def _counts_statement() -> str:
    """取出 unified_import_workspace.js 里批次卡片统计行那一条语句。"""
    text = LANE_FILE.read_text(encoding="utf-8")
    marker = text.index(COUNTS_TEMPLATE)
    start = text.rfind("\n", 0, marker) + 1      # 语句所在行（该文件一行一函数）
    end = text.find("\n", marker)
    line = text[start:end if end >= 0 else len(text)]
    # 若该行以块注释结尾开头（说明写在统计行上方），去掉注释尾巴——否则词法器
    # 把注释里的 "* /" 当成正则起始，吞掉后面的 T( 调用。
    return line.split("*/", 1)[1] if "*/" in line else line


def test_no_element_argument_to_translator_anywhere():
    """全库：T()/RFLang.t() 的任何非模板参数都不得是元素构造调用。"""
    offenders = []
    for name, source in _frontend_sources():
        for line, index, snippet in _element_arg_hits(source):
            offenders.append(f"{name}:{line} arg[{index}] = {snippet}")
    assert not offenders, (
        "T()/RFLang.t() 参数里塞了 DOM 元素（RFLang.t 会 String() 化成 [object …]）：\n  "
        + "\n  ".join(offenders)
    )


def test_batch_counts_call_has_primitive_arguments_and_units():
    statement = _counts_statement()
    masked = _mask_literals(statement)
    open_paren = _t_calls(masked)[0]
    args = [statement[a:b].strip() for a, b in _top_level_args(masked, open_paren)]
    assert len(args) == 6, f"统计行应传 1 个模板 + 5 个数字参数，实际 {len(args)} 个：{statement}"
    for index, arg in enumerate(args[1:], start=1):
        assert arg.startswith("String("), (
            f"第 {index} 个统计参数必须是原始值（String(...)），不得是元素/对象：{arg}"
        )
    # 导入12项 #7-A：前四项是记录、末项是候选组——单位不能随修复一起丢失。
    assert statement.count("T('records')") == 4, "前四项统计必须保留 records 单位"
    assert statement.count("T('groups')") == 1, "末项（候选组）必须保留 groups 单位"
    for field in ("raw_source_records", "active_records", "pending_records",
                  "metadata_review_count", "pending_candidate_groups"):
        assert f"item.{field}" in statement, f"统计行缺少字段 {field}：{statement}"


def test_scanner_positive_and_negative_controls():
    """扫描器自检：既不放过元素参数，也不冤枉同名数值函数（index.html:6632 形状）。"""
    bad = ("const l=U.literal;"
           "function f(item){counts.textContent=T('x {0}', l('span',(item.n||0)+' '+T('records')));}")
    assert _element_arg_hits(bad), "正控失败：l('span', …) 作为 T 参数必须被识别"
    bound = "const n=U.node;T('x {0}', n('span','1'));"
    assert _element_arg_hits(bound), "正控失败：绑定为 U.node 的 n(…) 必须被识别"
    # index.html:6632 的真实形状：n 是本地数值计数函数，不是元素构造器。
    good = ("const n=k=>Object.values(S.decisions).filter(d=>d.decision===k).length;"
            "el('span',{text:T('纳入 {0}', n('include'))});")
    assert not _element_arg_hits(good), "负控失败：数值计数函数 n() 不得被误判为元素"


def test_batch_counts_template_unchanged():
    """模板是十语言词典键（workflow_locale.js:155），修复不得改模板。"""
    assert COUNTS_TEMPLATE in LANE_FILE.read_text(encoding="utf-8")
    locale = (FRONTEND / "workflow_locale.js").read_text(encoding="utf-8")
    row = next((line for line in locale.splitlines() if COUNTS_TEMPLATE in line), "")
    assert row, "统计行模板在 workflow_locale.js 中找不到对应词典行"
    assert row.count('"') >= 20, f"词典行应为十语言：{row[:120]}"
