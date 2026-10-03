"""PubMed 自动监控可选插件（衍生产品规格 §5.3）——全仓唯一允许联网的模块。

- **默认关闭**：本模块不加入 custom_backend.main 的默认导入；只有用户显式开启
  「自动监控」并手动点击「立即检查」时，check-now 端点才延迟导入并调用它
  （产品哲学 §0.1：网络功能仅作为可选插件，默认关闭，显式开启）。
- **可捕获失败**：任何网络/解析故障统一抛 :class:`PubMedPluginError`，调用方
  捕获后降级到手动模式（「网络不可达，请手动导出文件后导入」），功能不中断。
- 使用 PubMed E-utilities 免费 API（无 key 可用）：esearch 取 PMID 列表，
  efetch 取 MEDLINE 格式详情。遵守 NCBI 公共访问礼仪：带工具标识的
  User-Agent、无并发轰炸（一次调用两个串行请求）。

MEDLINE 解析（:func:`parse_medline_records`）是纯函数，无网络依赖，供测试
直接调用。
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

from coscreen.normalize import normalize_authors, normalize_doi, normalize_title

__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "ESEARCH_URL",
    "EFETCH_URL",
    "PubMedPluginError",
    "fetch_pubmed_updates",
    "parse_medline_records",
]

ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"

#: 单请求超时（秒）：不可达/挂起时尽快失败，调用方降级
DEFAULT_TIMEOUT_SECONDS = 15.0

#: esearch retmax（规格 §5.3：一次增量拉取至多 10000 条 PMID）
RETMAX = 10000

_USER_AGENT = "ReviewFlow-LivingReview/1.0 (offline-first; contact: local-user)"

# MEDLINE 行：4 字符标签 + "- " + 值（如 "TI  - Title."）；续行以 6 个空格开头
_MEDLINE_TAG_RE = re.compile(r"^([A-Z][A-Z0-9 ]{3})- ?(.*)$")
_YEAR_RE = re.compile(r"(\d{4})")


class PubMedPluginError(RuntimeError):
    """PubMed 插件失败（网络不可达 / HTTP 错误 / 响应不可解析）。

    调用方必须捕获本异常并降级到手动导入模式，绝不让主应用 500。
    """


def _open(url: str, timeout: float) -> bytes:
    """单次 GET；任何网络层故障统一包装为 PubMedPluginError。"""
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except PubMedPluginError:
        raise
    except urllib.error.HTTPError as exc:
        raise PubMedPluginError(f"PubMed 返回 HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise PubMedPluginError(f"PubMed 不可达：{exc}") from exc


def _wrap_opener(open_: Callable[[str, float], bytes],
                 timeout: float) -> Callable[[str], bytes]:
    """把任何 opener（含测试注入的假 opener）的故障统一成 PubMedPluginError。

    保证 :func:`fetch_pubmed_updates` 的契约不依赖 opener 来源：
    超时/不可达/HTTP 错误一律是可捕获的 :class:`PubMedPluginError`。
    """

    def _fetch(url: str) -> bytes:
        try:
            return open_(url, timeout)
        except PubMedPluginError:
            raise
        except urllib.error.HTTPError as exc:
            raise PubMedPluginError(f"PubMed 返回 HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise PubMedPluginError(f"PubMed 不可达：{exc}") from exc

    return _fetch


def _normalize_since(since_date: str) -> str:
    """YYYY-MM-DD -> YYYY/MM/DD（E-utilities mindate 的日期分隔符）。"""
    return (since_date or "").strip().replace("-", "/")


def fetch_pubmed_updates(
    query: str,
    since_date: str,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    url_opener: Callable[[str, float], bytes] | None = None,
) -> list[dict]:
    """通过 PubMed E-utilities 拉取 ``since_date`` 之后的增量文献（免费 API，无 key）。

    流程（规格 §5.3）：

    1. ``esearch.fcgi``：``db=pubmed, term=<query>, mindate=<since_date>,
       datetype=edat（录入日期——不带 datetype 时 NCBI 会忽略日期过滤），
       retmax=10000, retmode=json`` → PMID 列表；
    2. ``efetch.fcgi``：``db=pubmed, id=<PMID,...>, rettype=medline,
       retmode=text`` → MEDLINE 文本；
    3. :func:`parse_medline_records` 解析为 Article 字段形状的 dict 列表。

    返回 dict 的键与 :class:`coscreen.models.Article` 字段一致
    （zotero_key/title/authors/journal/year/doi/abstract/url/source_format），
    另附 ``pmid``；``zotero_key`` 取 ``PMID-<pmid>``（稳定、全局唯一）。

    失败抛 :class:`PubMedPluginError`（超时/不可达/响应不可解析），调用方降级。
    ``url_opener`` 仅供测试注入假响应（生产调用方不传）。
    """
    term = (query or "").strip()
    if not term:
        raise ValueError("检索式不能为空")
    open_ = _wrap_opener(url_opener or _open, timeout)

    params = urllib.parse.urlencode({
        "db": "pubmed",
        "term": term,
        "mindate": _normalize_since(since_date),
        "datetype": "edat",
        "retmax": RETMAX,
        "retmode": "json",
        "tool": "ReviewFlow",
    })
    raw = open_(f"{ESEARCH_URL}?{params}")
    try:
        payload = json.loads(raw.decode("utf-8"))
        id_list = payload["esearchresult"]["idlist"]
    except (UnicodeDecodeError, ValueError, KeyError, TypeError) as exc:
        raise PubMedPluginError(f"esearch 响应不可解析：{exc}") from exc
    if not isinstance(id_list, list):
        raise PubMedPluginError("esearch 响应中的 idlist 不是列表")
    pmids = [str(pmid) for pmid in id_list if str(pmid).strip()]
    if not pmids:
        return []

    params = urllib.parse.urlencode({
        "db": "pubmed",
        "id": ",".join(pmids),
        "rettype": "medline",
        "retmode": "text",
        "tool": "ReviewFlow",
    })
    raw = open_(f"{EFETCH_URL}?{params}")
    try:
        text = raw.decode("utf-8", errors="replace")
    except (UnicodeDecodeError, ValueError) as exc:  # pragma: no cover - bytes.decode(replace) 不抛
        raise PubMedPluginError(f"efetch 响应不可解码：{exc}") from exc
    articles = parse_medline_records(text)
    if not articles:
        raise PubMedPluginError("efetch 返回的 MEDLINE 文本中没有任何记录")
    return articles


# ---------------------------------------------------------------------------
# MEDLINE 解析（纯函数，无网络）
# ---------------------------------------------------------------------------

def _field(record: dict[str, str], tag: str) -> str:
    return record.get(tag, "").strip()


def parse_medline_records(text: str) -> list[dict]:
    """解析 MEDLINE（rettype=medline, retmode=text）为 Article 字段形状的列表。

    记录以空行分隔；行形如 ``TI  - value``（标签域定宽 4 字符，短标签右侧
    补空格），续行以 6 空格缩进并入当前字段。抽取 PMID/TI/AU/TA/DP/AB/LID/AID；
    DOI 取以 ``[doi]`` 结尾的 LID/AID；作者以 ``; `` 连接（与 coscreen.parsers
    的 Article.authors 口径一致）。无 PMID 的记录跳过（无法构造稳定 zotero_key）。
    """
    records: list[dict[str, str]] = []
    current: dict[str, str] = {}
    last_tag: str | None = None
    for line in text.splitlines():
        if not line.strip():
            if current:
                records.append(current)
            current, last_tag = {}, None
            continue
        match = _MEDLINE_TAG_RE.match(line)
        if match:
            last_tag = match.group(1).rstrip()  # "TI  " -> "TI"（定宽标签去补位空格）
            value = match.group(2)
            # 同一标签重复出现（多作者 AU / 多 AID）以 "; " 连接——与下方
            # split(";") 拆分口径互逆；唯一字段（TI/AB 等）不受影响。
            current[last_tag] = (
                f"{current[last_tag]}; {value}".strip()
                if last_tag in current else value
            )
        elif last_tag and line.startswith("      "):
            current[last_tag] = f"{current[last_tag]} {line.strip()}".strip()
    if current:
        records.append(current)

    articles: list[dict[str, Any]] = []
    for record in records:
        pmid = _field(record, "PMID") or _field(record, "PM")
        if not pmid:
            continue
        title = normalize_title(_field(record, "TI"))
        doi = ""
        for tag in ("LID", "AID"):
            for candidate in record.get(tag, "").split(";"):
                candidate = candidate.strip()
                if candidate.lower().endswith("[doi]"):
                    doi = normalize_doi(candidate[: -len("[doi]")].strip())
                    break
            if doi:
                break
        year_match = _YEAR_RE.search(_field(record, "DP"))
        authors_raw = [
            value.strip() for value in record.get("AU", "").split(";") if value.strip()
        ]
        articles.append({
            "zotero_key": f"PMID-{pmid}",
            "title": title,
            "authors": normalize_authors(authors_raw),
            "journal": _field(record, "TA"),
            "year": int(year_match.group(1)) if year_match else None,
            "doi": doi,
            "abstract": _field(record, "AB"),
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            "source_format": "pubmed",
            "pmid": pmid,
        })
    return articles
