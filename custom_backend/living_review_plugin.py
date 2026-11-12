"""PubMed 自动监控可选插件（衍生产品规格 §5.3）——全仓唯一允许联网的模块。

- **默认关闭；W3 已复活（接线状态以实现为准，MM10-14）**：本模块不加入
  custom_backend.main 的默认导入；只有用户手动点击「立即检查」（或显式开启
  自动监控后由前端页面会话内定时器触发 check-now）时，check-now 端点才延迟
  导入并调用它（产品哲学 §0.1：网络功能仅作为可选插件，默认关闭，显式开启；
  后端自身绝不起后台线程/定时任务，应用关闭即停）。
  与遗留上传路由的分工：`POST /living-review/import-update` 仍只鉴权后固定返回
  409 ``protected_import_required``（引导用户把检索结果文件走受保护的导入复核）；
  check-now 拉取的文献同样**不直接入库**——合成虚拟 RIS 文件后走
  ``review_imports.upload()`` 同一条受保护导入管线。
- **可捕获失败（MM10-14 契约）**：入参类型不符、url_opener 返回非 bytes、
  opener 抛任意异常、网络不可达/超时/HTTP 错误/重定向被拒、响应不可解析——
  一律统一抛 :class:`PubMedPluginError`；调用方捕获后降级到手动模式
  （「网络不可达，请手动导出文件后导入」），功能不中断、绝不 500。
  （降级覆盖检索阶段；进入受保护导入管线后的失败与其他导入端点同语义。）
- 使用 PubMed E-utilities 免费 API（无 key 可用）：esearch 取 PMID 列表，
  efetch 取 MEDLINE 格式详情。遵守 NCBI 公共访问礼仪：带工具标识的
  User-Agent、无并发轰炸（一次调用两个串行请求）。出站纪律沿袭
  custom_backend/ai_suggest_api.py 的 _NoRedirectHandler 先例：拒绝跟随
  3xx 重定向；单响应读取上限 MAX_RESPONSE_BYTES（防无界内存读取）。

MEDLINE 解析（:func:`parse_medline_records`）是纯函数，无网络依赖，供测试
直接调用。
"""

from __future__ import annotations

import http.client
import inspect
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable

from coscreen.normalize import normalize_authors, normalize_doi, normalize_title

__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "ESEARCH_URL",
    "EFETCH_URL",
    "MAX_RESPONSE_BYTES",
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

#: efetch 每批 PMID 数。r8-r3 评审真网实测：单条 MEDLINE 约 4.1-4.2 KB
#: （100 条=0.39 MiB、300=1.20 MiB、600=2.38 MiB），10000 条约 39.7 MiB，
#: 会直接撞上读取上限。按 200 条/批拉取（≈0.8 MiB/批）既避开上限，也让
#: 进度与失败粒度可控——任一批失败整次降级，不写入半截结果。
EFETCH_PAGE_SIZE = 200

#: 单响应读取上限。真网实测约 4.1 KB/条，故 32 MiB 约合 8000 条；由于
#: efetch 已按 EFETCH_PAGE_SIZE 分页（每批 ≈0.8 MiB），本上限现在只作为
#: 防御性护栏（异常大单条记录、被劫持的响应体），不再是正常路径的约束。
MAX_RESPONSE_BYTES = 32 * 1024 * 1024

#: esearch 分页读取 PMID 的页大小（与 efetch 批次解耦：先取全量 PMID 列表）
ESEARCH_PAGE_SIZE = 5000

_USER_AGENT = "ReviewFlow-LivingReview/1.0 (offline-first; contact: local-user)"

# MEDLINE 行：4 字符标签 + "- " + 值（如 "TI  - Title."）；续行以 6 个空格开头
_MEDLINE_TAG_RE = re.compile(r"^([A-Z][A-Z0-9 ]{3})- ?(.*)$")
_YEAR_RE = re.compile(r"(\d{4})")


