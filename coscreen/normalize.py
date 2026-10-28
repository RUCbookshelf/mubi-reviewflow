"""字段规范化工具 —— DOI/标题/作者/年份/唯一键 的清洗与派生（见 SPEC.md §3）。

所有函数均为纯函数：无 IO、无随机性、无时间依赖，输出确定性。
"""

from __future__ import annotations

import hashlib
import re
import string
import unicodedata

# DOI 前缀（大小写不敏感；scheme 可省略，覆盖用户从地址栏粘贴的形态）：
# https://doi.org/ 、http://dx.doi.org/ 、doi.org/ 、dx.doi.org/ 、www.doi.org/ 、doi:
# （全角冒号 "doi：" 已由 NFKC 折叠为 ASCII 冒号后再匹配）
_DOI_PREFIX_RE = re.compile(
    r"^(?:(?:https?://)?(?:www\.)?(?:dx\.)?doi\.org/|doi\s*:)", re.IGNORECASE
)
# 连续空白（含全角空格与各类 Unicode 空白）
_MULTI_WS_RE = re.compile(r"\s+")
# 独立的 4 位数字（前后都不是数字，避免从 12345 中截取）
_YEAR_RE = re.compile(r"(?<!\d)(\d{4})(?!\d)")
# 成对圆括号段（extract_year 优先取括号内年份用）
_PAREN_RE = re.compile(r"\(([^()]*)\)")
# 年份合法区间（与既有行为一致）
_YEAR_MIN, _YEAR_MAX = 1500, 2100

# 标题首尾需剥离的字符：ASCII 标点 + 空白 + 常用中英文排版标点
_TITLE_STRIP_CHARS = string.punctuation + string.whitespace + "，。、；：？！“”‘’《》〈〉【】（）·—…．"

# build_zotero_key 在 raw 中查找的候选键名（小写）
_KEY_FIELDS: tuple[str, ...] = ("key", "id", "an", "accession_number")
# build_zotero_key 生成 auto-* 键时在 raw 中查找第一作者/年份的候选字段名（小写）；
# RIS（rispy 可读键名与原始标签）与 Zotero CSV 原始表头两种形态都覆盖
_AUTHOR_FIELDS: tuple[str, ...] = (
    "authors", "author", "first_authors", "creators", "creator", "au",
)
_YEAR_LOOKUP_FIELDS: tuple[str, ...] = (
    "year", "date", "publication year", "publication_year",
    "year_of_publication", "py", "da",
)

# 人名姓氏中的常见助词前缀（荷兰/德语/法语/意大利语/西班牙语等），
# 比较姓氏时剥离："van der Berg" 与 "Berg" 视为同姓（U1-P2/U1-P11）
_NAME_PARTICLES: frozenset[str] = frozenset(
    "van von de der den del della delle di da das dos du la le les "
    "ten ter te ver vanden vander".split()
)

# 机构作者判定标记：功能词与常见组织词（小写整词匹配）。无逗号整串中出现
# 任一标记，或实义词数 >= 4 且不含姓名缩写时，按机构作者整串比较（U1-P2）
_ORG_MARKER_WORDS: frozenset[str] = frozenset(
    """the of and for with university institute institution university-hospital
    organization organisation collaboration group network committee council
    foundation association society hospital college school faculty department
    centre center program programme project team task force working
    initiative bureau laboratory lab consortium alliance panel board trust
    union federation who unicef nih cochrane""".split()
)


