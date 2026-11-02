# -*- coding: utf-8 -*-
"""r10-u4 / F16：NAVIGATION_MAP.md 必须由 navigation_registry.js 再生成。

批准单 §四 F16（推荐=修复）：「以 navigation_registry.js 为唯一事实源重新生成
思维导图与计数（可写生成脚本防再脱节）」。本文件即那个生成脚本：

    python build/tests/test_r10_u4_f16_navigation_map.py --write

它按注册表重写 NAVIGATION_MAP.md 的两张 mindmap（§1 全站层级、§2 数据分析层级）
与 §2 的计数句；不加 --write 时只做断言。任何一侧单独变动（改注册表忘改文档、
手改文档忘改注册表）都会让本文件变红——这正是「防再脱节」的钉子。

破坏即红（每条对应一处原始缺陷）：
- 文档写死 31 个入口：test_method_count_matches_registry 红（文档 31 ≠ 注册表 35）；
- 思维导图缺 4 个实际注册入口（证据地图/GRADE/撰写稿件/活体综述）：
  test_every_registered_tool_is_in_the_mindmap 红；
- 有人手工往 mindmap 里塞注册表没有的节点（如把「图表与报告导出」当方法入口）：
  test_mindmap_is_registry_only 红（多出节点）；
- §1 页面名与注册表不一致（「导出决策」vs 注册表「导出结果」、「文献编码」vs
  「数据提取与编码」）：test_site_map_matches_registry 红。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
FRONTEND = REPO / "custom_frontend"
REGISTRY = FRONTEND / "navigation_registry.js"
MAP = FRONTEND / "NAVIGATION_MAP.md"
NODE = (
    os.environ.get("RF_NODE")
    or shutil.which("node")
    or r"C:\Users\16073\AppData\Local\hermes\node\node.exe"
)
pytestmark = pytest.mark.skipif(not os.path.exists(NODE), reason="node runtime not found")

HEADER1 = "## 1. 全站层级图（先确定语义，再实现）"
HEADER2 = "## 2. 数据分析层级图"

NOTE1 = (
    "“首页与设置”“轨迹”“帮助”为全局入口，不归入三个阶段。三个阶段是导航分组，"
    "不虚构新的业务页面。任务名是数据上下文，独立显示，不再充当功能菜单或路径层级。"
    "所有页面标题、侧栏和面包屑使用同一注册表。"
)

# 注册表里不是方法入口、但出现在方法页面内的上下文操作/录入变体，只在说明里点名，
# 不进 mindmap（进了就等于给注册表添了一个并不存在的入口）。
IN_PAGE_NOTE = (
    "“图表与报告导出”为方法页面内的上下文操作，保留原有导出处理，不新增独立计算页面，"
    "也不计入 {count} 个方法入口；“效应量录入与计算”里的录入方式变体"
    "（按原文的其他数据格式计算／单组标准化前后变化／由中位事件时间估计 HR 或发生率／"
    "由中位数估计各组均值和标准差／配对二分类结局／整群随机试验／零单元格敏感性分析）"
    "是同一入口内的表单切换，同样不单独计数。"
)


def registry() -> dict:
    """在 node vm 里跑真实注册表，返回 pages/stages/groups/tools（不手抄任何名字）。"""
    harness = r"""