class PubMedPluginError(RuntimeError):
    """PubMed 插件失败（入参类型不符 / 网络不可达 / HTTP 错误 / 响应不可解析）。

    本模块全部对外入口的失败统一抛本异常（MM10-14 契约归一）。调用方必须
    捕获本异常并降级到手动导入模式，绝不让主应用 500。
    """


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """拒绝跟随 3xx 重定向（先例：custom_backend/ai_suggest_api.py:301）。

    E-utilities 两个端点是声明的固定 https 主机；重定向只会把请求引向
    未声明的主机，一律视为错误（30x 由 urllib 转成 HTTPError 后归一）。
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


_OPENER = urllib.request.build_opener(
    # review67 L-51：显式空 ProxyHandler——urllib 默认读环境 http_proxy/
    # https_proxy，在场时 PubMed 出站（含检索式全文）会被第三方代理中转；
    # 与 pdf_fetch_api.py 的空代理纪律对齐（其注释明言否则「隐私披露变成
    # 假话」）。重定向拒绝/固定端点/读取上限不受影响。
    urllib.request.ProxyHandler({}), _NoRedirectHandler,
)


def _open(url: str, timeout: float, *,
          data: bytes | None = None) -> bytes:
    """单次请求（GET 或 POST 表单）：显式 UA + 拒绝重定向 + 读取上限。

    r8 评审 low：``http.client.IncompleteRead`` 等 ``HTTPException`` 不是
    ``OSError`` 子类，此前会从 ``_open`` 硬逃逸；现一并归入与网络故障同一
    出口，使「本模块全部对外入口的失败统一抛 PubMedPluginError」对私有
    ``_open`` 也成立。``data`` 非空时按 POST（application/x-www-form-urlencoded）
    发送——efetch 一次可能携带上万个 PMID，长 GET URL 会被 NCBI 拒绝或截断。
    """
    headers = {"User-Agent": _USER_AGENT}
    if data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    request = urllib.request.Request(url, data=data, headers=headers)
    try:
        with _OPENER.open(request, timeout=timeout) as response:  # noqa: S310  固定 eutils 端点
            chunks, total = [], 0
            while True:
                chunk = response.read(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_RESPONSE_BYTES:
                    raise PubMedPluginError(
                        f"PubMed 响应超过 "
                        f"{MAX_RESPONSE_BYTES // (1024 * 1024)} MiB 读取上限。")
                chunks.append(chunk)
            data_out = b"".join(chunks)
    except PubMedPluginError:
        raise
    except urllib.error.HTTPError as exc:
        if 300 <= exc.code < 400:
            raise PubMedPluginError(
                f"PubMed HTTP {exc.code}：重定向被拒绝（仅访问声明的 "
                "eutils.ncbi.nlm.nih.gov 端点）。") from exc
        raise PubMedPluginError(f"PubMed 返回 HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError,
            http.client.HTTPException) as exc:
        raise PubMedPluginError(f"PubMed 不可达：{exc}") from exc
    return data_out


def _supports_post(open_: Callable[..., bytes]) -> bool:
    """opener 是否接受 ``data=`` 关键字（生产 ``_open`` 接受；测试桩常只需
    两参数，此时 efetch 回退 GET——注入契约保持 ``(url, timeout)`` 不变）。"""
    try:
        parameters = inspect.signature(open_).parameters.values()
    except (TypeError, ValueError):  # 内建/无签名可读：按支持对待，调用期兜错
        return True
    return any(p.name == "data" or p.kind is inspect.Parameter.VAR_KEYWORD
               for p in parameters)


def _wrap_opener(open_: Callable[..., bytes],
                 timeout: float) -> Callable[[str, bytes | None], bytes]:
    """把任何 opener（含测试注入的假 opener）的故障统一成 PubMedPluginError。

    保证 :func:`fetch_pubmed_updates` 的契约不依赖 opener 来源（MM10-14）：
    opener 抛任意异常（urllib 错误、超时、注入桩的 TypeError 等）或返回非
    bytes，一律是可捕获的 :class:`PubMedPluginError`，绝不硬逃逸。
    第二参数为 POST 表单体（None 表示 GET）；不接受 ``data=`` 的测试桩
    自动回退 GET，不重复调用。
    """
    post_capable = _supports_post(open_)

    def _fetch(url: str, data: bytes | None = None) -> bytes:
        try:
            if data is not None and post_capable:
                payload = open_(url, timeout, data=data)
            else:
                payload = open_(url, timeout)
        except PubMedPluginError:
            raise
        except Exception as exc:  # noqa: BLE001  网络包装层：任意 opener 故障归一
            raise PubMedPluginError(f"PubMed 不可达：{exc}") from exc
        if not isinstance(payload, (bytes, bytearray)):
            raise PubMedPluginError(
                f"url_opener 返回 {type(payload).__name__}，预期 bytes。")
        return bytes(payload)

    return _fetch


def _normalize_since(since_date: str) -> str:
    """YYYY-MM-DD -> YYYY/MM/DD（E-utilities mindate 的日期分隔符）。"""
    return (since_date or "").strip().replace("-", "/")


class PubMedUpdates(list):
    """``fetch_pubmed_updates`` 的返回载体：list 兼容 + 携带 NCBI 命中数。

    review67 L-50：调用方此前只能用 ``len(articles) >= RETMAX`` 反推截断，
    产出缺口（esearch 空白项、medline 无 PMID 记录被跳过）会让截断窗口被
    误判为完整增量入库。esearch 的 ``count`` 经 ``total_hits`` 随载体暴露，
    调方可直接对权威命中数判截断；``total >= RETMAX`` 的必然截断本插件已
    在 esearch 阶段短路抛错（见 fetch_pubmed_updates），不依赖调用方兜底。
    """

    __slots__ = ("total_hits",)

    def __init__(self, items=(), *, total_hits: int = 0):
        super().__init__(items)
        self.total_hits = int(total_hits)


def fetch_pubmed_updates(
    query: str,
    since_date: str,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    url_opener: Callable[..., bytes] | None = None,
) -> list[dict]:
    """通过 PubMed E-utilities 拉取 ``since_date`` 之后的增量文献（免费 API，无 key）。

    流程（规格 §5.3）：

    1. ``esearch.fcgi``：``db=pubmed, term=<query>, mindate=<since_date>,
       maxdate=<今天>, datetype=edat, retmax=5000, retmode=json`` →
       PMID 列表（空列表直接返回 ``[]``，不再请求 efetch）。**mindate 与
       maxdate 必须成对**：真网实测只给 mindate 时 NCBI 会整段丢弃日期过滤
       （'network meta-analysis[tiab]' 无过滤 16849、只给 mindate 仍 16849、
       成对给 24），因此本函数总是同时发送两端。超过 ESEARCH_PAGE_SIZE 时
       用 retstart 分页，总计不超过 RETMAX；分页跨页对 PMID 去重
       （review67 L-49：edat 窗口实时增长使 retstart 重发整式查询时结果集
       漂移，重复 PMID 原样透传会灌水待审计数）。NCBI ``count`` 经返回载体
       :class:`PubMedUpdates` 的 ``total_hits`` 暴露；``total >= RETMAX`` 的
       必然截断在 esearch 阶段即抛 :class:`PubMedPluginError` 短路
       （review67 L-50：不再空跑 50 批 efetch，也不依赖 len() 反推截断）；
    2. ``efetch.fcgi``：**POST** 表单 ``db=pubmed, id=<PMID,...>,
       rettype=medline, retmode=text``，按 ``EFETCH_PAGE_SIZE``（200 条）
       分批。真网实测单条约 4.1-4.2 KB，10000 条 ≈39.7 MiB 会撞读取上限；
       分批后每批约 0.8 MiB。任一批失败则整次抛错，绝不返回半截结果；
    3. :func:`parse_medline_records` 解析为 Article 字段形状的 dict 列表。

    真实网络验证范围（如实披露）：esearch 日期配对、efetch 单批体量、POST
    形态、UA 与重定向拒绝均已对 eutils.ncbi.nlm.nih.gov 实测；**未**验证的
    是真实 MEDLINE 语料对解析器全部字段形态的覆盖（解析器仅以合成夹具与
    仓库既有夹具验证），以及 NCBI 频控边界（本模块一次调用最多
    ceil(N/200)+ceil(N/5000) 个串行请求，无并发）。

    返回 dict 的键与 :class:`coscreen.models.Article` 字段一致
    （zotero_key/title/authors/journal/year/doi/abstract/url/source_format），
    另附 ``pmid``；``zotero_key`` 取 ``PMID-<pmid>``（稳定、全局唯一）。

    失败契约（MM10-14 归一）：``query`` 非 str 或为空、``since_date`` 非
    非空 str、opener 返回非 bytes、opener 抛任意异常、超时/不可达/HTTP
    错误（含 3xx 重定向被拒与 http.client 协议异常）/响应不可解析——
    **一律抛** :class:`PubMedPluginError`，调用方只需捕获这一种异常并降级。
    ``url_opener`` 仅供测试注入假响应（生产调用方不传）；签名 ``(url, timeout)``
    即可，需要覆盖 POST 时接受 ``data=`` 关键字。
    """
    if not isinstance(query, str):
        raise PubMedPluginError(f"检索式必须为字符串，得到 {type(query).__name__}。")
    if not query.strip():
        raise PubMedPluginError("检索式不能为空。")
    # r8 评审 low：None/空日期会构造出 mindate= 的空值请求（NCBI 行为未定义），
    # 直接按契约拒绝，而不是发一个语义不明的请求。
    if not isinstance(since_date, str):
        raise PubMedPluginError(
            f"since_date 必须为 YYYY-MM-DD 字符串，得到 {type(since_date).__name__}。")
    if not since_date.strip():
        raise PubMedPluginError("since_date 不能为空。")
    term = query.strip()
    open_ = _wrap_opener(url_opener or _open, timeout)

    # 日期过滤必须 mindate + maxdate 成对（r8-r3 评审 blocking，真网实测）：
    # 只给 mindate 时 NCBI 会丢弃整段日期过滤——'network meta-analysis[tiab]'
    # 无过滤 16849，只给 mindate=2026/10/29 仍是 16849（完全相同），成对给
    # mindate+maxdate 则降到 24。maxdate 取今天（ureg 口径的录入日期上界）。
    since = _normalize_since(since_date)
    until = _normalize_since(datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    pmids: list[str] = []
    seen: set[str] = set()
    total = 0
    retstart = 0
    while True:
        params = urllib.parse.urlencode({
            "db": "pubmed",
            "term": term,
            "mindate": since,
            "maxdate": until,      # 与 mindate 成对，否则过滤被 NCBI 忽略
            "datetype": "edat",
            "retmax": ESEARCH_PAGE_SIZE,
            "retstart": retstart,
            "retmode": "json",
            "tool": "ReviewFlow",
        })
        raw = open_(f"{ESEARCH_URL}?{params}")
        try:
            payload = json.loads(raw.decode("utf-8"))
            page = payload["esearchresult"]["idlist"]
            total = int(payload["esearchresult"].get("count") or 0)
        except (UnicodeDecodeError, ValueError, KeyError, TypeError) as exc:
            raise PubMedPluginError(f"esearch 响应不可解析：{exc}") from exc
        if not isinstance(page, list):
            raise PubMedPluginError("esearch 响应中的 idlist 不是列表")
        # review67 L-50：count 是 NCBI 的权威命中数（此前解析后即丢弃）。
        # total ≥ RETMAX 时窗口必然截断——第一页即可判定，直接短路抛
        # PubMedPluginError（调用方 main._pubmed_unavailable 对含「截断」
        # 文案给体量类可执行指引），不再空跑后续 esearch 分页和 50 批
        # efetch；也不再依赖 len(articles) 反推截断（esearch 空白项、
        # medline 无 PMID 记录等产出缺口会让截断被误判为完整增量）。
        if total >= RETMAX:
            raise PubMedPluginError(
                f"本次增量命中 {total} 条，超过单次上限 {RETMAX} 条，结果被截断")
        # review67 L-49：retstart 分页不做 PMID 去重时，跨页结果集漂移
        #（edat 窗口实时增长）造成的重复 PMID 会原样透传——灌水待审计数、
        # 翻倍 efetch 流量。这里跨页去重（保持首见顺序，空白项照旧丢弃）。
        for pmid in page:
            norm = str(pmid).strip()
            if norm and norm not in seen:
                seen.add(norm)
                pmids.append(norm)
        retstart += ESEARCH_PAGE_SIZE
        if not page or retstart >= min(total, RETMAX) or len(pmids) >= RETMAX:
            break
    pmids = pmids[:RETMAX]
    if not pmids:
        return PubMedUpdates([], total_hits=total)

    # efetch 分页（r8-r3 评审 blocking，真网实测）：单条约 4.1-4.2 KB，10000 条
    # ≈39.7 MiB，会直接撞上读取上限。按 EFETCH_PAGE_SIZE 拉取，任一批失败整次
    # 降级（不返回半截结果，避免把不完整增量当成完整增量写进导入待审）。
    articles: list[dict] = []
    for start in range(0, len(pmids), EFETCH_PAGE_SIZE):
        batch = pmids[start:start + EFETCH_PAGE_SIZE]
        form = urllib.parse.urlencode({
            "db": "pubmed",
            "id": ",".join(batch),
            "rettype": "medline",
            "retmode": "text",
            "tool": "ReviewFlow",
        }).encode("utf-8")
        raw = open_(EFETCH_URL, form)
        try:
            text = raw.decode("utf-8", errors="replace")
        except (UnicodeDecodeError, ValueError) as exc:  # pragma: no cover
            raise PubMedPluginError(f"efetch 响应不可解码：{exc}") from exc
        articles.extend(parse_medline_records(text))
    if not articles:
        raise PubMedPluginError("efetch 返回的 MEDLINE 文本中没有任何记录")
    return PubMedUpdates(articles, total_hits=total)


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

    失败契约（MM10-14 归一）：``text`` 非 str（None/数字/列表/bytes 等）抛
    :class:`PubMedPluginError`，不再让 AttributeError/TypeError 硬逃逸。
    """
    if not isinstance(text, str):
        raise PubMedPluginError(
            f"MEDLINE 文本必须为 str，得到 {type(text).__name__}。")
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