def normalize_doi(doi: str) -> str:
    """规范化 DOI；空值返回 ""。

    步骤（U1-P5/U1-P8/U1-P9）：
    1. 删除全部 Cf 类不可见字符（U+200B 零宽空格、U+200D 零宽连接符、
       U+FEFF BOM、U+00AD 软连字符等）——``\\s`` 不匹配 Cf 类，必须按
       Unicode 类别剔除，否则同一 DOI 的变体穿透精确与模糊两层去重；
    2. NFKC 折叠全角冒号/数字/字母（"doi：１０.１０００/α" -> ASCII 形态）；
    3. 删除全部空白并转小写；
    4. 循环剥离前缀（含 scheme-less 的 doi.org/dx.doi.org/www.doi.org
       与 "doi:"，重复前缀也剥，如 "https://doi.org/doi:10.x/y"）；
    5. 剥离尾部引用标点（句点/逗号/分号/冒号及未配对的右括号）。

    dedup._norm_doi 委托本函数，去重比较与解析存储共用同一份口径。
    """
    if not doi:
        return ""
    s = "".join(ch for ch in str(doi) if unicodedata.category(ch) != "Cf")
    s = unicodedata.normalize("NFKC", s)
    s = _MULTI_WS_RE.sub("", s).lower()
    while True:
        m = _DOI_PREFIX_RE.match(s)
        if not m:
            break
        s = s[m.end():]
    return _strip_doi_tail_punct(s)


def _strip_doi_tail_punct(s: str) -> str:
    """剥离 DOI 尾部的引用标点："10.1000/abc." -> "10.1000/abc"。

    右括号/方括号仅在未配对（多于对应的左括号）时剥离，避免破坏
    合法含括号的 DOI（如 "10.1000/(abc).def"）。中文句读（。，、；：
    ！？及 CJK 右引号/右括号）在 DOI 中绝无合法位置，同样按尾部
    标点或未配对右括号剥离（U1-P5 残余：CJK 尾标点使同文 DOI 不相等，
    重复文献失去 exact 匹配标记）。
    """
    pairs = {")": "(", "]": "[", "}": "{",
             "】": "【", "」": "「", "』": "『", "”": "“", "’": "‘"}
    # NFKC 先行折叠（全角 ！？》→ !?>、，；：→ 半角），ASCII 与不可折叠的
    # CJK 句读（。，、”’…‥·】」』）都须命中，两种形态不可漏。
    while s:
        ch = s[-1]
        if ch in ".,;:!?>。，、；：！？”’…‥·】」』》":
            s = s[:-1]
            continue
        if ch in pairs and s.count(pairs[ch]) < s.count(ch):
            s = s[:-1]
            continue
        break
    return s


def normalize_title(title: str) -> str:
    """规范化标题：压缩内部空白、剥离首尾标点与空白。

    不做小写化——小写在去重比较时进行（见 SPEC.md §4 / dedup.py）。
    """
    if not title:
        return ""
    s = _MULTI_WS_RE.sub(" ", str(title)).strip()
    return s.strip(_TITLE_STRIP_CHARS).strip()


def _clean_part(part: object) -> str:
    """清洗单个作者名：压缩空白、去首尾空白。"""
    return _MULTI_WS_RE.sub(" ", str(part)).strip()


def normalize_authors(raw: str | list | None) -> str:
    """规范化作者字段：list → "a; b"；str 按分号拆分后清洗再拼回。

    空段丢弃；无有效作者时返回 ""。与 Zotero "Creator" 列的 "; " 分隔约定一致。
    """
    if raw is None:
        return ""
    if isinstance(raw, (list, tuple)):
        parts = [_clean_part(p) for p in raw]
    else:
        parts = [_clean_part(p) for p in str(raw).split(";")]
    return "; ".join(p for p in parts if p)


