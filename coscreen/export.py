"""决策导出 / 回导 / RIS 导出 —— CSV 契约见 SPEC.md §6，RIS 见 SPEC §6bis，
复筛导出（decisions2 / 高亮 Markdown）见 SPEC.md §14。

- 导出列固定为 DECISION_COLUMNS，按 import_order 排序，保证确定性；
- 导出的 zotero_key 与导入值逐一对应（主键原样输出）；
- "导出 → 重新导入" 必须无损还原决策（import_decisions_csv）；
- 回导兼容旧 6 列文件（缺 tags 列按 "" 处理）与 7 列新文件（tags 原样还原）；
- export_articles_ris 把按决策筛选后的文献写为标准 RIS（Zotero 可再导入）；
- 复筛扩展（SPEC §14，additive）：export_stage2_decisions 导出复筛决策 CSV
  （7 列同初筛）、export_highlights_markdown 导出高亮笔记 Markdown、
  export_articles_ris 以 decision_source="stage2" 导出复筛纳入 RIS。
- 编码扩展（SPEC §15.3，additive）：export_coding_matrix 导出「文献 × 维度」
  矩阵 CSV、export_coding_long 导出长表 CSV、export_coding_notes_markdown
  导出备注/OCR Markdown；三者均为 utf-8-sig、按 import_order 排序、确定性。
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from coscreen.research_trace import TraceContext
from pathlib import Path

import pandas as pd

from coscreen import db as db_mod
from coscreen.fulltext import COLOR_LABELS, list_highlights, list_stage2_decisions
from coscreen.models import Article

StrPath = str | Path

DECISION_COLUMNS = [
    "zotero_key",
    "title",
    "decision",
    "exclusion_reason",
    "notes",
    "tags",
    "screened_at",
]


def export_decisions(db_path: StrPath, out_csv: StrPath) -> Path:
    """把 decisions 表导出为 CSV（按文献导入顺序）；返回写入路径。"""
    decisions = db_mod.list_decisions(db_path)  # 已按 import_order 排序
    titles = {a.zotero_key: a.title for a in db_mod.list_articles(db_path, include_duplicates=True)}
    df = pd.DataFrame(
        [
            {
                "zotero_key": d["zotero_key"],
                "title": titles.get(d["zotero_key"], ""),
                "decision": d["decision"],
                "exclusion_reason": d.get("exclusion_reason", "") or "",
                "notes": d.get("notes", "") or "",
                "tags": d.get("tags", "") or "",
                "screened_at": d["screened_at"],
            }
            for d in decisions
        ],
        columns=DECISION_COLUMNS,
    )
    out = Path(out_csv)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False, encoding="utf-8-sig")
    return out


def export_stage2_decisions(db_path: StrPath, out_csv: StrPath) -> Path:
    """把复筛决策（stage2_decisions，SPEC §14）导出为 CSV；列与初筛完全相同。

    按文献导入顺序排序（确定性）；返回写入路径。
    """
    decisions = list_stage2_decisions(db_path)  # 已按 import_order 排序
    titles = {
        a.zotero_key: a.title
        for a in db_mod.list_articles(db_path, include_duplicates=True)
    }
    df = pd.DataFrame(
        [
            {
                "zotero_key": d["zotero_key"],
                "title": titles.get(d["zotero_key"], ""),
                "decision": d["decision"],
                "exclusion_reason": d.get("exclusion_reason", "") or "",
                "notes": d.get("notes", "") or "",
                "tags": d.get("tags", "") or "",
                "screened_at": d["screened_at"],
            }
            for d in decisions
        ],
        columns=DECISION_COLUMNS,
    )
    out = Path(out_csv)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False, encoding="utf-8-sig")
    return out


def load_decisions_csv(path: StrPath) -> pd.DataFrame:
    """读取决策 CSV：校验必需列存在，decision 统一小写。"""
    df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    missing = [c for c in ("zotero_key", "decision") if c not in df.columns]
    if missing:
        raise ValueError(f"决策 CSV 缺少必需列: {missing}，实际列为 {list(df.columns)}")
    df["decision"] = df["decision"].str.strip().str.lower()
    return df


def import_decisions_csv(db_path: StrPath, csv_path: StrPath, *, trace_context: TraceContext | None = None) -> int:
    """把导出的决策 CSV 恢复进 decisions 表（按 zotero_key upsert，原子）。

    - 整批校验（decision 合法、zotero_key 已在 articles 表）通过后单事务
      批量写入，不留部分导入状态；
    - zotero_key 不在库时 ValueError（列出未知键，防止错库回导）；
    - 兼容 7 列新文件（含 tags，原样还原）与旧 6 列文件（缺 tags 列按 "" 处理）；
    - 返回恢复的决策条数。
    """
    if trace_context is None:
        df = load_decisions_csv(csv_path)
        return db_mod.restore_decisions(db_path, df.to_dict("records"))
    import hashlib
    import io
    import uuid
    from dataclasses import replace

    # Hash and parse the same bytes; the trusted caller supplies task/actor identity.
    payload = Path(csv_path).read_bytes()
    df = load_decisions_csv(io.BytesIO(payload))
    context = replace(trace_context, source="import",
                      source_record_id="sha256:" + hashlib.sha256(payload).hexdigest(),
                      batch_id=trace_context.batch_id or str(uuid.uuid4()))
    return db_mod.restore_decisions(db_path, df.to_dict("records"), trace_context=context)


# ---------------------------------------------------------------------------
# RIS 导出（SPEC §6bis）：按决策筛选文献，写出 Zotero 可再导入的标准 RIS
# ---------------------------------------------------------------------------

#: Zotero item_type -> RIS TY 参考类型映射；未识别/缺失类型回退 JOUR。
_RIS_TYPE_MAP = {
    "journalArticle": "JOUR",
    "bookSection": "CHAP",
    "report": "RPRT",
    "thesis": "THES",
    "webpage": "ELEC",
}


def _ris_type(item_type: str) -> str:
    """Zotero item_type 映射为 RIS TY 类型；未识别/缺失回退 JOUR。"""
    return _RIS_TYPE_MAP.get((item_type or "").strip(), "JOUR")


def _ris_one_line(value: object) -> str:
    """把字段值规整为单行文本：RIS 一个标签一行，内部换行会撕裂记录。"""
    text = "" if value is None else str(value)
    return re.sub(r"\s*[\r\n]+\s*", " ", text).strip()


def _ris_split(value: str, sep: str) -> list[str]:
    """按分隔符切分多值字段（作者用";"、标签用","），去首尾空白、丢弃空段。"""
    return [seg.strip() for seg in re.split(sep, value or "") if seg.strip()]


def _ris_record(art: Article, tags: str) -> list[str]:
    """一篇文献 -> RIS 记录的行列表（不含行尾符），以 ER 终止行收尾。

    字段顺序：TY/TI/AU（作者按";"切分，每段一行）/JO/PY/DO（非空才写）/
    AB（非空才写）/UR（非空才写）/KW（tags 按","切分，每段一行）/ID/ER。
    """
    lines = [
        f"TY  - {_ris_type(art.item_type)}",
        f"TI  - {_ris_one_line(art.title)}",
    ]
    lines += [f"AU  - {_ris_one_line(a)}" for a in _ris_split(art.authors, ";")]
    lines.append(f"JO  - {_ris_one_line(art.journal)}")
    lines.append(f"PY  - {_ris_one_line(art.year)}")
    if art.doi:
        lines.append(f"DO  - {_ris_one_line(art.doi)}")
    if art.abstract:
        lines.append(f"AB  - {_ris_one_line(art.abstract)}")
    if art.url:
        lines.append(f"UR  - {_ris_one_line(art.url)}")
    lines += [f"KW  - {_ris_one_line(t)}" for t in _ris_split(tags, ",")]
    lines.append(f"ID  - {_ris_one_line(art.zotero_key)}")
    lines.append("ER  - ")
    return lines


def export_articles_ris(
    db_path: StrPath,
    out_ris: StrPath,
    decision_filter: str = "include",
    decision_source: str = "stage1",
) -> Path:
    """把按决策筛选后的非重复文献导出为 RIS 文件；返回写入路径。

    - 只保留 decision == decision_filter 的文献；无决策的条目一律跳过；
    - decision_source（SPEC §14 additive）："stage1"（默认）读初筛 decisions
      表，"stage2" 读复筛 stage2_decisions 表；其他值 ValueError；
    - 顺序 = 文献导入顺序（import_order），两次调用字节级一致（确定性）；
    - UTF-8 无 BOM，CRLF 行尾（RIS 惯例）；记录间以一个空行分隔；
    - 空选择写空文件（RIS 无表头概念），同样返回路径。
    """
    if decision_source == "stage1":
        decisions = {d["zotero_key"]: d for d in db_mod.list_decisions(db_path)}
    elif decision_source == "stage2":
        decisions = {d["zotero_key"]: d for d in list_stage2_decisions(db_path)}
    else:
        raise ValueError(
            f"未知 decision_source: {decision_source!r}，必须为 'stage1' 或 'stage2'"
        )
    selected = [
        art
        for art in db_mod.list_articles(db_path)  # 非重复、按 import_order
        if decisions.get(art.zotero_key, {}).get("decision") == decision_filter
    ]
    blocks = [
        "\r\n".join(
            _ris_record(art, decisions[art.zotero_key].get("tags", "") or "")
        )
        for art in selected
    ]
    text = "\r\n\r\n".join(blocks)
    if text:
        text += "\r\n"  # 末条 ER 行的收尾换行
    out = Path(out_ris)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8", newline="")
    return out


# ---------------------------------------------------------------------------
# 高亮笔记 Markdown 导出（SPEC §14）
# ---------------------------------------------------------------------------

def export_highlights_markdown(db_path: StrPath, out_md: StrPath) -> Path:
    """把高亮笔记导出为 Markdown；返回写入路径。

    结构：每篇有高亮的文献一个二级标题（按文献导入顺序），其下按页码升序
    分页罗列高亮（同页按创建顺序），每条含颜色标签、原文选段与可选笔记。
    内容不含时间戳等易变信息：同一数据库状态两次导出字节级一致（确定性）。
    """
    highlights = list_highlights(db_path)  # 全库，按 (page, id) 排序
    all_articles = db_mod.list_articles(db_path, include_duplicates=True)
    titles = {a.zotero_key: (a.title or "（无标题）") for a in all_articles}
    orders = {a.zotero_key: i for i, a in enumerate(all_articles)}

    by_key: dict[str, list] = {}
    for hl in highlights:
        by_key.setdefault(hl.zotero_key, []).append(hl)
    # 文献按导入顺序排列；无对应文献条目的高亮（理论不存在）垫底按键序
    ordered_keys = sorted(
        by_key,
        key=lambda k: (orders.get(k, len(orders)), k),
    )

    lines: list[str] = [
        "# 高亮笔记（全文复筛标注）",
        "",
        f"- 文献数：{len(ordered_keys)}；高亮数：{len(highlights)}",
    ]
    for key in ordered_keys:
        rows = by_key[key]
        lines.append("")
        lines.append(f"## {titles.get(key, '（无标题）')}")
        lines.append("")
        lines.append(f"- zotero_key：`{key}`")
        current_page = None
        for hl in rows:
            if hl.page != current_page:
                current_page = hl.page
                lines.append("")
                lines.append(f"### 第 {hl.page} 页")
                lines.append("")
            color_label = COLOR_LABELS.get(hl.color, hl.color)
            lines.append(f"- 【{color_label}】{hl.text.replace(chr(10), ' ')}")
            if (hl.note or "").strip():
                lines.append(f"  - 笔记：{hl.note.strip()}")
    text = "\n".join(lines)
    if highlights:
        text += "\n"

    out = Path(out_md)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    return out


# ---------------------------------------------------------------------------
# 编码导出（SPEC §15.3）：矩阵 CSV / 长表 CSV / 备注 Markdown
# ---------------------------------------------------------------------------

#: 编码矩阵 CSV 的前两列（其后每维度一列，列名 = 维度名）
CODING_MATRIX_HEAD_COLUMNS = ["zotero_key", "title"]

#: 编码长表 CSV 的固定列（SPEC §15.3）
CODING_LONG_COLUMNS = [
    "zotero_key",
    "title",
    "dimension",
    "value",
    "note_text",
    "images_sha256",
    "ocr_texts",
]

#: 多图字段的连接符（sha256 十六进制串不含 "|"，可无损还原）
_CODING_IMG_SEP = "|"

#: 多图 OCR 文本的连接符（OCR 文本可含换行，用空行分段便于阅读）
_CODING_OCR_SEP = "\n\n"


def _coding_layer():
    """延迟导入编码数据层 coscreen.coding（SPEC §15.1）。

    延迟导入（而非模块顶层）有两个理由：其一，编码层由并行任务实现，本模块
    在编码层缺席时仍可被导出页导入并给出友好提示，而不是 ImportError 崩溃；
    其二，导出页与 CLI 的既有路径（决策/RIS/高亮）不因此多一个模块依赖。
    """
    from coscreen import coding

    return coding


def _notes_index(db_path: StrPath) -> dict[tuple[int, str], dict]:
    """全库备注索引 ``{(dim_id, zotero_key): {"id": note_id, "text": 备注文字}}``。

    优先走编码数据层的 ``list_coding_notes``（SPEC §15.1 的读侧工具，一次查询
    取回全库备注）；数据层缺席（ImportError）或没有该读接口时，按 §15.1 的
    DDL 只读直查 coding_notes（表不存在时返回空索引，不建表）。
    """
    try:
        coding = _coding_layer()
    except ImportError:
        coding = None
    reader = getattr(coding, "list_coding_notes", None) if coding is not None else None
    if callable(reader):
        return {
            (int(row["dimension_id"]), str(row["zotero_key"])): {
                "id": int(row["id"]),
                "text": row.get("text") or "",
            }
            for row in (reader(db_path) or [])
        }

    import sqlite3
    from contextlib import closing

    try:
        with closing(sqlite3.connect(str(db_path))) as conn:
            rows = conn.execute(
                "SELECT id, dimension_id, zotero_key, text FROM coding_notes "
                "ORDER BY dimension_id, id"
            ).fetchall()
    except sqlite3.Error:  # 表不存在 / 库不可读：视作没有备注
        return {}
    return {
        (int(dim_id), str(key)): {"id": int(note_id), "text": text or ""}
        for note_id, dim_id, key, text in rows
    }


def read_coding_notes(db_path: StrPath, key: str) -> dict[int, dict]:
    """读取某文献的备注行：``{dim_id: {"id": note_id, "text": 备注文字}}``。

    供编码导出与编码工作台（ui/9）回显使用；读侧实现见 :func:`_notes_index`。
    """
    return {
        dim_id: row
        for (dim_id, note_key), row in _notes_index(db_path).items()
        if note_key == key
    }


def _coding_row_keys(
    db_path: StrPath, coding, notes: dict[tuple[int, str], dict]
) -> list[str]:
    """编码导出的行键（文献）：编码队列 ∪ 已有编码数据的文献，按 import_order。

    SPEC §15.3 要求行序 = import_order；行集合取「编码队列」（工作台的编码
    范围，coding_queue 缺省读设置）与「已有编码值或备注的文献」的并集——
    队列外的历史数据不因队列模式切换而在导出中静默消失。
    """
    queue = set(coding.coding_queue(db_path))
    note_keys = {key for (_dim, key) in notes}
    valued_keys = set(coding.get_coding_values_all(db_path))  # 单连接全量，替代逐篇探测
    keys: list[str] = []
    for art in db_mod.list_articles(db_path):  # 已按 import_order 排序
        key = art.zotero_key
        if key in queue or key in note_keys or key in valued_keys:
            keys.append(key)
    return keys


def _coding_images_index(db_path: StrPath, coding) -> dict[int, list[dict]]:
    """note_id -> 图片列表：单连接全量取回（list_note_images(None)），按 id 升序。"""
    index: dict[int, list[dict]] = {}
    for image in coding.list_note_images(db_path) or []:
        index.setdefault(int(image["note_id"]), []).append(image)
    for images in index.values():
        images.sort(key=lambda img: int(img.get("id", 0)))
    return index


def _coding_notes_and_images(
    dim_id: int,
    key: str,
    notes: dict[tuple[int, str], dict],
    images_by_note: dict[int, list[dict]],
) -> tuple[str, list[dict]]:
    """(备注文字, 图片列表)；图片来自批量索引，顺序按图片 id，保证导出确定性。"""
    row = notes.get((int(dim_id), key))
    if not row:
        return "", []
    return row.get("text", "") or "", images_by_note.get(int(row["id"]), [])


def _coding_titles(db_path: StrPath) -> dict[str, str]:
    """zotero_key -> 标题（含重复文献列的兜底映射，缺标题回退键本身）。"""
    return {
        a.zotero_key: (a.title or a.zotero_key)
        for a in db_mod.list_articles(db_path, include_duplicates=True)
    }


def export_coding_matrix(db_path: StrPath, out_csv: StrPath) -> Path:
    """编码矩阵 CSV（SPEC §15.3）：行 = 文献、列 = 维度；返回写入路径。

    - 首两列 zotero_key / title，其后每个维度一列（列名 = 维度名，§15.1 保证唯一）；
    - 多选维度的值按 "|" 连接（数据层即以该形式存储，原样输出）；
    - 行序 = 文献 import_order（见 _coding_row_keys）；空值写空串；
    - utf-8-sig（Excel 直接打开中文不乱码）；同一数据库状态两次导出字节级一致。
    """
    coding = _coding_layer()
    dims = coding.list_dimensions(db_path)  # 已按 (section, position, id) 排序
    notes = _notes_index(db_path)
    titles = _coding_titles(db_path)
    values_all = coding.get_coding_values_all(db_path)  # 单连接批量（P2-10）
    columns = CODING_MATRIX_HEAD_COLUMNS + [d["name"] for d in dims]
    rows = []
    for key in _coding_row_keys(db_path, coding, notes):
        values = values_all.get(key, {})
        row = {"zotero_key": key, "title": titles.get(key, "")}
        for dim in dims:
            row[dim["name"]] = values.get(int(dim["id"]), "") or ""
        rows.append(row)
    df = pd.DataFrame(rows, columns=columns)
    out = Path(out_csv)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False, encoding="utf-8-sig")
    return out


def export_coding_long(db_path: StrPath, out_csv: StrPath) -> Path:
    """编码长表 CSV（SPEC §15.3）：每「文献 × 维度」一行；返回写入路径。

    列固定为 CODING_LONG_COLUMNS（zotero_key / title / dimension / value /
    note_text / images_sha256 / ocr_texts）：即矩阵的 melt 形式，未填写的
    维度也有空行（保持行长 = 文献数 × 维度数，便于 SPSS/R 直接长宽变换）。
    多图时 images_sha256 以 "|" 连接、ocr_texts 以空行连接（按图片 id 升序）。
    """
    coding = _coding_layer()
    dims = coding.list_dimensions(db_path)
    notes = _notes_index(db_path)
    titles = _coding_titles(db_path)
    values_all = coding.get_coding_values_all(db_path)  # 单连接批量（P2-10）
    images_by_note = _coding_images_index(db_path, coding)
    rows = []
    for key in _coding_row_keys(db_path, coding, notes):
        values = values_all.get(key, {})
        for dim in dims:
            dim_id = int(dim["id"])
            note_text, images = _coding_notes_and_images(dim_id, key, notes, images_by_note)
            rows.append(
                {
                    "zotero_key": key,
                    "title": titles.get(key, ""),
                    "dimension": dim["name"],
                    "value": values.get(dim_id, "") or "",
                    "note_text": note_text,
                    "images_sha256": _CODING_IMG_SEP.join(
                        (img.get("sha256", "") or "") for img in images
                    ),
                    "ocr_texts": _CODING_OCR_SEP.join(
                        (img.get("ocr_text", "") or "").strip()
                        for img in images
                        if (img.get("ocr_text", "") or "").strip()
                    ),
                }
            )
    df = pd.DataFrame(rows, columns=CODING_LONG_COLUMNS)
    out = Path(out_csv)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False, encoding="utf-8-sig")
    return out


def export_coding_notes_markdown(db_path: StrPath, out_md: StrPath) -> Path:
    """编码备注 Markdown（SPEC §15.3）：按文献分节，含备注文字与图片 OCR 文本。

    结构：有备注（文字或图片）的文献一个二级标题（按 import_order），其下
    按 (section, position, id) 序罗列有内容的维度（三级标题 = 维度名），每条
    含备注文字与逐图「文件名 / sha256 / 识别状态 / OCR 文本」。不含时间戳等
    易变信息：同一数据库状态两次导出字节级一致（确定性）。
    """
    coding = _coding_layer()
    dims = coding.list_dimensions(db_path)
    all_notes = _notes_index(db_path)
    titles = _coding_titles(db_path)

    sections: list[tuple[str, list[str]]] = []
    n_notes = 0
    n_images = 0
    for key in _coding_row_keys(db_path, coding, all_notes):
        blocks: list[str] = []
        for dim in dims:
            note_text, images = _coding_notes_and_images(
                db_path, coding, int(dim["id"]), key, all_notes
            )
            if not note_text.strip() and not images:
                continue  # 该维度无任何备注内容：不产生小节
            n_notes += 1
            n_images += len(images)
            block = ["", f"### {dim['name']}", ""]
            if (dim.get("unit") or "").strip():
                block.append(f"- 单位：{dim['unit']}")
                block.append("")
            block.append(note_text.strip() or "（无备注文字）")
            for i, img in enumerate(images, start=1):
                block.append("")
                block.append(
                    f"**图片 {i}**｜`{Path(img.get('path', '') or '').name}`"
                    f"｜sha256 `{img.get('sha256', '') or ''}`"
                    f"｜识别状态：{img.get('ocr_status', '') or 'pending'}"
                )
                block.append("")
                block.append((img.get("ocr_text", "") or "").strip() or "（无 OCR 文本）")
            blocks.extend(block)
        if blocks:
            sections.append((key, blocks))

    lines: list[str] = [
        "# 编码备注（数据提取阶段）",
        "",
        f"- 文献数：{len(sections)}；维度备注数：{n_notes}；图片数：{n_images}",
    ]
    for key, blocks in sections:
        lines.append("")
        lines.append(f"## {titles.get(key, key)}")
        lines.append("")
        lines.append(f"- zotero_key：`{key}`")
        lines.extend(blocks)
    text = "\n".join(lines)
    if sections:
        text += "\n"

    out = Path(out_md)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    return out


def coding_export_counts(db_path: StrPath) -> dict:
    """编码导出规模：``{"dimensions": 维度数, "studies": 矩阵行数}``。

    供导出页（ui/4）判断空态与生成下载文案；不写文件、不修改数据库。
    """
    coding = _coding_layer()
    return {
        "dimensions": len(coding.list_dimensions(db_path)),
        "studies": len(_coding_row_keys(db_path, coding, _notes_index(db_path))),
    }