const vm=require('vm'),fs=require('fs');
const ctx={window:{}};
vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'),ctx,{timeout:20000});
const R=ctx.window.RFNavigation;
if(!R)throw new Error('navigation_registry.js did not publish window.RFNavigation');
const pick=(list,keys)=>list.map(item=>Object.fromEntries(keys.map(k=>[k,item[k]])));
process.stdout.write(JSON.stringify({
  pages:pick(R.pages,['id','label','stage']),
  stages:pick(R.stages,['id','label','defaultPage']),
  groups:pick(R.groups,['id','label','description']),
  tools:pick(R.tools,['id','group','label'])
}));
"""
    out = subprocess.run([NODE, "-e", harness, str(REGISTRY)],
                         capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert out.returncode == 0, f"注册表加载失败：{out.stderr}"
    return json.loads(out.stdout)


def site_map(data: dict) -> str:
    """§1：页面按注册表的阶段分组生成；stage=null 的页面（首页/轨迹/帮助）列根下。"""
    lines = ["```mermaid", "mindmap", "  root((ReviewFlow))"]
    loose = [p for p in data["pages"] if p["stage"] is None and p["id"] != "home"]
    home = next((p for p in data["pages"] if p["id"] == "home"), None)
    if home:
        lines.append("    " + home["label"])
    for stage in data["stages"]:
        lines.append("    " + stage["label"])
        for page in data["pages"]:
            if page["stage"] == stage["id"]:
                lines.append("      " + page["label"])
    for page in loose:
        lines.append("    " + page["label"])
    lines.append("```")
    return "\n".join(lines)


def analysis_map(data: dict) -> str:
    """§2：模块(8) → 方法入口(35)，逐条取自注册表，顺序即注册表顺序。"""
    lines = ["```mermaid", "mindmap", "  root((数据分析))"]
    for group in data["groups"]:
        lines.append("    " + group["label"])
        for tool in data["tools"]:
            if tool["group"] == group["id"]:
                lines.append("      " + tool["label"])
    lines.append("```")
    return "\n".join(lines)


def fenced_after(text: str, header: str) -> str:
    start = text.index(header)
    fence = text.index("```mermaid", start)
    end = text.index("```", fence + 3) + 3
    return text[fence:end]


def section_bounds(text: str, header: str) -> tuple[int, int]:
    start = text.index(header)
    nxt = text.find("\n## ", start + len(header))
    return start, (len(text) if nxt < 0 else nxt + 1)


def render(data: dict) -> dict:
    count = len(data["tools"])
    return {
        "map1": site_map(data),
        "map2": analysis_map(data),
        "note1": NOTE1,
        "note2": (IN_PAGE_NOTE.format(count=count)
                  + f" 共 {len(data['groups'])} 个分析模块、{count} 个方法入口，"
                    f"与界面「全部方法 {count}」一致。"),
    }


def write_map() -> None:
    data = registry()
    text = MAP.read_text(encoding="utf-8")
    parts = {k: v for k, v in render(data).items()}
    s1, e1 = section_bounds(text, HEADER1)
    s2, e2 = section_bounds(text, HEADER2)
    section1 = f"{HEADER1}\n\n{parts['map1']}\n\n{parts['note1']}\n\n"
    # §2 保留原有的方法学说明段（Q/I²/τ² 等），只替换 mindmap 与计数说明首句所在段落。
    old2 = text[s2:e2]
    tail_start = old2.index("```", old2.index("```mermaid") + 3) + 3
    tail = old2[tail_start:]
    paragraphs = [p for p in tail.split("\n\n")]
    kept = [p for p in paragraphs if not p.strip().startswith("说明：")]
    section2 = (f"{HEADER2}\n\n{parts['map2']}\n\n说明：{parts['note2']}\n\n"
                + "\n\n".join(kept).lstrip("\n"))
    new = text[:s1] + section1 + section2 + text[e2:]
    # 文档全篇使用 LF。Path.write_text 默认按平台换行转换（Windows → CRLF），会把
    # 未改动的行一并改写，整篇 126 行都变成假 diff（对照组纪律不允许）。显式钉 LF。
    with MAP.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(new)


def _map_text() -> str:
    return MAP.read_text(encoding="utf-8")


def test_method_count_matches_registry():
    """§2 的入口计数必须等于注册表实际条数（缺陷形态：文档写 31、UI 显示 35）。"""
    data = registry()
    count = len(data["tools"])
    text = _map_text()
    s2, e2 = section_bounds(text, HEADER2)
    section = text[s2:e2]
    assert f"共 {len(data['groups'])} 个分析模块、{count} 个方法入口" in section
    assert f"不计入 {count} 个方法入口" in section
    assert "31 个方法入口" not in text, "文档不得再出现写死的 31 个入口"


def test_every_registered_tool_is_in_the_mindmap():
    """四个曾被漏掉的注册入口必须出现在 §2 mindmap（缺陷形态：缺 证据地图/GRADE/撰写稿件/活体综述）。"""
    data = registry()
    block = fenced_after(_map_text(), HEADER2)
    nodes = [line.strip() for line in block.splitlines() if line.startswith("      ")]
    for tool in data["tools"]:
        assert tool["label"] in nodes, f"方法入口未进入思维导图：{tool['label']}"
    for required in ("证据地图", "GRADE 评估", "撰写稿件", "活体综述"):
        assert required in nodes, f"注册表内确有此入口，思维导图不得遗漏：{required}"
    assert len([t for t in data["tools"] if t["label"] in nodes]) == len(data["tools"])


def test_mindmap_is_registry_only():
    """mindmap 里的方法节点集合必须恰为注册表集合（手工添加的“伪入口”即红）。"""
    data = registry()
    block = fenced_after(_map_text(), HEADER2)
    nodes = [line.strip() for line in block.splitlines() if line.startswith("      ")]
    known = [tool["label"] for tool in data["tools"]]
    extra = [node for node in nodes if node not in known]
    assert extra == [], f"思维导图出现注册表之外的方法节点：{extra}"
    assert sorted(nodes) == sorted(known), "方法节点数或顺序与注册表不一致（按注册表顺序生成）"


def test_site_map_matches_registry():
    """§1 的页面名必须与注册表一致（缺陷形态：「导出决策」「文献编码」两种旧名）。"""
    data = registry()
    block = fenced_after(_map_text(), HEADER1)
    for page in data["pages"]:
        assert page["label"] in block, f"页面未进入全站层级图：{page['label']}"
    for stale in ("导出决策", "文献编码"):
        assert stale not in block, f"全站层级图仍使用与注册表不一致的旧名：{stale}"
    for stage in data["stages"]:
        assert stage["label"] in block


@pytest.mark.skipif(os.environ.get("RF_R10_U4_WRITE_MAP") != "1", reason="只在显式要求时重写文档")
def test_document_is_regenerable_in_place():
    """--write 模式的回归口：重写一次后必须仍全绿（生成器与断言同源）。"""
    write_map()


def test_generator_preserves_line_endings(tmp_path):
    """再生成只改该改的行——不得因平台换行转换把未改动的行也改写。

    破坏即红：生成器曾用 MAP.write_text（Windows 按 os.linesep 落盘 → 全文 CRLF），
    未改动的 110 行也变成 diff，「与本条无关的既有输出逐位不变」当场失效。这里在
    临时目录跑同一个生成器，钉住「输出换行符与输入一致」。
    """
    source = MAP.read_bytes()
    lf_only = source.count(b"\n") - source.count(b"\r\n")
    assert source.count(b"\r\n") == 0, "NAVIGATION_MAP.md 本身必须是 LF（仓库现状）"
    assert lf_only > 0

    scratch = tmp_path / "NAVIGATION_MAP.md"
    scratch.write_bytes(source)
    original_map = globals()["MAP"]
    globals()["MAP"] = scratch
    try:
        write_map()
        written = scratch.read_bytes()
    finally:
        globals()["MAP"] = original_map
    assert written.count(b"\r\n") == 0, \
        "生成器把文档改写成了 CRLF：未改动的行也会进 diff（必须显式 newline='\\n'）"
    assert written == source, "对已与注册表一致的文档再生成一次，必须逐位不变"


if __name__ == "__main__":
    if "--write" in sys.argv:
        write_map()
        print(f"rewrote {MAP}")
    else:
        print(__doc__)