def extract_year(date_str: str) -> int | None:
    """保守提取年份；无法确定时返回 None 而不是取首个数字（U1-P13）。

    优先级（真实杂乱元数据中更可靠的信号在前）：
    1. 括号内年份——再版/重印声明把真实出版年放括号里
       （"Reprint of the 1905 original (2012)" -> 2012）；多个时取最后一个；
    2. 句末年份——允许尾随标点/空白
       （"Volume 1850, issued 2019" -> 2019、"April 2021" -> 2021）；
    3. 全串唯一的合法年份（"2020-12-01" -> 2020、"© 2011 Springer" -> 2011）；
    4. 多个候选且无上述任一信号 -> None（宁缺毋错）。

    合法区间 1500-2100 与既有行为一致。
    """
    if date_str is None:
        return None
    text = str(date_str).strip()
    if not text:
        return None

    def _valid(year_text: str) -> int | None:
        year = int(year_text)
        return year if _YEAR_MIN <= year <= _YEAR_MAX else None

    # 1) 括号内年份优先
    paren_years: list[int] = []
    for paren in _PAREN_RE.finditer(text):
        for m in _YEAR_RE.finditer(paren.group(1)):
            year = _valid(m.group(1))
            if year is not None:
                paren_years.append(year)
    if paren_years:
        return paren_years[-1]

    # 2) 句末年份（剥掉尾部标点/空白后，末尾恰为一个 4 位年份）
    tail = text.rstrip(string.punctuation + string.whitespace + "，。；、）")
    end_match = re.search(r"(?<!\d)(\d{4})$", tail)
    if end_match:
        year = _valid(end_match.group(1))
        if year is not None:
            return year

    # 3) 唯一候选
    candidates = [y for m in _YEAR_RE.finditer(text) if (y := _valid(m.group(1))) is not None]
    if len(set(candidates)) == 1:
        return candidates[0]
    return None


def _raw_first_text(raw: dict | None, fields: tuple[str, ...]) -> str:
    """在原始记录中按候选字段名（小写）取首个非空文本；列表取首个元素。"""
    if not raw:
        return ""
    lowered = {str(k).strip().lower(): v for k, v in raw.items()}
    for field in fields:
        value = lowered.get(field)
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            value = next((item for item in value if str(item).strip()), "")
        text = str(value).strip()
        if text:
            return text
    return ""


def _is_name_initial(word: str) -> bool:
    """是否为姓名缩写（R6-D3 修正：两字母混合大小写是真实姓氏）。

    - 单字母（可带点）恒为缩写："J."、"A"；
    - 两字母仅**全大写**视为 PubMed 风格双名缩写（"Smith JA" 的 "JA"）；
      混合大小写的两字母是真实姓氏（Wu/Li/Xu/He 等中国姓氏，"John Wu"、
      "Wei Li" 的末词），不得误判为缩写——修复前 'John Wu' 的姓氏被回退
      成 'john'、'Wei Li' 被回退成 'wei'（R6-D3 复审实测）；
    - 全大写双词（"WEI LI"）与全大写缩写形态上不可区分，维持按缩写处理
      的旧口径（残余如实声明：此类输入姓氏取前词）。
    """
    core = word.strip(".,")
    if not (0 < len(core) <= 2 and core.isalpha()):
        return False
    if len(core) == 1:
        return True
    return core.isupper() and core == word.strip(".")


def _is_trailing_name_token(word: str) -> bool:
    """姓名串**末位** token 是否为缩写（R6-D3 收口）：

    - 单字母（可带点）恒为缩写；两字母全大写（PubMed "JA"）为缩写；
    - 两字母**全小写或带点的混合形态**（"Smith ja"/"Smith J.P." 的
      "ja"/"J.P."）在末位按缩写——修复前被当姓氏（'Smith ja' 曾得
      姓 'ja'，同名同题重导因作者门被拆两行，破坏 R1 幂等）；
    - 两字母**混合大小写且无点**（Wu/Li/Xu/He 等中国姓氏）是真实姓氏，
      不按缩写（R6-D3 目标 'John Wu'→wu 保持）。
    """
    core = word.strip(".,")
    if not core or not core.isalpha() or len(core) > 2:
        return False
    if len(core) == 1:
        return True
    return core.isupper() or core.islower() or "." in word


