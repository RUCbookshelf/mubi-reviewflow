"""HTTP 安全设施：安全响应头、登录限流、上传校验与路径防护。

- 安全头由纯 ASGI 中间件注入（不经 BaseHTTPMiddleware，避免流式响应陷阱）；
- 登录限流：内存滑动窗口，按客户端 IP 计数（5 次/分钟），超限 429 + Retry-After；
- 上传安全：大小上限、扩展名白名单、文件名清洗（防路径穿越/控制字符）、
  magic bytes 校验（PDF=%PDF-，CSV/RIS=可解码文本且无 NUL 字节）；
- 路径防护：is_relative_to 判定解析后的真实路径不得逃出白名单根目录。
"""

from __future__ import annotations

import base64
import hashlib
import re
import threading
import time
from pathlib import Path

__all__ = [
    "MAX_UPLOAD_BYTES",
    "RateLimiter",
    "SecurityHeadersMiddleware",
    "content_security_policy",
    "inline_script_hashes",
    "clean_upload_name",
    "check_pdf_bytes",
    "check_text_bytes",
    "is_within",
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

        csp = content_security_policy().encode("ascii")

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                have = {k.lower() for k, _ in headers}
                for name, value in self.HEADERS:
                    if name not in have:
                        headers.append((name, value))
                if b"content-security-policy" not in have:
                    headers.append((b"content-security-policy", csp))
                ctype = next((v for k, v in headers if k.lower() == b"content-type"), b"")
                if b"text/html" in ctype.lower() and b"cache-control" not in have:
                    headers.append((b"cache-control", b"no-store, max-age=0"))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_wrapper)


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


# ---------------------------------------------------------------------------
# 上传校验
# ---------------------------------------------------------------------------

def clean_upload_name(filename: str, fallback: str = "upload") -> str:
    """清洗上传文件名：去目录成分、去控制字符、去 ".."、限制长度。

    只保留最终成分（``Path(...).name`` 语义 + 显式剥离分隔符），清洗后为空
    回退 fallback。**返回值只用于展示/派生名称，落盘路径一律由服务端构造。**
    """
    name = (filename or "").replace("\\", "/").split("/")[-1]
    name = _UNSAFE_NAME_RE.sub("", name).strip().strip(".")
    if name in ("", ".", ".."):
        name = fallback
    return name[:180]


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
