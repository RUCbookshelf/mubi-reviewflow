"""HTTP 安全设施：安全响应头、登录限流、上传校验与路径防护。

- 安全头由纯 ASGI 中间件注入（不经 BaseHTTPMiddleware，避免流式响应陷阱），
  加头逻辑同时以纯函数形式供各层早拒响应复用（R6-S5：最外层中间件在
  SecurityHeaders 之前直接应答的 400/401/411/413 曾裸奔，无 nosniff/CSP/CORS）；
- 登录限流：内存滑动窗口，按**直连对端 IP**计数（5 次/分钟），超限 429 +
  Retry-After；X-Forwarded-For 完全不信任（见 :func:`rate_limit_key`）；
- 上传安全：Content-Length 预检（解析前 413）+ boundary 解析对齐 Starlette
  （缺失/不可解析 400）+ 大小上限 + 文件 part 数/字段 part 数/单字段大小
  流式限制 + 扩展名白名单 + 文件名清洗（防路径穿越/控制字符/Windows 保留
  设备名）、magic bytes 校验；
- 路径防护：is_relative_to 判定解析后的真实路径不得逃出白名单根目录。

注意：``%PDF-`` 等 magic bytes 校验只确认文件头形状，**不是内容安全边界**
（U5-P9/P11：polyglot 可伪造文件头）；真正的缓解是同源回放 + nosniff +
CSP，以及保存时用 pypdf 实际解析（0 页拒绝，见 coscreen.fulltext.save_pdf）。
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import threading
import time
from pathlib import Path

__all__ = [
    "MAX_UPLOAD_BYTES",
    "MAX_FIELD_PARTS",
    "MAX_FIELD_PART_BYTES",
    "EventTicketStore",
    "MultipartStructureRejected",
    "RateLimiter",
    "SecurityHeadersMiddleware",
    "UploadSizeLimitMiddleware",
    "apply_security_headers",
    "content_security_policy",
    "cors_response_headers",
    "inline_script_hashes",
    "clean_upload_name",
    "check_pdf_bytes",
    "check_text_bytes",
    "is_within",
    "rate_limit_key",
    "safe_path_under",
]

#: 上传大小上限（100MB，任务书要求）
MAX_UPLOAD_BYTES = 100 * 1024 * 1024

#: PDF 文件头（与 coscreen.fulltext.PDF_MAGIC 一致）
_PDF_MAGIC = b"%PDF-"

#: CSV/RIS 允许的扩展名（小写、含点）
TEXT_EXTENSIONS = {".csv", ".ris", ".txt", ".tsv"}
PDF_EXTENSIONS = {".pdf"}

#: 文件名中不允许出现的字符与序列
_UNSAFE_NAME_RE = re.compile(r"[\\/:*?\"<>|\x00-\x1f\x7f]")


# ---------------------------------------------------------------------------
# CSP：内联脚本哈希白名单
# ---------------------------------------------------------------------------

#: 前端静态目录（仓库布局与打包布局均为 custom_backend 的同级目录）
_FRONTEND_DIR = Path(__file__).resolve().parent.parent / "custom_frontend"
_INLINE_SCRIPT_RE = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.S)
_HASH_CACHE: tuple[float, tuple[str, ...]] | None = None


def inline_script_hashes(frontend_dir: Path | None = None) -> tuple[str, ...]:
    """index.html 内联脚本块的 CSP sha256 白名单（按 mtime 缓存，变更即重算）。"""
    global _HASH_CACHE
    index = (frontend_dir or _FRONTEND_DIR) / "index.html"
    try:
        mtime = index.stat().st_mtime
    except OSError:
        return ()
    if _HASH_CACHE is not None and _HASH_CACHE[0] == mtime:
        return _HASH_CACHE[1]
    try:
        html = index.read_text(encoding="utf-8")
    except OSError:
        return ()
    hashes = tuple(
        "'sha256-" + base64.b64encode(hashlib.sha256(m.group(1).encode("utf-8")).digest()).decode("ascii") + "'"
        for m in _INLINE_SCRIPT_RE.finditer(html)
    )
    _HASH_CACHE = (mtime, hashes)
    return hashes


def content_security_policy() -> str:
    """同源部署用的 CSP（内联脚本走哈希白名单，不用 unsafe-inline）。"""
    script_src = " ".join(("'self'",) + inline_script_hashes())
    return (
        "default-src 'self'; "
        f"script-src {script_src}; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; "
        "frame-src 'self' blob:; "
        "worker-src 'self' blob:; "
        "connect-src 'self'; "
        "object-src 'none'; base-uri 'self'; form-action 'self'"
    )


# ---------------------------------------------------------------------------
# 安全响应头中间件（纯 ASGI）
# ---------------------------------------------------------------------------

class SecurityHeadersMiddleware:
    """给每个 HTTP 响应追加安全响应头（任务书第 8 条）。

    CSP 说明（同源部署 = 单端口拓扑，见 build/launcher.py）：
    - ``script-src`` 用 **内联脚本的 sha256 哈希白名单**，而不是 ``'unsafe-inline'``：
      SPA 的全部脚本内联在 index.html，哈希随文件自动重算（见 inline_script_hashes），
      这样注入的脚本仍无法执行——否则安装包里的前端会因为缺 script-src 而整段被
      浏览器拦下（应用变成没有 JS 的死页面）；
    - ``style-src`` 需 ``'unsafe-inline'``（内联 <style> 与 style 属性）；
    - ``img-src``/``frame-src``/``worker-src`` 放行 ``blob:``：PDF 内嵌预览用
      blob iframe、PNG 导出前的 SVG 光栅化用 blob img、pdf.js 用同源 worker；
    - HTML 文档额外加 ``Cache-Control: no-store``：前端更新后浏览器不得继续用旧
      index.html（否则「改了代码却仍跑旧 JS」，排查成本极高）。
    """

    HEADERS = [
        (b"x-content-type-options", b"nosniff"),
        (b"x-frame-options", b"DENY"),
        (b"x-xss-protection", b"1; mode=block"),
        (b"referrer-policy", b"strict-origin-when-cross-origin"),
    ]

    def __init__(self, app):  # noqa: ANN001  ASGI 约定
        self.app = app

    async def __call__(self, scope, receive, send):  # noqa: ANN001
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = apply_security_headers(list(message.get("headers") or []))
                ctype = next((v for k, v in headers if k.lower() == b"content-type"), b"")
                if b"text/html" in ctype.lower() and not any(
                        k.lower() == b"cache-control" for k, _ in headers):
                    headers.append((b"cache-control", b"no-store, max-age=0"))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_wrapper)


def apply_security_headers(headers: list) -> list:
    """给响应头列表追加安全响应头（幂等：已有同名头不覆盖），R6-S5。

    SecurityHeadersMiddleware 与各层中间件的**早拒响应**共用：最外层
    中间件（UploadSizeLimit/TaskLock）在 SecurityHeaders 之前直接应答时，
    响应同样必须带 nosniff/XFO/CSP 等，否则 401/413 早拒构成安全头回归。
    """
    have = {k.lower() for k, _ in headers}
    out = list(headers)
    for name, value in SecurityHeadersMiddleware.HEADERS:
        if name not in have:
            out.append((name, value))
    if b"content-security-policy" not in have:
        out.append((b"content-security-policy", content_security_policy().encode("ascii")))
    return out


def cors_response_headers(origin: str | None,
                          allowed_origins) -> list:  # noqa: ANN001
    """早拒响应的 CORS 简单响应头（R6-S5；origin 命中白名单才返回非空）。

    与 CORSMiddleware 对非预检请求的行为对齐：``Access-Control-Allow-
    Origin``（回显具体 origin，不用 *）+ ``Access-Control-Allow-
    Credentials`` + ``Vary: Origin``；预检专用头（Allow-Methods/Headers/
    Max-Age）不属于简单响应，不在此加。浏览器跨域读取上传 401/413 等
    早拒错误（而不是被吞成网络错误）依赖这几个头。
    """
    if not origin:
        return []
    allowed = {str(o).rstrip("/") for o in (allowed_origins or ())}
    if origin.rstrip("/") not in allowed:
        return []
    try:
        encoded = origin.encode("ascii")
    except UnicodeEncodeError:
        return []
    return [
        (b"access-control-allow-origin", encoded),
        (b"access-control-allow-credentials", b"true"),
        (b"vary", b"Origin"),
    ]


# ---------------------------------------------------------------------------
# 登录限流（内存滑动窗口）
# ---------------------------------------------------------------------------

class RateLimiter:
    """按键（客户端 IP）的滑动窗口限流器。

    - ``max_events`` 次 / ``window_seconds`` 秒；
    - 线程安全（uvicorn 单进程多线程处理同步端点）；
    - key 数量有全局上限（``max_keys``）：达到上限先惰性清理过期桶，
      仍满则逐出最久未活跃的桶——远程攻击者用海量伪造源 IP 打登录
      端点也无法无限撑大 ``_events`` 内存；
    - 记录仅存内存，重启即清零（MVP 约定，反代理层可再加一层）；
    - 计数是**进程内**的：多 worker（uvicorn workers>1 / 多实例）部署下各
      进程独立计数，限流阈值按进程数放大。官方启动器是单进程
      （build/launcher.py 单 uvicorn），如改为多 worker 需在反代理层限流。
    """

    #: 默认全局 key 上限（10 万个 IP 桶，约几十 MB 量级以内）
    DEFAULT_MAX_KEYS = 100_000

    def __init__(self, max_events: int, window_seconds: float,
                 max_keys: int = DEFAULT_MAX_KEYS) -> None:
        if max_keys < 1:
            raise ValueError("max_keys must be positive")
        self.max_events = max_events
        self.window = window_seconds
        self.max_keys = max_keys
        self._events: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str, now: float | None = None) -> tuple[bool, int]:
        """记录一次尝试并判定是否放行。

        返回 ``(allowed, retry_after_seconds)``：拒绝时 retry_after 为需等待
        的整秒数（至少 1），放行时为 0。
        """
        moment = time.monotonic() if now is None else now
        with self._lock:
            if len(self._events) >= self.max_keys:
                self._evict(moment)
            bucket = [t for t in self._events.get(key, []) if moment - t < self.window]
            if len(bucket) >= self.max_events:
                retry_after = max(1, int(self.window - (moment - bucket[0])) + 1)
                self._events[key] = bucket
                return False, retry_after
            bucket.append(moment)
            self._events[key] = bucket
            return True, 0

    def peek(self, key: str, now: float | None = None) -> tuple[bool, int]:
        """只判定是否放行，**不记录**本次尝试（R6-S1：成功登录不计数的
        前置检查——bcrypt 校验前先挡掉已达阈值的对端，防口令猜解耗 CPU；
        真实计数由 record() 在认证失败时补记）。
        """
        moment = time.monotonic() if now is None else now
        with self._lock:
            bucket = [t for t in self._events.get(key, []) if moment - t < self.window]
            if len(bucket) >= self.max_events:
                retry_after = max(1, int(self.window - (moment - bucket[0])) + 1)
                self._events[key] = bucket
                return False, retry_after
            return True, 0

    def record(self, key: str, now: float | None = None) -> None:
        """补记一次失败尝试（与 peek 配对；成功路径不调用即不计数）。"""
        moment = time.monotonic() if now is None else now
        with self._lock:
            if len(self._events) >= self.max_keys:
                self._evict(moment)
            bucket = [t for t in self._events.get(key, []) if moment - t < self.window]
            bucket.append(moment)
            self._events[key] = bucket

    def reset(self, key: str) -> None:
        """成功登录/注册清零该对端计数（handoff：成功即清零，合法用户
        输错几次后登成功不遗留满桶）。"""
        with self._lock:
            self._events.pop(key, None)

    def _evict(self, moment: float) -> None:
        """达到 key 上限时收缩存储：先清全过期桶，仍满则逐出最久未活跃桶。"""
        self._events = {
            key: bucket for key, bucket in self._events.items()
            if bucket and moment - bucket[-1] < self.window
        }
        excess = len(self._events) - self.max_keys + 1
        if excess > 0:
            oldest = sorted(self._events, key=lambda key: self._events[key][-1])[:excess]
            for key in oldest:
                del self._events[key]


#: 登录限流器：5 次/分钟/IP
login_limiter = RateLimiter(max_events=5, window_seconds=60.0)


def rate_limit_key(action: str, client_host: str | None,
                   forwarded_for: str | None = None) -> str:
    """限流桶 key：按**直连对端 IP**分桶，X-Forwarded-For 一律忽略（U5-P5/R6-S1）。

    ``forwarded_for`` 参数仅为调用方兼容保留，**不参与分桶**：

    - r5 曾改为「带 XFF 即并入全局 ``xff-untrusted`` 单桶」，但部署文档的
      nginx 示例对每个请求都追加 X-Forwarded-For——反代部署下全站登录/
      注册共享 5 次/分钟，未认证者 5 个请求即可把所有人锁在门外 60 秒
      （R6-S1 复审实测的 DoS 放大），已回退到修复前语义；
    - 直连部署（桌面 launcher 显式 ``proxy_headers=False``）：对端即真实
      客户端 IP，客户端伪造/轮换 XFF 无法影响桶 key，无法绕过限流；
    - 反向代理部署：应用层看到的所有对端都是反代机，应用层限流退化为
      单一共享桶——**此时限流必须由反代层按真实客户端 IP 完成**
      （limit_req 完整配置见 docs/deployment/UBUNTU_SERVER.md）。
    """
    return f"{action}:{client_host or '?'}"


# ---------------------------------------------------------------------------
# 上传校验
# ---------------------------------------------------------------------------

#: Windows 保留设备名（CON/NUL/COM1..9/LPT1..9 等，带任意扩展名同样保留）
_WINDOWS_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)

#: 清洗后文件名总长上限（含扩展名）
_MAX_NAME_LENGTH = 180

#: 视为"扩展名"的最大长度（含点）；超过即按无扩展名整体截断（评审回归：
#: 'a.'+'b'*300 这类"扩展名本身超长"的名字此前会突破 180 上限）
_MAX_EXTENSION_LENGTH = 40


def clean_upload_name(filename: str, fallback: str = "upload") -> str:
    """清洗上传文件名：去目录成分、去控制字符、去 ".."、规避保留设备名、限长。

    只保留最终成分（``Path(...).name`` 语义 + 显式剥离分隔符），清洗后为空
    回退 fallback。**返回值只用于展示/派生名称，落盘路径一律由服务端构造。"""

    def _truncate(name: str) -> str:
        # U5-P8：先取最后一个扩展名再截断主体，保证 ".csv" 等合法后缀
        # 不会被截掉（截掉后 check_extension 会把超长文件名误判 415）；
        # "扩展名"本身超长时按无扩展名整体截断，保证结果恒 ≤ 180。
        if len(name) <= _MAX_NAME_LENGTH:
            return name
        stem, dot, ext = name.rpartition(".")
        if not stem or len(dot) + len(ext) > _MAX_EXTENSION_LENGTH:
            return name[:_MAX_NAME_LENGTH]
        return stem[: _MAX_NAME_LENGTH - (len(dot) + len(ext))] + dot + ext

    name = (filename or "").replace("\\", "/").split("/")[-1]
    name = _UNSAFE_NAME_RE.sub("", name).strip().strip(".")
    if name in ("", ".", ".."):
        name = fallback
    # U5-P8：Windows 保留设备名（NUL 落盘静默丢数据、CON/COM1 可能重定向
    # 设备）加下划线前缀规避。Win32 按第一个点之前的基名判定，因此
    # "NUL.tar.gz"/"COM1.csv" 同样命中。
    base = name.split(".", 1)[0].rstrip(". ")
    if base.upper() in _WINDOWS_RESERVED_NAMES:
        name = "_" + name
    return _truncate(name)


def stem_of(filename: str) -> str:
    """清洗后文件名的主干（去最后一个扩展名），供合并页取筛选员/编码员名。"""
    name = clean_upload_name(filename)
    return name.rsplit(".", 1)[0].strip() if "." in name else name


def check_size(data: bytes) -> None:
    """超限抛 ``UploadRejected``（映射为 413）。"""
    if not data:
        raise UploadRejected("上传内容为空。", status=413)
    if len(data) > MAX_UPLOAD_BYTES:
        raise UploadRejected(
            f"文件超过大小上限（{MAX_UPLOAD_BYTES // (1024 * 1024)} MB）。", status=413
        )


def check_extension(filename: str, allowed: set[str]) -> None:
    ext = Path(clean_upload_name(filename)).suffix.lower()
    if ext not in allowed:
        raise UploadRejected(
            f"不支持的文件扩展名：{ext or '(无)'}，仅允许 {sorted(allowed)}。", status=415
        )


def check_pdf_bytes(data: bytes) -> None:
    """PDF 上传校验：大小 + %PDF- 头。"""
    check_size(data)
    if not data.startswith(_PDF_MAGIC):
        raise UploadRejected("不是有效的 PDF 文件（缺少 %PDF- 文件头）。", status=415)


def check_text_bytes(data: bytes) -> None:
    """CSV/RIS 上传校验：大小 + 可解码为文本 + 无 NUL 字节（拒绝二进制伪装）。"""
    check_size(data)
    if b"\x00" in data:  # 全量扫描（C 级 memchr）；此前仅扫前 64KB，NUL 可藏在其后
        raise UploadRejected("文件包含二进制内容，不是有效的 CSV/RIS 文本。", status=415)
    try:
        data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise UploadRejected("文件不是有效的 UTF-8 文本。", status=415) from exc


class UploadRejected(Exception):
    """上传校验失败（status 供 HTTP 映射：413 超限 / 415 类型不符）。"""

    def __init__(self, message: str, status: int = 415) -> None:
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------------------
# 路径防护
# ---------------------------------------------------------------------------

def is_within(child: Path, root: Path) -> bool:
    """child 解析后是否位于 root 解析后的目录树内（防路径穿越逃逸）。"""
    try:
        child.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def safe_path_under(root: Path, *parts: str) -> Path:
    """在 root 下拼接白名单成分并验证不逃逸；逃逸抛 ``UploadRejected``。

    ``parts`` 中每个成分先做文件名清洗（最终成分允许子目录名同样清洗）。
    """
    cleaned = [clean_upload_name(p, "_") for p in parts]
    candidate = root.joinpath(*cleaned)
    if not is_within(candidate, root):
        raise UploadRejected("非法路径。", status=400)
    return candidate


# ---------------------------------------------------------------------------
# 请求体 Content-Length / 结构预检（U5-P3 / R6-S3）
# ---------------------------------------------------------------------------

# boundary 解析与 Starlette 同源（starlette.formparsers 同样的导入链）：
# python-multipart 的 parse_options_header——重复 boundary 参数取**最后一个**、
# 引号内的值去引号、整体值 strip。r5 自写的 _extract_boundary 取第一个且
# 不解引号，`boundary=abc; boundary=def`（body 用 def）/`boundary=";abc"` 时
# 计数器装错 key 或干脆不装（fail-open，评审实测 21 个 part 全部落盘）。
try:  # 与 starlette.formparsers 完全一致的两级回退
    from python_multipart.multipart import parse_options_header as _parse_options_header
except ModuleNotFoundError:  # pragma: no cover - starlette 同款回退
    try:
        from multipart.multipart import parse_options_header as _parse_options_header
    except ModuleNotFoundError:  # pragma: no cover
        _parse_options_header = None

#: multipart 字段 part 预检上限（R6-S3 复审 #38）。
#: 本产品全部上传端点的表单字段最多 4 个（trace 提交：preview_sha256/
#: actor_aliases/subject_mappings/confirm_unmapped；导入类 2 个 job_id/
#: source_id[s]），16 个是 4 倍余量。Starlette 自身只限 1000 字段 ×
#: 1MB/字段——字段在端点校验前**整体进内存**，复审实测 50×1MiB ≈ 50MB，
#: 按 1000×1MB 线性放大约 1GB。
MAX_FIELD_PARTS = 16

#: 单个字段 part 的大小预检上限：对齐 Starlette MultiPartParser 的
#: max_part_size（1MB/字段）再加 64KB 余量——本中间件只会拒绝 Starlette
#: 解析器自己也必拒的请求，不新增任何严格性。
MAX_FIELD_PART_BYTES = 1024 * 1024 + 64 * 1024


class _MultipartPartCounter:
    """流式统计 multipart 请求里的**文件 part** 与**字段 part** 数。

    只观察流经的字节、不修改不缓存整个请求；超限即抛
    :class:`MultipartStructureRejected`——该 part 的内容不会再被转发给
    解析器（r5 评审：21×1.1MiB 已认证 import-batch 曾在文件数校验前全部
    spool；r6 复审 #38：50×1MiB 字段 part 曾在 422 前整体进内存）。

    - part 头部块（到空行 ``\\r\\n\\r\\n`` 为止）含 ``filename`` → 文件
      part：第 ``max_files + 1`` 个的头部出现即拒绝；
    - 其余为字段 part：数量超 ``max_fields``、或单字段字节超
      ``max_field_bytes``（字段 part 的 body 在端点校验前整体驻留内存，
      必须有界）即拒绝；
    - 头部块跨 chunk 累计超过 ``_MAX_HEADER_BYTES`` 仍未见空行：无法
      分类，按「**可能是文件 part**」保守计 1 个文件 part 再重扫下一个
      分隔符（r6 复审 #44：此前直接放弃分类，4.2KB 头部的文件 part 可
      漏计落盘）。观察窗取 python-multipart 单行头上限（4096+128）的
      两倍，正常客户端（浏览器/requests/curl）的 part 头远小于此。

    判定依据与 Starlette 一致：formparsers.py 按头块里的 ``filename``
    键区分文件/字段。已知边界：名字恰为 "filename" 的**表单字段**也会被
    计入文件——本产品各端点的表单字段名均不含该词，误报只可能来自
    攻击者自伤。

    首边界的识别（评审二轮实测绕过）：RFC 7578 首边界**无前导 CRLF**，
    且可能跨任意 chunk 拆分——此前只在首个 feed 块以完整 ``--boundary``
    开头时识别，首块 ``'-'``、boundary 前缀或逐字节滴灌时第一个 part
    完全不被计数（2 个文件 part、max=1 放行）。现改为：见到首个 part
    之前持续查找 ``--boundary``（缓冲跨 chunk 拼接，lead 之前按
    preamble 跳过，与 python-multipart 行为一致），之后的边界仍按
    ``\\r\\n--boundary`` 匹配。
    """

    _SCAN = 0
    _HEADERS = 1
    _DONE = 2
    #: part 头部块的最大观察长度（超过按「可能是文件 part」保守计数）
    _MAX_HEADER_BYTES = 8192

    def __init__(self, boundary: bytes, max_files: int,
                 max_fields: int = MAX_FIELD_PARTS,
                 max_field_bytes: int = MAX_FIELD_PART_BYTES) -> None:
        self._delim = b"\r\n--" + boundary
        self._lead = b"--" + boundary
        self._max_files = max_files
        self._max_fields = max_fields
        self._max_field_bytes = max_field_bytes
        self.files = 0
        self.fields = 0
        self._field_bytes = 0
        self._state = self._SCAN
        self._buf = b""        # 尚未判定的观察窗口（字节本身仍原样转发）
        self._part_seen = False    # 是否已见到首个 part 的开始边界
        self._in_file_part = False  # 当前 part 是否按文件 part 计数
        self._part_active = False   # 是否已进入某个 part（preamble 不计数）

    def _bump_file(self) -> None:
        self.files += 1
        self._in_file_part = True
        self._part_active = True
        self._field_bytes = 0
        if self.files > self._max_files:
            raise MultipartStructureRejected(
                f"上传文件数超过该端点允许的上限（{_fmt_count(self._max_files)} 个）。"
            )

    def _bump_field(self) -> None:
        self.fields += 1
        self._in_file_part = False
        self._part_active = True
        self._field_bytes = 0
        if self.fields > self._max_fields:
            raise MultipartStructureRejected(
                f"上传表单字段数超过上限（{_fmt_count(self._max_fields)} 个）。"
            )

    def _account_field_bytes(self, count: int) -> None:
        """当前 part 是字段 part 时累计其 body 字节并检查上限。"""
        if count <= 0 or not self._part_active or self._in_file_part:
            return
        self._field_bytes += count
        if self._field_bytes > self._max_field_bytes:
            raise MultipartStructureRejected(
                "单个表单字段超过大小上限（1 MB），请改用文件上传。"
            )

    def feed(self, chunk: bytes) -> None:
        data = self._buf + chunk
        self._buf = b""
        pos = 0
        while self._state != self._DONE:
            if not self._part_seen:
                # 首个 part 开始前：查找无前导 CRLF 的首边界 --boundary
                # （跨 chunk 拼接、前导 preamble 均可识别）。未找到则保留
                # 尾部窗口等下一个 chunk 补全，其余按 preamble 丢弃。
                idx = data.find(self._lead, pos)
                if idx < 0:
                    keep = len(self._lead) - 1
                    if len(data) - pos > keep:
                        self._buf = data[-keep:]
                    else:
                        self._buf = data[pos:]
                    return
                pos = idx + len(self._lead)
                if data[pos:pos + 2] == b"--":  # 空 multipart：--boundary--
                    self._state = self._DONE
                    return
                self._part_seen = True
                self._state = self._HEADERS
                continue
            if self._state == self._SCAN:
                idx = data.find(self._delim, pos)
                if idx < 0:
                    keep = len(self._delim) - 1  # 跨 chunk 的分隔符不漏判
                    if len(data) - pos > keep:
                        self._account_field_bytes(len(data) - pos - keep)
                        self._buf = data[-keep:]
                    else:
                        self._buf = data[pos:]
                    return
                self._account_field_bytes(idx - pos)
                after = idx + len(self._delim)
                if data[after:after + 2] == b"--":  # 结束边界 --boundary--
                    self._state = self._DONE
                    return
                pos = after
                self._state = self._HEADERS
                continue
            # _HEADERS：累积 part 头到空行为止，判定文件/字段 part
            end = data.find(b"\r\n\r\n", pos)
            if end < 0:
                tail = data[pos:]
                if len(tail) > self._MAX_HEADER_BYTES:
                    # 异常超长头：无法分类，按「可能是文件 part」保守计数，
                    # 再从当前位置起重扫下一个分隔符（已观察字节不丢弃）。
                    self._bump_file()
                    self._state = self._SCAN
                    continue
                self._buf = tail  # 头部未完整，等下一个 chunk 补全
                return
            if b"filename" in data[pos:end].lower():
                self._bump_file()
            else:
                self._bump_field()
            pos = end + 4
            self._state = self._SCAN


#: 兼容别名（r5 测试引用名）：计数器已扩展为文件 + 字段双口径。
_MultipartFilePartCounter = _MultipartPartCounter


def _fmt_count(limit: int) -> str:
    return "1" if limit == 1 else f"1–{limit}"


class MultipartStructureRejected(UploadRejected):
    """multipart 结构超限（文件 part 数超端点上限），映射 413。

    在解析器读取多余 part **内容之前**由 receive 层抛出；经 ExceptionMiddleware
    按既有 ``UploadRejected`` 处理器映射（子类按 MRO 匹配），不会落 500。
    """

    def __init__(self, message: str) -> None:
        super().__init__(message, status=413)


class UploadSizeLimitMiddleware:
    """multipart 上传的 ASGI 级预检：**解析请求体之前**拒绝异常请求。

    背景：FastAPI/Starlette 在进入端点/依赖前已完整解析 multipart，>1MB 的
    文件 part 会 spool 到系统临时目录——端点内的 ``check_size``/文件数校验
    永远发生在落盘之后（G3/U5-P3 同一根因）。本中间件在解析发生前依次检查
    （顺序：400 重复头 → 411 → 401 → 400 boundary 缺失 → 413 字节上限），
    放行后仍在 receive 层流式限制文件 part 数与字段 part 数/大小：

    0. **Content-Type / Content-Length 头重复 → 400**：同名头取值以先到者
       为准（与 Starlette Headers.get 前值语义一致）。评审实测：双
       Content-Type（前 multipart 后 json）可让本中间件按后值放行、而
       Starlette 按前值照常解析，绕过下面全部预检——重复的这两个头只会
       来自异常客户端，一律 400；
    1. **无/非法 Content-Length 的 multipart → 411**：浏览器与 fetch 发送
       multipart 表单必然携带 Content-Length；
    2. **无有效 Bearer 凭据 → 401**（``token_validator`` 注入时启用）：全部
       上传端点都要求登录，未认证请求不解析、不 spool、不触任务锁；
    3. **boundary 缺失/不可解析 → 400**（R6-S3）：multipart 的 boundary
       用与 Starlette **同源**的 ``parse_options_header`` 解析（python-
       multipart，重复参数取最后一个、引号去引号、值 strip）。解析不出
       boundary 的 multipart 本就会被 Starlette 以 400 拒绝（MultiPart
       Exception "Missing boundary"），这里只是提前到落盘之前；同时修掉
       r5 自写提取器取**第一个** boundary 与 Starlette 取**最后一个**的
       分歧（`boundary=abc; boundary=def` 曾致计数器 fail-open）；
    4. **Content-Length 超过路径上限 → 413**：``rules`` =
       ``[(路径正则, 最大请求体字节数, 最大文件 part 数), ...]``，上限取
       各端点**真实限制 + multipart 开销余量**；
    5. **文件 part 数 / 字段 part 数 / 单字段大小超限 → 413**（receive 层
       流式计数，见 :class:`_MultipartPartCounter`）：超限 part 的内容
       不再转发给解析器，已认证请求的落盘量与字段内存同样有界。

    只拦 ``multipart/form-data`` 的 POST/PUT（JSON 端点不受影响）。
    早拒响应统一带安全响应头，Origin 命中 ``cors_origins`` 白名单时补
    CORS 简单响应头（R6-S5：早拒发生在 SecurityHeaders/CORS 之外）。
    """

    def __init__(self, app, rules: list, token_validator=None,
                 cors_origins=None):  # noqa: ANN001
        self.app = app
        self.rules = rules
        self.token_validator = token_validator
        self.cors_origins = list(cors_origins) if cors_origins else []

    @staticmethod
    def _bearer_token(scope) -> str | None:  # noqa: ANN001
        """从 ASGI scope 提取 Bearer 令牌（首个 Authorization 头，同 Starlette）。"""
        for key, value in scope.get("headers") or []:
            if key.lower() == b"authorization":
                try:
                    authorization = value.decode("latin-1")
                except (UnicodeDecodeError, AttributeError):
                    return None
                if not authorization.lower().startswith("bearer "):
                    return None
                token = authorization[7:].strip()
                return token or None
        return None

    @staticmethod
    def _origin_of(scope) -> str | None:  # noqa: ANN001
        for key, value in scope.get("headers") or []:
            if key.lower() == b"origin":
                try:
                    return value.decode("ascii")
                except (UnicodeDecodeError, AttributeError):
                    return None
        return None

    @staticmethod
    def _extract_boundary(content_type: bytes) -> bytes | None:
        """降级用的 boundary 提取（python-multipart 不可用时；正常不走）。"""
        for part in content_type.split(b";"):
            key, _, value = part.partition(b"=")
            if key.strip().lower() == b"boundary":
                return value.strip().strip(b'"') or None
        return None

    async def _reject(self, scope, send, status: int, detail: str,
                      extra_headers: list = None) -> None:  # noqa: ANN001
        body = json.dumps({"detail": detail}, ensure_ascii=False).encode("utf-8")
        headers = [(b"content-type", b"application/json"),
                   (b"content-length", str(len(body)).encode("ascii")),
                   (b"connection", b"close")]
        headers.extend(extra_headers or [])
        # R6-S5：早拒响应与正常响应同一套安全头；跨域客户端还要能读到错误
        headers = apply_security_headers(headers)
        headers.extend(cors_response_headers(self._origin_of(scope), self.cors_origins))
        # 未读请求体即应答：要求关闭连接，避免服务端继续吸收数据。
        await send({"type": "http.response.start", "status": status,
                    "headers": headers})
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope, receive, send):  # noqa: ANN001
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        method = scope.get("method", "")
        if method not in ("POST", "PUT"):
            await self.app(scope, receive, send)
            return
        content_types: list[bytes] = []
        content_lengths: list[bytes] = []
        for key, value in scope.get("headers") or []:
            lowered = key.lower()
            if lowered == b"content-type":
                content_types.append(value)  # 原值保留：boundary 大小写敏感
            elif lowered == b"content-length":
                content_lengths.append(value)
        if len(content_types) > 1 or len(content_lengths) > 1:
            # 双 Content-Type（前 multipart 后 json）可让预检按后值放行、
            # Starlette 按前值照常解析（评审实测绕过）；重复头一律拒绝。
            await self._reject(scope, send, 400,
                               "请求头结构异常（Content-Type/Content-Length 重复）。")
            return
        content_type_header = content_types[0] if content_types else b""
        # R6-S3：Content-Type 的解析与 Starlette 同源（python-multipart
        # parse_options_header：值 strip/小写化、重复参数取最后一个、引号
        # 去引号）——此前用原值 startswith 判定，前导空白的 Content-Type
        # 在 ASGI 直调层可绕过预检（r6 复审 #43）。
        if _parse_options_header is not None:
            ctype, params = _parse_options_header(content_type_header)
            if ctype != b"multipart/form-data":
                await self.app(scope, receive, send)
                return
            boundary = params.get(b"boundary") or None
        else:  # pragma: no cover - python-multipart 缺失时的降级路径
            if not content_type_header.lower().lstrip().startswith(b"multipart/form-data"):
                await self.app(scope, receive, send)
                return
            ctype, boundary = b"multipart/form-data", self._extract_boundary(content_type_header)
        raw_length = content_lengths[0] if content_lengths else None
        try:
            content_length = int(raw_length) if raw_length is not None else None
        except ValueError:
            content_length = None
        if content_length is None:
            await self._reject(scope, send, 411,
                               "上传请求缺少有效的 Content-Length，无法预检大小。")
            return
        if self.token_validator is not None:
            token = self._bearer_token(scope)
            if token is None or not self.token_validator(token):
                await self._reject(
                    scope, send, 401, "未登录或缺少访问令牌。",
                    extra_headers=[(b"www-authenticate", b"Bearer")])
                return
        path = scope.get("path", "")
        rule = next(((pattern, limit, max_files) for pattern, limit, max_files in self.rules
                     if pattern.match(path)), None)
        if rule is not None:
            _, limit, max_files = rule
            if boundary is None:
                # Starlette 对无 boundary 的 multipart 本就 400（Missing
                # boundary），这里提前到解析/落盘之前（r6 复审 #37 的
                # fail-open 缺口：解析器装不上计数器时请求曾原样放行）。
                await self._reject(scope, send, 400,
                                   "multipart 请求缺少有效的 boundary，无法解析。")
                return
            if content_length > limit:
                await self._reject(scope, send, 413,
                                   "上传请求体超过大小上限，请在压缩或减少文件数量后重试。")
                return
            if max_files >= 1:
                counter = _MultipartPartCounter(boundary, max_files)

                async def receive_wrapper():
                    message = await receive()
                    if message.get("type") == "http.request" and message.get("body"):
                        counter.feed(message["body"])
                    return message

                await self.app(scope, receive_wrapper, send)
                return
        await self.app(scope, receive, send)


# ---------------------------------------------------------------------------
# SSE 事件流短期票据（U5-P10）
# ---------------------------------------------------------------------------

class EventTicketStore:
    """``/api/events`` 的短期一次性票据（默认 5 分钟）。

    EventSource 无法设置 Authorization 头，旧方案把 24h JWT 放进 URL
    查询参数——完整凭据进入反代/访问日志与浏览器历史。改由已认证客户端
    ``POST /api/events/ticket``（凭 Bearer）换取短时效票据，再用票据连
    接事件流：票据一次性（连接时消费）、过期即作废，泄露面从 24h 降到
    5 分钟且不可重放。

    容量有界（``max_active``）：签发与消费时都清理过期票据，达到上限时
    逐出最早过期者——反复签发不能无限撑大内存（评审低危残余的收口）。
    """

    def __init__(self, ttl_seconds: float = 300.0,
                 max_active: int = 10_000) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if max_active < 1:
            raise ValueError("max_active must be positive")
        self.ttl = ttl_seconds
        self.max_active = max_active
        self._tickets: dict[str, tuple[float, dict]] = {}
        self._lock = threading.Lock()

    def issue(self, user_id: int, username: str,
              now: float | None = None) -> tuple[str, int]:
        """签发一张票据，返回 ``(ticket, expires_in_seconds)``。"""
        moment = time.monotonic() if now is None else now
        ticket = secrets.token_urlsafe(32)
        with self._lock:
            self._cleanup(moment)
            if len(self._tickets) >= self.max_active:
                oldest = min(self._tickets, key=lambda key: self._tickets[key][0])
                del self._tickets[oldest]
            self._tickets[ticket] = (
                moment + self.ttl, {"user_id": int(user_id), "username": str(username)}
            )
        return ticket, int(self.ttl)

    def consume(self, ticket: str,
                now: float | None = None) -> dict | None:
        """一次性消费票据；无效/已用/过期返回 None（顺带清理过期票据）。"""
        moment = time.monotonic() if now is None else now
        if not ticket:
            return None
        with self._lock:
            self._cleanup(moment)
            entry = self._tickets.pop(ticket, None)
            if entry is None:
                return None
            expires_at, payload = entry
            if expires_at <= moment:
                return None
            return dict(payload)

    def _cleanup(self, moment: float) -> None:
        if len(self._tickets) < 512:
            return  # 小表不做全量扫描（消费路径也走这里）
        self._tickets = {
            key: entry for key, entry in self._tickets.items()
            if entry[0] > moment
        }