def _looks_institutional(words: list[str]) -> bool:
    """无逗号作者串是否更像机构作者（U1-P2）。

    判定：出现功能/组织词标记，或实义词（剥助词后）>= 4 个且不含
    姓名缩写（人名极少超过 3 个实义词）。
    """
    if len(words) < 2:
        return False
    lowered = [w.lower().strip(".,") for w in words]
    if any(w in _ORG_MARKER_WORDS for w in lowered):
        return True
    content = [w for w in lowered if w not in _NAME_PARTICLES]
    has_initial = any(_is_name_initial(w) for w in words)
    return len(content) >= 4 and not has_initial


def first_author_surname(authors: str | list | None) -> str:
    """第一作者的比较形式：姓氏（小写），供作者门与生成键共用（U1-P2/U1-P11）。

    - 识别两种姓名顺序："Smith, John"（姓在前，逗号分隔）与 "John Smith"
      （名在前）都得到 "smith"；带缩写尾的 "Smith J"/"Smith J. A." 亦然；
    - 姓氏助词前缀（van/von/de/der/di/da/…）剥离后比较：
      "van der Berg, J." -> "berg"，"van Dijk, A." -> "dijk"（不同姓，可区分）；
    - 机构作者（无逗号、多词、含功能/组织词或 >=4 个实义词）按整串小写
      比较："The Cochrane Collaboration" -> "the cochrane collaboration"；
    - 空作者/空首段返回 ""——调用方必须视为门控不通过，而不是两空相等。

    纯函数、确定性；仅用于比较，不回写解析结果。
    """
    head = ""
    if authors is None:
        return ""
    if isinstance(authors, (list, tuple)):
        head = str(authors[0]).strip() if authors else ""
    else:
        head = str(authors).split(";")[0].strip()
    if not head:
        return ""

    if "," in head:
        # 姓在前："van der Berg, J. A." -> 姓氏段 "van der Berg"
        surname_words = [w for w in head.split(",")[0].split() if w]
    else:
        words = [w for w in head.split() if w]
        if not words:
            return ""
        if _looks_institutional(words):
            return " ".join(words).lower()
        # 名在前（"John Smith"/"Maria de la Cruz"）。若结尾是缩写
        # （"Smith J" PubMed 风格、"van Dijk A"），则前面整段是姓氏；
        # 否则末词是姓氏。
        # 尾位扫描：末 token 用尾位豁免规则（两字母全小写/带点也按缩写），
        # 其后逐个回退只认严格缩写（"Smith J. A." 的中间缩写）
        end = len(words)
        while end > 1:
            token = words[end - 1]
            is_initial = (_is_trailing_name_token(token)
                          if end == len(words) else _is_name_initial(token))
            if not is_initial:
                break
            end -= 1
        if end == len(words):
            surname_words = [words[-1]]
        else:
            surname_words = words[:end]

    # 剥离姓氏开头的助词前缀（保留至少一词）
    index = 0
    while index < len(surname_words) - 1 and surname_words[index].lower() in _NAME_PARTICLES:
        index += 1
    surname_words = surname_words[index:]
    if not surname_words:
        return ""
    return " ".join(surname_words).lower().strip(".,:")


def first_author_given_names(authors: str | list | None) -> str:
    """第一作者的名（小写、逐词归一），供同姓不同名的区分（R6-D4）。

    - "Smith, John" -> "john"；"Smith, J. A." -> "j a"；
    - "John Wu" -> "john"（末词为姓）；"Smith JA" -> "ja"（缩写也是名）；
    - 空作者/空首段 -> ""（调用方按「缺名视为兼容」处理，不得两空相等）。

    纯函数；与 first_author_surname 共用同一姓名顺序/缩写判定口径。
    """
    head = ""
    if authors is None:
        return ""
    if isinstance(authors, (list, tuple)):
        head = str(authors[0]).strip() if authors else ""
    else:
        head = str(authors).split(";")[0].strip()
    if not head:
        return ""
    if "," in head:
        given_text = head.split(",", 1)[1]
    else:
        words = [w for w in head.split() if w]
        if len(words) < 2:
            return ""  # 单词串无从分名姓
        end = len(words)
        while end > 1:
            token = words[end - 1]
            is_initial = (_is_trailing_name_token(token)
                          if end == len(words) else _is_name_initial(token))
            if not is_initial:
                break
            end -= 1
        if end == len(words):
            given_text = " ".join(words[:-1])   # 末词是姓
        else:
            given_text = " ".join(words[end:])  # 缩写尾是名
    tokens = [token.strip(".,: ").lower() for token in given_text.split()]
    return " ".join(token for token in tokens if token)


