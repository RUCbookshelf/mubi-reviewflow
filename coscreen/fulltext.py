"""复筛（全文阶段）与 PDF 阅读标记 —— PDF 管理 / 高亮 / 复筛决策（SPEC §14）。

- PDF 二进制存 ``data/pdfs/{单射化的 key}.pdf``（MM-07：截断清洗主干 +
  sha256(key) 短后缀，不同键不互相覆盖），``fulltexts`` 表只存元数据
  （路径/哈希/页数；filename 列存展示名）；
- ``save_pdf`` 校验 %PDF- 文件头，页数用 pypdf 统计（解析失败按 0 处理）；
- ``match_pdfs`` 按确定性链路匹配：文件名（去扩展名）== zotero_key →
  DOI（规范化后相等）→ 标题 rapidfuzz token_set_ratio >= 90（取最高分，
  同分取导入顺序靠前）；同批先到先得，一个键只被一个文件认领；
- 高亮三色 jade / amber / terra，与主题三色（玉青/琥珀/陶红）一一对应；
- ``save_stage2`` / ``restore_stage2`` / ``get_stage2_progress`` 等复筛决策 API
  与初筛（coscreen.db decisions）语义完全一致：decision 合法性校验、
  screened_at 首筛保留（restore 覆盖）、tags 缺省保留、排除理由在调用方
  （UI 层 prepare_decision）必填校验——本模块只做 decision 取值校验；
- 复筛 upsert SQL 复用 coscreen.db 的表名参数化拼装器（仅固定字面量）。

所有函数每次调用自行开闭连接（sqlite3，``with closing``，WAL）；输出
（队列、列表、报告）一律确定性排序。
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import json
import re
import shutil
import tempfile
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd
from pypdf import PdfReader
from rapidfuzz import fuzz

from coscreen import config
from coscreen.db import (
    StrPath,
    _add_column_if_missing,  # noqa: PLC2701  同包内复用连接工厂模块的补列原语
    _connect,  # noqa: PLC2701  同包内复用唯一连接工厂（WAL/外键/旧库自愈）
    list_articles,
    list_decisions,
    upsert_decision_sql,
)
from coscreen.security import safe_path

if TYPE_CHECKING:
    from coscreen.research_trace import TraceContext

__all__ = [
    "COLOR_LABELS",
    "COLOR_RGB",
    "EXTRACT_FAILED_TEXT",
    "HIGHLIGHT_COLORS",
    "MATCH_HOW_LABELS",
    "PDF_MAGIC",
    "FulltextMeta",
    "Highlight",
    "MatchReport",
    "PdfMatch",
    "add_highlight",
    "add_highlight_rects",
    "delete_highlight",
    "format_highlights_as_note",
    "get_highlights_with_rects",
    "get_fulltext",
    "get_stage2",
    "get_stage2_progress",
    "list_highlights",
    "list_stage2_decisions",
    "load_pdf_bytes",
    "match_pdfs",
    "parse_merged_includes",
    "pdf_page_count",
    "pdf_text",
    "restore_stage2",
    "save_pdf",
    "save_stage2",
    "stage2_queue",
]


# ---------------------------------------------------------------------------
# 常量与数据结构
# ---------------------------------------------------------------------------

#: 合法 PDF 的文件头（save_pdf 的硬校验）
PDF_MAGIC = b"%PDF-"

#: 高亮三色（与 ui/_theme 的 JADE / AMBER / TERRACOTTA 一致）
HIGHLIGHT_COLORS = ("jade", "amber", "terra")

#: 高亮颜色的中文标签（界面与 Markdown 导出使用）
COLOR_LABELS = {"jade": "玉青", "amber": "琥珀", "terra": "陶红"}

#: 高亮颜色的 RGB 分量（UI 以 25% 透明度做背景色）
# 品牌对齐（WriteLab 令牌）：玉青 #2F6F6A / 花芽黄 #C2A878 / 陶红 #B0563F
COLOR_RGB = {"jade": (47, 111, 106), "amber": (194, 168, 120), "terra": (176, 86, 63)}

#: 文本提取失败的友好占位文案（扫描件/损坏页等；绝不抛异常打断阅读）
EXTRACT_FAILED_TEXT = "（本页文本无法提取，请使用左侧 PDF 原文阅读。）"

#: match_pdfs 的匹配方式 -> 中文标签（报告展示用）
MATCH_HOW_LABELS = {
    "filename": "文件名 = zotero_key",
    "doi": "DOI",
    "title": "标题相似度",
}


@dataclass
class FulltextMeta:
    """fulltexts 表一行：某篇文献的 PDF 元数据。"""

    zotero_key: str
    filename: str
    path: str
    sha256: str
    n_pages: int
    added_at: str


@dataclass
class PdfMatch:
    """一条 PDF 匹配结果：path 按何种方式（how）命中 zotero_key。"""

    path: Path
    zotero_key: str
    how: str  # "filename" | "doi" | "title"


@dataclass
class MatchReport:
    """一批 PDF 的匹配报告：matched 与输入同序；unmatched 按输入顺序收集。"""

    matched: list[PdfMatch]
    unmatched: list[str]


@dataclass
class Highlight:
    """highlights 表一行：某篇文献某页的一条高亮标记（可带页笔记）。"""

    id: int
    zotero_key: str
    page: int
    text: str
    color: str
    note: str
    created_at: str


def _now() -> str:
    """ISO8601 本地时间戳（与 coscreen.db._now 同口径）。"""
    return datetime.now().isoformat(timespec="seconds")


_UNSAFE_FILENAME_CHARS = re.compile(r"[^0-9A-Za-z_\-.\u4e00-\u9fff\u3400-\u4dbf]+")


def _safe_pdf_stem(key: str) -> str:
    """把 zotero_key 清洗为安全文件名主干。"""
    cleaned = _UNSAFE_FILENAME_CHARS.sub("", (key or "").strip()).strip(".")
    return cleaned or "pdf"


#: 落盘主干的截断上限（防超长键撑爆文件系统名长限制；Windows 单名 255）
_PDF_DISK_STEM_MAX = 96

#: 落盘名区分段的十六进制位数（48 bit；配合 save_pdf 事务内的占用守卫，
#: 截段碰撞也能被检出而非静默覆盖）
_PDF_DISK_DIGEST_CHARS = 12


def _pdf_disk_filename(key: str) -> str:
    """PDF **落盘**文件名（单射，MM-07）。

    ``_safe_pdf_stem`` 是删除式清洗，天然非单射：'AB:123' / 'AB123' /
    'AB 123' 都成 'AB123'，'$$$' 与 'pdf' 都成 'pdf'——不同文献键的 PDF
    会落同名文件互相覆盖（Windows 大小写不敏感再放大一层）。这里对
    **原始键**取 sha256 短前缀拼在截断主干之后：同键幂等（重复保存仍覆盖
    同一文件）、异键互异（单射）；截断只作用于主干，不影响单射性。
    展示名（fulltexts.filename 列 / 下载 Content-Disposition）仍用
    ``_safe_pdf_stem``，外部契约不变。
    """
    stem = _safe_pdf_stem(key)[:_PDF_DISK_STEM_MAX]
    digest = hashlib.sha256((key or "").encode("utf-8")).hexdigest()[
        :_PDF_DISK_DIGEST_CHARS
    ]
    return f"{stem}_{digest}.pdf"


def _norm_text(text: str) -> str:
    """标题/文件名比较形式：小写 + 连续空白折叠（与去重比较口径一致）。"""
    return " ".join(text.lower().split())


def _fold_doi(text: str) -> str:
    """DOI 折叠比较形式：只保留字母与数字（小写）。

    文件名主干中 DOI 的 "/" 常被替换为 "_" 或 "-"，折叠后比较对
    "10.1234/ab-cd" 与 "10.1234_ab cd" 等写法等价。
    """
    return re.sub(r"[^0-9a-z]", "", text.lower())


# ---------------------------------------------------------------------------
# PDF 保存 / 读取
# ---------------------------------------------------------------------------

_INSERT_FULLTEXT = (
    "INSERT INTO fulltexts "
    "(zotero_key, filename, path, sha256, n_pages, added_at) "
    "VALUES (?, ?, ?, ?, ?, ?) "
    "ON CONFLICT(zotero_key) DO UPDATE SET "
    "filename=excluded.filename, path=excluded.path, sha256=excluded.sha256, "
    "n_pages=excluded.n_pages, added_at=excluded.added_at"
)

_FULLTEXT_COLUMNS = (
    "zotero_key", "filename", "path", "sha256", "n_pages", "added_at",
)


def _page_count_from_bytes(data: bytes) -> int:
    """用 pypdf 统计页数；文件头合法但内容损坏时按 0 处理（不中断保存）。"""
    try:
        return len(PdfReader(BytesIO(data)).pages)
    except Exception:  # noqa: BLE001  pypdf 对损坏文件抛多种异常，统一按 0 页
        return 0


def _page_count_from_path(path: Path) -> int:
    """pypdf 按**文件句柄**统计页数；损坏按 0 处理（与 bytes 入口同口径）。

    必须传 open() 句柄、不能传 str/Path：pypdf 6.x 的 ``PdfReader`` 对
    str/Path 入参会 ``open`` 后整个读进 ``BytesIO``（整文件入内存，见
    ``pypdf/_reader.py`` ``_initialize_stream``）。

    实测（r3，48MiB 成员）：正常 PDF（内嵌 48MiB 附件）/随机字节尾部 →
    峰值 0.02MiB 级；**对抗性构造**（EOF 后 48MiB 单行注释 ``%AAA...``）→
    峰值 96.77MiB ≈ 2× 成员——pypdf 句柄模式的词法层仍会把这类巨量
    注释/字面量 token 读入内存，见 save_pdf 文档的残差披露。
    """
    try:
        with open(path, "rb") as handle:
            return len(PdfReader(handle).pages)
    except Exception:  # noqa: BLE001  与 _page_count_from_bytes 同口径
        return 0


def save_pdf(
    db_path: StrPath,
    key: str,
    data: bytes | None,
    pdfs_dir: StrPath = "data/pdfs",
    *,
    trace_context: TraceContext | None = None,
    data_path: StrPath | None = None,
) -> FulltextMeta:
    """保存一篇文献的 PDF：校验文件头 -> 落盘 -> upsert 元数据。

    - ``data`` 必须以 ``%PDF-`` 开头，否则 ValueError（中文提示）；
    - **U5-P9**：pypdf 解析不出任何页面（0 页）同样 ValueError 拒绝——
      ``%PDF-`` 前 5 字节的 magic 校验只是形状检查、不是内容安全边界
      （polyglot 可伪造文件头），真正打开过一遍的解析结果是最低门槛；
    - 字节写入 ``{pdfs_dir}/{单射化的安全 key}.pdf``（目录不存在自动创建，
      MM-07：截断清洗主干 + sha256(key) 短后缀——不同键不互相覆盖；超长键
      截断后仍可保存），同一 key 重复保存为覆盖（幂等更新）；key 先过
      safe_path 越界校验（安全加固 §4：书目数据攻击者可控），目标路径已被
      **其他键**登记时 ValueError 拒绝而非静默覆盖；
    - 文件替换发生在 SQLite 事务提交**之前**（轨迹/非轨迹两条分支同口径，
      MM3-02）：替换失败 → 事务回滚，元数据与磁盘文件保持一致；
    - ``n_pages`` 用 pypdf 统计；
    - zotero_key 不在 articles 表时因外键约束抛 sqlite3.IntegrityError；
    - **D1（2.1.5 收尾清债）流式入口**：``data_path``（与 ``data`` 二选一）
      指向磁盘上的 PDF（task_package 整包导入恢复的有界解压产物）。magic
      只读头部、页数用**文件句柄**喂 pypdf（seek 式解析，见
      ``_page_count_from_path``）、落盘与 sha256 在同一条 _CHUNK 拷贝循环里
      **边拷边算（Tee）**——sha256 与落盘字节必然同源（无先算后拷的
      TOCTOU 窗口），且不在内存里整块持有成员字节（旧路径
      ``_read_member_bounded`` ≤100MB 整块 bytearray）。两个入口的校验闸
      （magic、≥1 页、单射名、占用拒绝）与产物逐字段一致。
    - **残差披露（r3 实测）**：正常 PDF 下整条 ``data_path`` 保存路径峰值
      2.03MiB（48MiB 成员）；**对抗性 PDF**（EOF 后 48MiB 单行注释）下
      pypdf 词法层仍会把巨量注释 token 读入内存，峰值 96.77MiB ≈ 2× 成员
      ——与旧整块路径（97.50MiB）同量级、无恶化；成员受导入侧
      100MB 上限约束，故该残差上限 ≈200MB 瞬态（契约已知边界已披露）。
    """
    if data is not None and data_path is not None:
        raise ValueError("data 与 data_path 只能二选一。")
    if data_path is None and not (data or b"").startswith(PDF_MAGIC):
        raise ValueError("不是有效的 PDF 文件（缺少 %PDF- 文件头）")
    if not (key or "").strip():
        raise ValueError("zotero_key 不能为空")
    if data_path is not None:
        source = Path(data_path)
        with open(source, "rb") as handle:
            if not handle.read(len(PDF_MAGIC)).startswith(PDF_MAGIC):
                raise ValueError("不是有效的 PDF 文件（缺少 %PDF- 文件头）")
        n_pages = _page_count_from_path(source)
        # sha256 不在此处单独算：与落盘在同一条拷贝循环里 Tee（见下），
        # 消除「先算后拷」两遍之间文件被并发改写导致元数据与字节不一致的
        # 窗口（D1 复审意见）。
    else:
        if data is None:
            raise ValueError("data 与 data_path 必须提供其一。")
        n_pages = _page_count_from_bytes(data)
        sha_hex = hashlib.sha256(data).hexdigest()
    if n_pages <= 0:
        raise ValueError(
            "PDF 内容无法解析（pypdf 未识别出任何页面），请确认文件未损坏后重试。"
        )

    directory = Path(pdfs_dir)
    directory.mkdir(parents=True, exist_ok=True)
    # MM-07：filename 是**展示名**（fulltexts.filename 列 / 下载头），保持
    # 清洗主干不变；落盘路径用单射名（截断主干 + sha256(key) 短后缀），
    # 不同键的 PDF 不再互相覆盖。
    filename = f"{_safe_pdf_stem(key)}.pdf"
    path = safe_path(directory, _pdf_disk_filename(key))
    # Same-directory staging keeps replacement atomic. Both traced and
    # untraced saves replace the file before SQLite commit (MM3-02) and
    # restore the previous file if commit/capture fails.
    staging = None
    now = _now()
    meta: FulltextMeta | None = None
    backup = None
    replaced = False
    had_file = path.exists()
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=path.name + ".", suffix=".tmp", dir=directory, delete=False,
        ) as temp_file:
            if data_path is not None:
                # D1：_CHUNK 拷贝（1MiB/块），不整块入内存；sha256 在同一
                # 循环里边拷边算（Tee）——与落盘字节必然同源，无 TOCTOU 窗口。
                digest = hashlib.sha256()
                with open(source, "rb") as handle:
                    while chunk := handle.read(1024 * 1024):
                        digest.update(chunk)
                        temp_file.write(chunk)
                sha_hex = digest.hexdigest()
            else:
                temp_file.write(data)
            staging = Path(temp_file.name)
        meta = FulltextMeta(
            zotero_key=key,
            filename=filename,
            path=str(path),
            sha256=sha_hex,
            n_pages=n_pages,
            added_at=now,
        )
        with closing(_connect(db_path)) as conn, conn:
            if trace_context is not None:
                from coscreen.research_trace import begin_capture_transaction, begin_write_request

                begin_capture_transaction(conn, trace_context, "fulltext")
                replay, prior_result = begin_write_request(
                    conn, trace_context, stage="fulltext", operation="fulltext_save",
                    subject_id=meta.zotero_key,
                    payload={"sha256": meta.sha256, "n_pages": meta.n_pages, "filename": meta.filename},
                )
                if replay:
                    staging.unlink(missing_ok=True)
                    return FulltextMeta(**prior_result)
            # MM-07 第二道防线：目标路径已被**其他键**登记时拒绝，绝不静默
            # 覆盖（等价态：哈希截段碰撞、历史脏行/手工改库；单射命名下正常
            # 流程不会触发，同键幂等更新不受影响）。
            occupant = conn.execute(
                "SELECT zotero_key FROM fulltexts WHERE path=? AND zotero_key<>?",
                (meta.path, meta.zotero_key),
            ).fetchone()
            if occupant is not None:
                raise ValueError(
                    f"目标文件 {Path(meta.path).name} 已被文献 "
                    f"{occupant[0]!r} 的全文占用，已拒绝覆盖；请核对文献键后重试。"
                )
            old = conn.execute(
                "SELECT sha256,n_pages FROM fulltexts WHERE zotero_key=?", (meta.zotero_key,),
            ).fetchone()
            conn.execute(
                _INSERT_FULLTEXT,
                (
                    meta.zotero_key,
                    meta.filename,
                    meta.path,
                    meta.sha256,
                    meta.n_pages,
                    meta.added_at,
                ),
            )
            if trace_context is not None:
                from coscreen.research_trace import capture_event

                capture_event(
                    conn, trace_context, stage="fulltext",
                    action="fulltext_added" if old is None else "fulltext_replaced",
                    subject_id=meta.zotero_key,
                    before={"sha256": old[0], "n_pages": int(old[1])} if old else None,
                    after={"sha256": meta.sha256, "n_pages": meta.n_pages},
                )
                from coscreen.research_trace import finish_write_request

                finish_write_request(conn, trace_context, meta.__dict__)
            # MM3-02：文件备份与替换统一在事务**提交前**完成——原非轨迹分支的
            # os.replace 位于提交之后，替换失败（Windows 目标文件被占用等）时
            # 元数据已记新 sha256/页数而磁盘仍旧文件，永久不一致。两条分支同
            # 口径：任一步失败 → 事务回滚（DB 与磁盘一致）；替换成功而提交
            # 失败时按 backup/首存语义恢复原文件。
            if path.exists():
                fd, backup_name = tempfile.mkstemp(
                    prefix=path.name + ".", suffix=".backup", dir=directory,
                )
                os.close(fd)
                backup = Path(backup_name)
                shutil.copy2(path, backup)
            os.replace(staging, path)
            replaced = True
        # v06·F4-49：备份清理放在**提交成功之后、恢复守卫之外**，且失败不致命。
        # 修前这一步在 try 内裸 unlink：Windows 上备份被占用/只读时抛
        # PermissionError，落进下方 except → 把刚提交的新文件用备份恢复成旧
        # 文件，DB（已提交新 sha256）与磁盘（旧字节）永久分叉。此刻 DB 与磁盘
        # 已一致，残留 .backup 文件无害（下次保存会覆盖同名备份并被清理），
        # 因此只静默忽略清理失败，绝不回滚已提交状态。
        if backup is not None:
            with contextlib.suppress(OSError):
                backup.unlink(missing_ok=True)
    except BaseException:
        restored = False
        if replaced:
            try:
                if backup is not None and backup.exists():
                    os.replace(backup, path)
                    restored = True
                elif not had_file:
                    path.unlink(missing_ok=True)
                    restored = True
            except OSError:
                # Preserve the original exception; the backup remains for recovery.
                pass
        for temp_path in (staging,):
            with contextlib.suppress(OSError):
                temp_path.unlink()
        # v06·F4-49 后半：恢复失败时备份是**唯一**可恢复副本，绝不能删。
        # 只有「尚未替换」或「已成功恢复」两种情形下备份才是多余副本。
        if backup is not None and (not replaced or restored):
            with contextlib.suppress(OSError):
                backup.unlink()
        raise
    return meta


def get_fulltext(db_path: StrPath, zotero_key: str) -> FulltextMeta | None:
    """取一篇文献的 PDF 元数据；从未上传过返回 None。"""
    with closing(_connect(db_path)) as conn:
        row = conn.execute(
            "SELECT zotero_key, filename, path, sha256, n_pages, added_at FROM fulltexts "
            "WHERE zotero_key = ?",
            (zotero_key,),
        ).fetchone()
    return FulltextMeta(*row) if row else None


def load_pdf_bytes(meta: FulltextMeta, max_bytes: int = 100 * 1024 * 1024) -> bytes | None:
    """按元数据读取 PDF 字节；文件被移动/删除时返回 None（由调用方提示）。

    v15·F4-51：先 ``stat`` 判长度再读——旧实现先把整个文件读进内存再比
    ``max_bytes``，防御形同虚设（60MB 文件 / 1MB 上限 = 峰值 60MB）。上限
    之外直接返回 None，不分配缓冲区；``read_bytes`` 后仍复查一次长度，兜住
    stat 与读之间的竞态（非常规文件系统/并发替换）。
    """
    try:
        path = Path(meta.path)
        if path.stat().st_size > max_bytes:
            return None
        raw = path.read_bytes()
    except OSError:
        return None
    if len(raw) > max_bytes:  # 防御异常大的文件拖垮 UI
        return None
    return raw


# ---------------------------------------------------------------------------
# PDF 匹配
# ---------------------------------------------------------------------------

def match_pdfs(
    db_path: StrPath,
    paths: list[StrPath],
    pdfs_dir: StrPath = "data/pdfs",
) -> MatchReport:
    """把一批 PDF 文件按确定性链路匹配到文献（不落盘，只报告）。

    匹配链路（对每个文件依次尝试）：
      1. filename：文件名主干（去扩展名）与某条 zotero_key 完全相等；
      2. doi：主干与某条文献的 DOI 按"仅字母数字折叠"后相等
         （文件名不能含 "/"，故 10.1234/abc 常写作 10.1234_abc）；
      3. title：主干与标题的 rapidfuzz token_set_ratio >= 阈值（90），
         取最高分，同分取导入顺序靠前（确定性）。

    只在非重复条目（进入筛选序列的文献）中匹配；同批内先到先得——
    一个键被前面的文件认领后，后面的文件即使也命中也记为 unmatched，
    避免同键被两个文件反复覆盖。``pdfs_dir`` 仅用于生成报告建议路径，
    本函数不写任何文件。
    """
    del pdfs_dir  # 报告口径不依赖落盘位置；保留参数以匹配调用方签名
    articles = list_articles(db_path)  # 非重复、按 import_order
    by_key = {a.zotero_key: a for a in articles}
    ordered_keys = [a.zotero_key for a in articles]
    norm_dois = {
        k: (by_key[k].doi or "").strip().lower() for k in ordered_keys
    }
    norm_titles = {k: _norm_text(by_key[k].title or "") for k in ordered_keys}

    matched: list[PdfMatch] = []
    unmatched: list[str] = []
    claimed: set[str] = set()

    for raw in paths:
        p = Path(raw)
        if not p.is_file():
            unmatched.append(str(p))
            continue
        stem = p.stem.strip()

        key: str | None = None
        how: str | None = None

        # 1) 文件名主干 == zotero_key（精确）
        if stem and stem in by_key:
            key, how = stem, "filename"

        # 2) 主干 == 规范化 DOI（文件名不能含 "/"，按字母数字折叠后比较：
        #    "10.1234_abc.pdf" 与 DOI "10.1234/abc" 视为相等）
        if key is None:
            stem_doi = _fold_doi(stem)
            if stem_doi:
                for k in ordered_keys:
                    if k in claimed or not norm_dois[k]:
                        continue
                    if _fold_doi(norm_dois[k]) == stem_doi:
                        key, how = k, "doi"
                        break

        # 3) 标题模糊匹配（token_set_ratio >= 90；最高分、同分先到）
        if key is None and stem:
            needle = _norm_text(stem)
            best_key: str | None = None
            best_score = 0
            for k in ordered_keys:
                if k in claimed or not norm_titles[k]:
                    continue
                score = fuzz.token_set_ratio(needle, norm_titles[k])
                if score >= config.FUZZY_TITLE_THRESHOLD and score > best_score:
                    best_key, best_score = k, score
            if best_key is not None:
                key, how = best_key, "title"

        if key is None or key in claimed:
            unmatched.append(str(p))
        else:
            claimed.add(key)
            matched.append(PdfMatch(path=p, zotero_key=key, how=how))

    return MatchReport(matched=matched, unmatched=unmatched)


# ---------------------------------------------------------------------------
# 页数与文本层（pypdf；提取失败给友好占位，绝不抛异常打断阅读）
# ---------------------------------------------------------------------------

def pdf_page_count(path: StrPath) -> int:
    """PDF 页数；文件缺失或损坏返回 0（调用方按 0 页显示占位）。"""
    try:
        with Path(path).open("rb") as fh:
            return len(PdfReader(fh).pages)
    except Exception:  # noqa: BLE001  pypdf/IO 的任何失败都按 0 页
        return 0


def pdf_text(path: StrPath, page: int | None = None) -> list[str]:
    """PDF 文本层：page=None 返回全部页文本；page=k（1 起始）返回单页列表。

    任何一页提取失败（扫描件无文本层、字符映射损坏等）都以
    ``EXTRACT_FAILED_TEXT`` 占位；``page`` 越界或文件打不开同样返回占位
    （单元素列表），保证 UI 永远有内容可渲染。
    """
    try:
        reader = PdfReader(str(Path(path)))
        n_pages = len(reader.pages)
    except Exception:  # noqa: BLE001
        return [EXTRACT_FAILED_TEXT]

    if page is None:
        indices = list(range(n_pages))
    else:
        if page < 1 or page > n_pages:
            return [EXTRACT_FAILED_TEXT]
        indices = [page - 1]

    out: list[str] = []
    for i in indices:
        try:
            text = (reader.pages[i].extract_text() or "").strip()
        except Exception:  # noqa: BLE001
            text = ""
        out.append(text if text else EXTRACT_FAILED_TEXT)
    return out


# ---------------------------------------------------------------------------
# 高亮标记
# ---------------------------------------------------------------------------

_INSERT_HIGHLIGHT = (
    "INSERT INTO highlights (zotero_key, page, text, color, note, created_at) "
    "VALUES (?, ?, ?, ?, ?, ?)"
)

_HIGHLIGHT_COLUMNS = (
    "id", "zotero_key", "page", "text", "color", "note", "created_at",
)


def add_highlight(
    db_path: StrPath,
    zotero_key: str,
    page: int,
    text: str,
    color: str = "jade",
    note: str = "",
) -> int:
    """新增一条高亮标记，返回其行 id。

    - color 必须是 HIGHLIGHT_COLORS 之一（jade/amber/terra），否则 ValueError；
    - text 去首尾空白后不得为空；page 必须 >= 1；
    - zotero_key 不在 articles 表时因外键约束抛 sqlite3.IntegrityError。
    """
    if color not in HIGHLIGHT_COLORS:
        raise ValueError(
            f"非法高亮颜色: {color!r}，必须为 {HIGHLIGHT_COLORS} 之一"
        )
    cleaned = (text or "").strip()
    if not cleaned:
        raise ValueError("高亮文本不能为空")
    page = int(page)
    if page < 1:
        raise ValueError("页码必须为正整数（1 起始）")
    note = (note or "").strip()

    with closing(_connect(db_path)) as conn, conn:
        cur = conn.execute(
            _INSERT_HIGHLIGHT,
            (zotero_key, page, cleaned, color, note, _now()),
        )
        return int(cur.lastrowid)


def list_highlights(db_path: StrPath, zotero_key: str | None = None) -> list[Highlight]:
    """列出高亮：指定文献时只列该文献；一律按 (page, id) 升序（确定性）。"""
    sql = "SELECT id, zotero_key, page, text, color, note, created_at FROM highlights"
    params: tuple = ()
    if zotero_key is not None:
        sql += " WHERE zotero_key = ?"
        params = (zotero_key,)
    sql += " ORDER BY page, id"
    with closing(_connect(db_path)) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [Highlight(*row) for row in rows]


def delete_highlight(db_path: StrPath, highlight_id: int) -> bool:
    """删除一条高亮；返回是否确实删除了行。"""
    with closing(_connect(db_path)) as conn, conn:
        cur = conn.execute("DELETE FROM highlights WHERE id = ?", (int(highlight_id),))
        return cur.rowcount > 0


def recolor_highlight(db_path: StrPath, highlight_id: int, color: str) -> bool:
    """改一条已入库高亮的颜色（组件 recolor 事件，PDF_UPGRADE_PLAN §3.3）。

    color 必须是 HIGHLIGHT_COLORS 之一（否则 ValueError）；id 不存在返回
    False；重复执行幂等（同色 UPDATE 仍返回 True，行状态不变）。
    """
    if color not in HIGHLIGHT_COLORS:
        raise ValueError(
            f"非法高亮颜色: {color!r}，必须为 {HIGHLIGHT_COLORS} 之一"
        )
    with closing(_connect(db_path)) as conn, conn:
        cur = conn.execute(
            "UPDATE highlights SET color = ? WHERE id = ?", (color, int(highlight_id))
        )
        return cur.rowcount > 0


def edit_highlight_note(db_path: StrPath, highlight_id: int, note: str) -> bool:
    """改一条已入库高亮的批注（组件 note_edit 事件，PDF_UPGRADE_PLAN §3.3）。

    note 去首尾空白（空串 = 清空批注）；id 不存在返回 False；重复执行幂等。
    """
    with closing(_connect(db_path)) as conn, conn:
        cur = conn.execute(
            "UPDATE highlights SET note = ? WHERE id = ?",
            ((note or "").strip(), int(highlight_id)),
        )
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# 页面级标注（SPEC §16，Zotero 式组件）：rects 坐标批量入库 / 读取 / 备注格式化
# ---------------------------------------------------------------------------

_INSERT_HIGHLIGHT_RECTS = (
    "INSERT INTO highlights "
    "(zotero_key, page, text, color, note, rects_json, created_at) "
    "VALUES (?, ?, ?, ?, ?, ?, ?)"
)

#: 组件事件类型（kind）合法值
EVENT_KINDS = ("highlight", "note")


def _ensure_rects_column(conn) -> None:
    """旧库自愈（SPEC §16）：highlights 表缺 rects_json 列时补齐（幂等、additive）。

    与 coscreen.db._ensure_tags_column 完全同策略：PRAGMA table_info 区分
    「新库尚未建表」（建表交给 _ensure_stage2_tables）与「旧库缺列」；仅在
    表已存在且缺列时执行 ALTER，检查与 ALTER 同处一个 BEGIN IMMEDIATE 写
    事务，并发迁移不产生重复加列。由 coscreen.db._connect 在每次连接时调用
    （延迟导入以打破 db -> fulltext 的循环依赖）。
    """
    _add_column_if_missing(
        conn, "highlights", "rects_json",
        "ALTER TABLE highlights ADD COLUMN rects_json TEXT DEFAULT ''",
    )


def add_highlight_rects(db_path: StrPath, zotero_key: str, events: list) -> int:
    """批量入库 PDF 页面级标注事件（SPEC §16），返回实际新增/更新条数。

    events 元素为组件回传的 dict：
    - ``{"kind": "highlight", "page": int, "text": str, "color": str,
      "rects": [[x0,y0,x1,y1],...], "note": str(可选)}`` → 新增高亮行，
      rects 以紧凑 JSON 存 ``rects_json``（scale=1 视口坐标，重绘用）；
    - ``{"kind": "note", "page": int, "text": 批注文本, ...}`` → 附着到该篇
      该页最近一条高亮的 note 字段（Zotero 语义：批注挂在标记上）；该页
      没有任何高亮时降级为新行（text=note=批注文本），保证批注不丢。

    幂等：kind=highlight 且 (page, text, color) 已存在时跳过——组件重发或
    重复「导入备注」不产生重复行，复筛备注不会被重复追加。
    校验失败（kind/颜色/页码/空文本/某个事件不是对象/rects 不是矩形列表）
    抛 ValueError（中文提示）；zotero_key 不在 articles 表时因外键约束抛
    sqlite3.IntegrityError。

    v15·F4-50：幂等判定与写入同处一个 BEGIN IMMEDIATE 写事务，并且**逐条**
    重新判定（此前的读快照在事务外，双线程同时提交同一 (page,text,color)
    会各插一行）。并发下后到者等待写锁、拿到锁后看到先到者已提交的行而跳过。
    v15·F4-52：事件元素/rects 形状不合法时按 docstring 契约抛 ValueError，
    不再漏出 ``AttributeError: 'str' object has no attribute 'get'`` /
    ``TypeError: 'int' object is not iterable``。
    """
    normalized: list[tuple[str, int, str, str, str, list]] = []
    for ev in events or []:
        if not isinstance(ev, dict):
            raise ValueError(f"标注事件必须是对象，收到 {type(ev).__name__}")
        kind = str(ev.get("kind") or "highlight").strip()
        if kind not in EVENT_KINDS:
            raise ValueError(f"非法标注事件类型: {kind!r}，必须为 {EVENT_KINDS} 之一")
        try:
            page = int(ev.get("page") or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError("页码必须为正整数（1 起始）") from exc
        if page < 1:
            raise ValueError("页码必须为正整数（1 起始）")
        color = str(ev.get("color") or "jade").strip()
        if color not in HIGHLIGHT_COLORS:
            raise ValueError(
                f"非法高亮颜色: {color!r}，必须为 {HIGHLIGHT_COLORS} 之一"
            )
        text = str(ev.get("text") or "").strip()
        note = str(ev.get("note") or "").strip()
        if kind == "highlight" and not text:
            raise ValueError("高亮文本不能为空")
        if kind == "note" and not text:
            continue  # 空批注无事发生
        rects = ev.get("rects") or []
        if not isinstance(rects, (list, tuple)):
            raise ValueError("rects 必须是坐标列表（每项为 [x0,y0,x1,y1]）")
        normalized.append((kind, page, text, color, note, list(rects)))

    count = 0
    with closing(_connect(db_path)) as conn, conn:
        # 幂等判定与写入同处写事务：跨进程由 BEGIN IMMEDIATE 串行化，进程内
        # 亦不会出现两个连接同时「查无此行 → 各插一行」。
        conn.execute("BEGIN IMMEDIATE")
        for kind, page, text, color, note, rects in normalized:
            if kind == "highlight":
                if conn.execute(
                    "SELECT 1 FROM highlights WHERE zotero_key = ? AND page = ? "
                    "AND text = ? AND color = ? LIMIT 1",
                    (zotero_key, page, text, color),
                ).fetchone():
                    continue  # 幂等：组件重发 / 重复导入 / 并发先到者已写入
                rects_json = json.dumps(
                    rects, ensure_ascii=False, separators=(",", ":")
                )
                conn.execute(
                    _INSERT_HIGHLIGHT_RECTS,
                    (zotero_key, page, text, color, note, rects_json, _now()),
                )
            else:  # note：附着到该页最近一条高亮（子查询按 id 降序取最新）
                prev = conn.execute(
                    "SELECT id, note FROM highlights WHERE zotero_key = ? AND page = ?"
                    " ORDER BY id DESC LIMIT 1",
                    (zotero_key, page),
                ).fetchone()
                if prev is None:  # 该页尚无高亮：降级为新行，批注不丢
                    conn.execute(
                        _INSERT_HIGHLIGHT_RECTS,
                        (zotero_key, page, text, color, text, "[]", _now()),
                    )
                elif (prev[1] or "") == text:
                    continue  # 幂等：同批注重复导入不重复计数/追加
                else:
                    conn.execute(
                        "UPDATE highlights SET note = ? WHERE id = ?", (text, prev[0])
                    )
            count += 1
    return count


def get_highlights_with_rects(db_path: StrPath, zotero_key: str) -> list[dict]:
    """列出某篇文献的高亮（含 rects 坐标），按 (page, id) 升序（确定性）。

    每行 dict：id / zotero_key / page / text / color / note / created_at /
    rects；rects 为 ``[[x0,y0,x1,y1],...]``（scale=1 视口坐标，供组件 overlay
    重绘）；旧行（rects_json 为空）或损坏 JSON 一律解析为 []，绝不抛异常。
    """
    sql = (
        "SELECT id, zotero_key, page, text, color, note, created_at, rects_json "
        "FROM highlights WHERE zotero_key = ? ORDER BY page, id"
    )
    with closing(_connect(db_path)) as conn:
        rows = conn.execute(sql, (zotero_key,)).fetchall()

    out: list[dict] = []
    for r in rows:
        d = dict(zip(_HIGHLIGHT_COLUMNS, r[:-1]))
        raw = r[-1]
        try:
            rects = json.loads(raw) if raw else []
        except (ValueError, TypeError):
            rects = []
        if not isinstance(rects, list):
            rects = []
        d["rects"] = [
            [float(v) for v in rect]
            for rect in rects
            if isinstance(rect, (list, tuple)) and len(rect) == 4
        ]
        out.append(d)
    return out


def format_highlights_as_note(
    rows: list[dict], color_labels: dict, note_label: str = "批注"
) -> str:
    """把高亮行格式化为可追加到复筛备注的文本（SPEC §16：[p3·玉青] 选段）。

    rows：:func:`get_highlights_with_rects` 输出（dict 含 page/text/color/note）；
    color_labels：``{color: 展示标签}`` 由 UI 层经 i18n 传入（本函数不依赖
    streamlit）；note 非空时行尾追加「（批注：…）」。一行一条、换行连接，
    输入为空返回空串；排序沿用输入（确定性）。
    """
    lines: list[str] = []
    for row in rows or []:
        color = row.get("color", "jade")
        label = color_labels.get(color, color) if color_labels else color
        line = f"[p{row.get('page', '?')}·{label}] {row.get('text', '')}"
        note = str(row.get("note") or "").strip()
        if note:
            line += f"（{note_label}：{note}）"
        lines.append(line)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 复筛决策（stage2_decisions；语义与初筛 decisions 完全一致，SPEC §14）
# ---------------------------------------------------------------------------

_STAGE2_TABLE = "stage2_decisions"

_STAGE2_LIST_COLUMNS = (
    "zotero_key", "decision", "exclusion_reason", "notes", "tags", "screened_at",
)

_STAGE2_FULL_COLUMNS = _STAGE2_LIST_COLUMNS + ("updated_at",)


def _validate_stage2_decision(decision: str) -> None:
    if decision not in config.DECISIONS:
        raise ValueError(
            f"非法 decision: {decision!r}，必须为 {config.DECISIONS} 之一"
        )


def save_stage2(
    db_path: StrPath,
    zotero_key: str,
    decision: str,
    exclusion_reason: str = "",
    notes: str = "",
    tags: str | None = None,
    *,
    trace_context: TraceContext | None = None,
) -> None:
    """保存/更新一条复筛决策（upsert）。

    与初筛 save_decision 同语义：decision 必须合法；screened_at 记录首次
    复筛时间（更新时保留）；tags 传 None（缺省）保留原值、传字符串（含
    空串）整体覆盖；排除理由必填由调用方（UI 的 prepare_decision）校验。
    zotero_key 不存在时因外键约束抛 sqlite3.IntegrityError。
    """
    _validate_stage2_decision(decision)
    now = _now()
    sql = upsert_decision_sql(
        _STAGE2_TABLE, keep_tags=tags is None, keep_screened_at=True
    )
    with closing(_connect(db_path)) as conn, conn:
        if trace_context is not None:
            from coscreen.workspace_undo import begin_save, snapshot, record_save
            begin_save(conn)
            from coscreen.research_trace import begin_capture_transaction, begin_write_request

            begin_capture_transaction(conn, trace_context, "fulltext")
            replay, _ = begin_write_request(
                conn, trace_context, stage="fulltext", operation="stage2_save",
                subject_id=zotero_key,
                payload={"decision": decision, "exclusion_reason": exclusion_reason,
                         "notes": notes, "tags": {"keep": True} if tags is None else tags},
            )
            if replay:
                return None
            undo_before = snapshot(conn, "fulltext", zotero_key)
        old = conn.execute(
            "SELECT decision,exclusion_reason,notes,tags FROM stage2_decisions WHERE zotero_key=?",
            (zotero_key,),
        ).fetchone()
        before = ({"decision": old[0], "exclusion_reason": old[1] or "", "notes": old[2] or "",
                   "tags": old[3] or ""} if old else None)
        after = {
            "decision": decision, "exclusion_reason": exclusion_reason or "", "notes": notes or "",
            "tags": (tags if tags is not None else (old[3] if old else "")) or "",
        }
        conn.execute(
            sql,
            (zotero_key, decision, exclusion_reason, notes, tags or "", now, now),
        )
        if trace_context is not None:
            record_save(conn, trace_context, "fulltext", zotero_key, undo_before,
                        snapshot(conn, "fulltext", zotero_key))
            from coscreen.research_trace import capture_event

            capture_event(
                conn, trace_context, stage="fulltext",
                action="decision_created" if old is None else "decision_changed",
                subject_id=zotero_key, before=before, after=after,
            )
            from coscreen.research_trace import finish_write_request

            finish_write_request(conn, trace_context)


def restore_stage2(
    db_path: StrPath,
    zotero_key: str,
    decision: str,
    exclusion_reason: str = "",
    notes: str = "",
    screened_at: str | None = None,
    tags: str | None = None,
    *,
    trace_context: TraceContext | None = None,
) -> None:
    """回导专用：按导出 CSV 中的原始时间戳无损还原一条复筛决策。

    与 save_stage2 的区别：screened_at 以传入值为准（缺省取当前时间）；
    updated_at 始终取当前时间。tags 语义与 save_stage2 一致。
    """
    _validate_stage2_decision(decision)
    stamp = screened_at if screened_at else _now()
    sql = upsert_decision_sql(
        _STAGE2_TABLE, keep_tags=tags is None, keep_screened_at=False
    )
    with closing(_connect(db_path)) as conn, conn:
        if trace_context is not None:
            from coscreen.research_trace import begin_capture_transaction, begin_write_request

            begin_capture_transaction(conn, trace_context, "fulltext")
            replay, _ = begin_write_request(
                conn, trace_context, stage="fulltext", operation="stage2_restore",
                subject_id=zotero_key,
                payload={"decision": decision, "exclusion_reason": exclusion_reason,
                         "notes": notes, "tags": {"keep": True} if tags is None else tags,
                         "screened_at": stamp},
            )
            if replay:
                return None
        old = conn.execute(
            "SELECT decision,exclusion_reason,notes,tags FROM stage2_decisions WHERE zotero_key=?",
            (zotero_key,),
        ).fetchone()
        before = ({"decision": old[0], "exclusion_reason": old[1] or "", "notes": old[2] or "",
                   "tags": old[3] or ""} if old else None)
        after = {
            "decision": decision, "exclusion_reason": exclusion_reason or "", "notes": notes or "",
            "tags": (tags if tags is not None else (old[3] if old else "")) or "",
        }
        conn.execute(
            sql,
            (zotero_key, decision, exclusion_reason, notes, tags or "", stamp, _now()),
        )
        if trace_context is not None:
            from coscreen.research_trace import capture_event

            capture_event(
                conn, trace_context, stage="fulltext",
                action="decision_imported" if trace_context.source == "import" else (
                    "decision_created" if old is None else "decision_changed"
                ), subject_id=zotero_key, before=before, after=after,
            )
            from coscreen.research_trace import finish_write_request

            finish_write_request(conn, trace_context)


def get_stage2(db_path: StrPath, zotero_key: str) -> dict | None:
    """取一条复筛决策（含 updated_at）；从未复筛过返回 None。"""
    with closing(_connect(db_path)) as conn:
        row = conn.execute(
            "SELECT zotero_key, decision, exclusion_reason, notes, tags, screened_at, "
            "updated_at FROM stage2_decisions WHERE zotero_key = ?",
            (zotero_key,),
        ).fetchone()
    return dict(zip(_STAGE2_FULL_COLUMNS, row)) if row else None


def list_stage2_decisions(db_path: StrPath) -> list[dict]:
    """列出全部复筛决策（按文献导入顺序；导出所需 6 字段，与初筛同构）。"""
    with closing(_connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT s.zotero_key, s.decision, s.exclusion_reason, s.notes, "
            "s.tags, s.screened_at "
            "FROM stage2_decisions s "
            "LEFT JOIN articles a ON a.zotero_key = s.zotero_key "
            "ORDER BY a.import_order, s.zotero_key"
        ).fetchall()
    return [dict(zip(_STAGE2_LIST_COLUMNS, r)) for r in rows]


#: 「可进入筛选序列的文献」谓词（别名固定为 a）——与 :func:`coscreen.db.list_articles`
#: 的缺省口径逐字同构：排除已确认重复（is_duplicate_of 非空）与**待人工复核**的
#: 标题候选组成员（imported_in_batch=1 且组状态 pending/undecided）。复筛进度与
#: 复筛队列必须共用这一个谓词（v06·F4-53：此前进度排除、队列不排除，同一库
#: 两条路径给出不同的文献集合）。
#: 2.1.5 waveA：该文本在下方查询里以**字面量内联**（Mimosa 钩子不允许在
#: execute 处拼装 SQL）；本常量保留唯一权威原文，修改谓词时须同步各内联副本。
_ELIGIBLE_ARTICLE_SQL = (
    "a.is_duplicate_of IS NULL AND NOT EXISTS ("
    "SELECT 1 FROM title_duplicate_members m "
    "JOIN title_duplicate_reviews r ON r.review_id=m.review_id "
    "WHERE m.zotero_key=a.zotero_key AND m.imported_in_batch=1 "
    "AND r.status IN ('pending','undecided'))"
)


def get_stage2_progress(db_path: StrPath) -> dict:
    """复筛进度：只统计已纳入初筛且非重复的文献。

    复筛队列是初筛纳入条目的子集（2.1.5 W1 起仅 ``include``；``maybe`` 不再
    入队），因此尚未开始初筛时 total 为 0；
    返回 {"total", "screened", "remaining", "include", "exclude", "maybe"}。
    进度与队列共用 :data:`_ELIGIBLE_ARTICLE_SQL`，两者统计到的文献集合一致。
    """
    counts = {"include": 0, "exclude": 0, "maybe": 0}
    with closing(_connect(db_path)) as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM articles a "
            "JOIN decisions d ON d.zotero_key=a.zotero_key AND d.decision='include' "
            "WHERE a.is_duplicate_of IS NULL AND NOT EXISTS ("
            "SELECT 1 FROM title_duplicate_members m "
            "JOIN title_duplicate_reviews r ON r.review_id=m.review_id "
            "WHERE m.zotero_key=a.zotero_key AND m.imported_in_batch=1 "
            "AND r.status IN ('pending','undecided'))"
        ).fetchone()[0]
        for decision, cnt in conn.execute(
            "SELECT s.decision, COUNT(*) FROM stage2_decisions s "
            "JOIN articles a ON a.zotero_key = s.zotero_key "
            "JOIN decisions d ON d.zotero_key=a.zotero_key AND d.decision='include' "
            "WHERE a.is_duplicate_of IS NULL AND NOT EXISTS ("
            "SELECT 1 FROM title_duplicate_members m "
            "JOIN title_duplicate_reviews r ON r.review_id=m.review_id "
            "WHERE m.zotero_key=a.zotero_key AND m.imported_in_batch=1 "
            "AND r.status IN ('pending','undecided')) "
            "GROUP BY s.decision"
        ):
            if decision in counts:
                counts[decision] = cnt
    screened = sum(counts.values())
    return {
        "total": total,
        "screened": screened,
        "remaining": total - screened,
        "include": counts["include"],
        "exclude": counts["exclude"],
        "maybe": counts["maybe"],
    }


# ---------------------------------------------------------------------------
# 复筛队列
# ---------------------------------------------------------------------------

def stage2_queue(
    db_path: StrPath,
    mode: str = "own",
    arbitrated_keys: list[str] | None = None,
) -> list[str]:
    """构造复筛队列（zotero_key 列表，按文献导入顺序、确定性）。

    - mode="own"（默认）：本人初筛 decisions 中 Include 的键
      （2.1.5 W1：maybe 不再入复筛；双盲：只用本人数据库），且只保留**可进入
      筛选序列**的文献——已确认重复（is_duplicate_of 非空）与待人工复核的标题
      候选组成员不进复筛队列，与 :func:`get_stage2_progress` 共用
      :data:`_ELIGIBLE_ARTICLE_SQL`
      （v06·F4-53：两处口径分裂时 pending 期进度 total 2 vs 队列 3、判 duplicate
      后队列仍含重复键而进度已排除）；
    - mode="arbitrated":外部传入的双方一致纳入键列表（UI 上传合并明细
      CSV 后经 :func:`parse_merged_includes` 提取），只保留存在于本人库
      （非重复条目）中的键并按导入顺序去重排序；
    - 其他 mode 值抛 ValueError；mode="arbitrated" 未提供 arbitrated_keys
      时抛 ValueError。
    """
    if mode == "own":
        # list_decisions 已按 (import_order, zotero_key) 排序；仅补一道
        # 「可进入筛选序列」过滤，保持原有顺序与双盲口径不变。
        decisions = list_decisions(db_path)
        if not any(d["decision"] == "include" for d in decisions):
            return []
        with closing(_connect(db_path)) as conn:
            eligible = {
                row[0] for row in conn.execute(
                    "SELECT a.zotero_key FROM articles a WHERE "
                    "a.is_duplicate_of IS NULL AND NOT EXISTS ("
                    "SELECT 1 FROM title_duplicate_members m "
                    "JOIN title_duplicate_reviews r ON r.review_id=m.review_id "
                    "WHERE m.zotero_key=a.zotero_key AND m.imported_in_batch=1 "
                    "AND r.status IN ('pending','undecided'))"
                )
            }
        return [
            d["zotero_key"]
            for d in decisions
            if d["decision"] == "include" and d["zotero_key"] in eligible
        ]
    if mode == "arbitrated":
        if arbitrated_keys is None:
            raise ValueError(
                "mode='arbitrated' 需要提供 arbitrated_keys（合并明细中双方一致纳入的键）"
            )
        known = [a.zotero_key for a in list_articles(db_path)]
        keep = set(arbitrated_keys)
        return [k for k in known if k in keep]
    raise ValueError(
        f"未知队列模式: {mode!r}，必须为 'own' 或 'arbitrated'"
    )


def parse_merged_includes(data: bytes) -> list[str]:
    """从上传的合并明细 CSV（merge 输出的 merged_detail.csv）提取复筛队列键。

    取 ``status == "agree"`` 且 ``decision_A == "include"`` 的行的
    zotero_key（agree 行双方决策必然相同）；去重并排序（确定性）。
    缺少必需列时抛 ValueError（中文提示）。
    """
    df = pd.read_csv(BytesIO(data), dtype=str, keep_default_na=False,
                     encoding="utf-8-sig")
    required = ("zotero_key", "status", "decision_A")
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(
            f"合并明细 CSV 缺少必需列: {missing}，实际列为 {list(df.columns)}"
        )
    mask = (
        df["status"].str.strip().str.lower().eq("agree")
        & df["decision_A"].str.strip().str.lower().eq("include")
    )
    return sorted({k for k in df.loc[mask, "zotero_key"] if k})


# ---------------------------------------------------------------------------
# 临时文件辅助（UI 上传字节 -> 保留原文件名的临时路径，供 match_pdfs 使用）
# ---------------------------------------------------------------------------

def save_upload_to_tempdir(data: bytes, filename: str) -> Path:
    """把上传字节写入临时目录并保留原文件名（匹配依赖文件名主干）。

    返回的路径由调用方负责删除（或清理其临时目录）。
    """
    safe_name = Path(filename or "upload.pdf").name or "upload.pdf"
    # review67 L-11：全点号名（如 "..."；"."/".." 的 name 为空、已被上一行
    # 兜住）在 Windows 上 CreateFile 直接拒绝，write_bytes 抛未处理
    # PermissionError——本函数当前全仓无调用方（match_pdfs 已改为直接收
    # 路径），属潜伏缺陷防御：纯点串一律回落到默认名，不依赖平台行为。
    if not safe_name.strip("."):
        safe_name = "upload.pdf"
    tmpdir = tempfile.mkdtemp(prefix="cobookshelf_pdf_")
    path = Path(tmpdir) / safe_name
    path.write_bytes(data)
    return path