def given_names_compatible(given_a: str, given_b: str) -> bool:
    """名的兼容性（R6-D4）：能证明不同才不兼容，缺名按元数据补全口径兼容。

    - 任一侧为空 -> True；
    - 双侧均含全名词 -> 全名词序列必须一致（"john" vs "jane" -> False，
      「Editorial」Smith, John vs Smith, Jane 同键同题坍缩的根因）；
    - 至少一侧只有缩写 -> 首字母有交集即兼容（"j" vs "john" -> True）。
      纯缩写互比（"j a" vs "j b"）无法证明不同，按兼容（残余如实声明）。
    """
    tokens_a = [t for t in given_a.split() if t]
    tokens_b = [t for t in given_b.split() if t]
    if not tokens_a or not tokens_b:
        return True
    full_a = [t for t in tokens_a if len(t) > 1]
    full_b = [t for t in tokens_b if len(t) > 1]
    if full_a and full_b:
        return full_a == full_b
    return bool({t[0] for t in tokens_a} & {t[0] for t in tokens_b})


def years_compatible(year_a: int | None, year_b: int | None) -> bool:
    """年份兼容（R6-D2）：双侧均明确且不同才不兼容；缺一侧按元数据口径兼容。"""
    if year_a is None or year_b is None:
        return True
    try:
        return int(year_a) == int(year_b)
    except (TypeError, ValueError):
        return True


def build_zotero_key(
    raw: dict, content_hash: str,
    *, authors: str | list | None = None, year: int | None = None,
) -> str:
    """从原始行/记录中取唯一键；缺失时返回 "auto-" + 摘要前 12 位（U1-P6）。

    候选键名（大小写不敏感）：Key（CSV）/ ID、AN（RIS 及其 rispy 映射名）。

    生成键掺入第一作者姓氏、**名**与年份（R6-D4：同姓不同名不再坍缩为
    同一 auto-* 键；authors/year 显式给出，否则在 raw 中按候选字段名查找）：
    同标题、不同作者/不同年份的记录不再坍缩导致主键覆盖丢条目。摘要输入
    为 content_hash|姓氏|名|年份 的 sha1，确定性可复现。

    R1 铁律：键公式变更不破坏旧库重导幂等——既有 auto-* 键不重写（本函数
    只在导入解析时被调用），重导经 stabilize 的分层身份匹配（_fallback_id →
    content_hash → 标题+姓氏+年份）沿用库内既有键，2.1.3 预置库重导同一
    文件零新增行由 LegacyAutoKeyReimportTests 验收钉住。
    """
    if raw:
        lowered = {str(k).strip().lower(): v for k, v in raw.items()}
        for field in _KEY_FIELDS:
            value = lowered.get(field)
            if value is None:
                continue
            if isinstance(value, (list, tuple)):
                value = value[0] if value else ""
            text = str(value).strip()
            if text:
                return text
    if authors is None:
        authors = _raw_first_text(raw, _AUTHOR_FIELDS)
    surname = first_author_surname(authors)
    given = first_author_given_names(authors)
    if year is None:
        year = extract_year(_raw_first_text(raw, _YEAR_LOOKUP_FIELDS))
    digest = hashlib.sha1(
        f"{content_hash}|{surname}|{given}|{year if year is not None else ''}".encode("utf-8")
    ).hexdigest()
    return "auto-" + digest[:12]
