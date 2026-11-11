"""决策模型（Jev 类）适配器：state + 类型化问题 + 候选项 → 结构化结果。

2026-11-10 用户裁定：Jev 决策模型提前到 2.1.5，**只用于**导入复核 AI 助手的
重复判断（``duplicate_group``）与合并字段冲突（``merge_conflict``）——绝不
进入筛选/编码/其他 AI 功能（越界由 ``ai_suggest_api`` 硬闸拒绝）。

API 形状（2026-11-10 官方文档核实，HTML/MD 存证 ``E:/rf214tmp/jev/``）：

- 来源：``https://docs.typesafe.ai/api/system-one.md``（内嵌 OpenAPI 3.1 全量
  规范；``https://api.typesafe.ai/openapi.json`` 直连在本网络返回 451 地域
  阻断，文档页为同源官方发布）。交叉验证：Cloudflare Clef（Jev 兼容生态）
  文档 ``https://developers.cloudflare.com/workers-ai/models/clef/`` 使用同一
  请求/响应词汇（state/questions/criteria/choice/probabilities/confidence）。
- 请求 ``POST {base_url}/v1/systemone``（Authorization: Bearer，纯文本 JSON）::

      {"model": "jev-latest",
       "state": <string | object | array>,
       "questions": {"<名字>": {"type": "choice",
                                 "instructions": <string | object | array>,
                                 "criteria": {"<选项名>": <string | object | array>}}}}

  Choice 问题的 ``criteria`` 至少 1 项（上限 255），值是"该选项何时适用"的
  描述；一个请求可混排多个问题，响应按同名字回填。
- 响应::

      {"model": "<实际模型名>",
       "answers": {"<名字>": {"type": "choice",
                              "choice": "<选中选项名>",
                              "confidence": <0~1>,
                              "probabilities": {"<选项名>": <0~1>}}},
       "usage": {"input_tokens": <int>, "output_tokens": <int>}}

  ``confidence`` 由概率分布推导（文档 /confidence：全概率集中在一项=1，均匀
  分摊=0）；``probabilities`` 是各选项的校准概率（近似和为 1）。
- 图片（Box/Assets）与长上下文需要显式 ``TypeSafe-Danger-Zone-*`` 请求头，
  本适配器是纯文本通道，一概不发。
- HTTP 错误码：400 请求规则/超上下文、401 钥匙、413 载荷超限、422 校验、
  429/529 限流/过载（可退避重试）。

统一输出形状（与 LLM 适配器 ``parse_reply`` / ``parse_merge_reply`` 一致，
下游审核表、unsure 回落、审计、出站六字段白名单全部不变）：

- duplicate_group → ``{verdict, confidence, rationale_zh, field_picks=None,
  warnings, degraded}``；Jev 候选项 ``duplicate``/``include``/``unsure`` 映射
  统一 verdict ``same``/``different``/``unsure``。
- merge_conflict → ``{picks, confidence, rationale_zh, warnings, degraded}``，
  每个冲突字段一个 Choice 问题（候选项 ``left``/``right``/``unsure``）；
  整体置信度 = 各字段映射置信度的**最小值**（任一字段低置信 → 整个 AI 层
  降级 unsure，与 LLM 通道"整体置信度"语义对齐）。

概率 → confidence 映射：``probabilities[选中项]``（选中候选的校准概率）与
答案的 ``confidence`` 字段（提供方由概率分布推导的置信统计量）**都可用时
取小**——官方 ``/confidence`` 表明 confidence 系统性低于选中项概率，取小
保证映射绝不比提供方自身校准信号更宽松（低置信更易回落 unsure）；只有一方
可用时用该方；两者都缺/非法 → ``confidence_missing`` 降级 unsure。阈值沿用
可配置默认 ``0.6``（**阈值待用历史已确认样本校准**——校准需用户服务器上的
历史已确认重复/非重复数据，本波不做）。

降级纪律与 LLM 通道相同：非法返回/低置信/缺置信/出站失败 → ``unsure`` /
回落保留条目值，绝不硬给；决策模型不产出理由文本，``rationale_zh`` 用模板串
（如"决策模型判定，置信 0.87"）。

出站治理复用 ``ai_suggest_api`` 既有模式：短超时由调用方传入（导入复核
10s）、响应读取上限 2 MiB、拒重定向、显式空 ProxyHandler、BYOK 钥匙柜、
错误消息绝不携带钥匙或载荷明文。
"""
from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.request
from typing import Any

from custom_backend import ai_keys
from custom_backend.ai_keys import DECISION_DEFAULT_BASE_URL  # noqa: F401  单一事实源在 ai_keys
from custom_backend.ai_suggest_api import (
    MAX_PROVIDER_BYTES,
    PROVIDER_RETRIES,
    ProviderError,
    _OPENER,
    _OUTBOUND_SEMAPHORE,
    _ProviderBusyError,
)

__all__ = [
    "DECISION_DEFAULT_BASE_URL",
    "DECISION_MERGE_PROMPT_VERSION",
    "DECISION_PROMPT_VERSION",
    "DEFAULT_DECISION_MODEL",
    "call_decision",
    "connectivity_body",
    "decision_base_url_of",
    "duplicate_request",
    "merge_request",
    "parse_duplicate_answer",
    "parse_merge_answers",
    "post_decision",
    "systemone_url",
]

#: 决策模型钥匙缺省 base_url：官方 hosted Jev 端点（用户可改填自部署
#: JEV-9B/Kev 或任何 Jev 兼容端点）。常量单一事实源在 ``ai_keys``（保存/env
#: 缺省按 kind 落盘都在那里），本模块 re-export 供就近引用。

#: 钥匙未填 model 时的兜底（官方文档：模型名/别名见 GET /v1/models）。
DEFAULT_DECISION_MODEL = "jev-latest"

#: 决策模型两场景的提示词版本（审计行独立于 LLM 版本，互不漂移）。
DECISION_PROMPT_VERSION = "import-ai-assist-decision/v1"
DECISION_MERGE_PROMPT_VERSION = "import-ai-assist-merge-decision/v1"

USER_AGENT = "ReviewFlow-Decision-Model/2.1.5"

_SYSTEMONE_PATH = "/v1/systemone"

#: duplicate_group 的候选项 → 统一 verdict（LLM 通道的 same/different/unsure）。
_DUPLICATE_VERDICTS = {"duplicate": "same", "include": "different", "unsure": "unsure"}

#: 置信度阈值缺省（**待用历史已确认样本校准**；调用方传任务级口径）。
DEFAULT_MIN_CONFIDENCE = 0.6

_DUPLICATE_INSTRUCTIONS = (
    "只依据 state 中给出的题录字段（题名/作者/年份/期刊/DOI/摘要）判断这些题录"
    "是否指向同一篇文献，不得使用题录之外的信息；证据不足时必须选 unsure。")
_DUPLICATE_CRITERIA = {
    "duplicate": "这些题录指向同一篇文献，应按重复合并处理。",
    "include": "这些题录不是同一篇文献，应各自保留。",
    "unsure": "题录证据不足，无法判断是否同一篇文献。",
}
_MERGE_INSTRUCTIONS = (
    "字段 {field}：left 是保留条目的值，right 是另一条记录的值。按优先级选择更好"
    "的一侧：信息更完整（未截断、作者全列、期刊全称、完整摘要）优先；信息量相当"
    "时格式更规范（正常大小写、无乱码、DOI 标准形式）优先。两侧都完整规范但实质"
    "内容不同（如两个不同的合法年份、内容不同的摘要）、或无法确定哪一侧更好时，"
    "必须选 unsure。不得改写、合并或补写任何值。")
_MERGE_CRITERIA = {
    "left": "采用保留条目（left）的值。",
    "right": "采用另一条记录（right）的值。",
    "unsure": "无法判断哪一侧更好。",
}


# ---------------------------------------------------------------------------
# base_url 解析（纯函数）
# ---------------------------------------------------------------------------

def decision_base_url_of(record: dict | None) -> str:
    """决策模型钥匙的规范 base_url：显式配置优先（沿用 ``ai_keys`` 校验），
    缺省回官方 hosted Jev 端点（llm 钥匙的 OpenAI 缺省不适用于决策模型）。"""
    raw = str((record or {}).get("base_url") or "").strip()
    if not raw:
        return DECISION_DEFAULT_BASE_URL
    return ai_keys.base_url_of({"base_url": raw})


def systemone_url(base_url: str) -> str:
    """``{base}/v1/systemone``；用户按 OpenAI 习惯填了尾部 ``/v1`` 时自动剥离
    （避免 ``/v1/v1/systemone`` 的常见错配；其余路径原样保留）。"""
    trimmed = str(base_url or "").rstrip("/")
    if trimmed.lower().endswith("/v1"):
        trimmed = trimmed[:-3]
    return trimmed + _SYSTEMONE_PATH


# ---------------------------------------------------------------------------
# 请求构造（纯函数；出站载荷与 LLM 通道同源：六字段白名单题录）
# ---------------------------------------------------------------------------

def duplicate_request(payload: dict, *, model: str) -> dict:
    """duplicate_group 的 systemone 请求体。

    ``payload`` 即 ``load_item_payload`` 的 ``{"kind","members"}`` 载荷
    （成员只含出站白名单字段）；候选项 include/duplicate/unsure + 概率。
    """
    return {
        "model": model,
        "state": payload,
        "questions": {
            "duplicate_verdict": {
                "type": "choice",
                "instructions": _DUPLICATE_INSTRUCTIONS,
                "criteria": dict(_DUPLICATE_CRITERIA),
            },
        },
    }


def merge_request(fields: list[dict], *, model: str) -> dict:
    """merge_conflict 的 systemone 请求体：每个出站冲突字段一个 Choice 问题。

    ``fields`` = ``_merge_ai_fields`` 的行（只含规则层判不出的六字段白名单
    冲突）；state 与 LLM 通道的出站消息同形（``{"kind","conflicts"}``，每项
    仅 ``field/left/right`` 三键）。
    """
    conflicts = [{"field": row["field"], "left": row["left"], "right": row["right"]}
                 for row in fields]
    questions = {}
    for row in fields:
        field = row["field"]
        questions[field] = {
            "type": "choice",
            "instructions": _MERGE_INSTRUCTIONS.format(field=field),
            "criteria": dict(_MERGE_CRITERIA),
        }
    return {"model": model,
            "state": {"kind": "merge_conflict", "conflicts": conflicts},
            "questions": questions}


def connectivity_body(*, model: str) -> dict:
    """钥匙测试/保存前校验用的最小 systemone 请求（纯文本，无 Danger-Zone 头）。"""
    return {
        "model": model,
        "state": "connectivity check",
        "questions": {"ping": {
            "type": "choice",
            "instructions": "Is this request well-formed?",
            "criteria": {"ok": "The request is well-formed and answerable.",
                         "fail": "The request is malformed."},
        }},
    }


# ---------------------------------------------------------------------------
# 出站（治理口径与 ai_suggest_api._provider_generate 一致；测试注入 fake
# 替换 ``post_decision``）
# ---------------------------------------------------------------------------

def post_decision(body: dict, *, api_key: str, base_url: str, model: str,
                  timeout: float) -> dict:
    """POST ``/v1/systemone``；返回解析后的响应 dict（至少含 ``model``/``answers``）。

    日志红线同 LLM 通道：错误消息绝不携带 api_key 或请求载荷；拒重定向
    （30x 视为错误）；响应体读取上限 2 MiB；非 2xx 只回状态码不回显响应体。
    sec29b W4④：decision 出站与 LLM 通道共用**同一把** ``_OUTBOUND_SEMAPHORE``
    并发闸（``ai_suggest_api`` 模块级信号量，同一对象——llm 与 decision 两路
    出站合计不超过 8 并发；超限快速失败 ``_ProviderBusyError`` 503，不排队）。
    钥匙测试 / verify=true kind=decision / 导入复核 duplicate/merge 所有出站
    路径都经本函数，闸在最底层全覆盖。
    """
    if not _OUTBOUND_SEMAPHORE.acquire(blocking=False):
        raise _ProviderBusyError() from None
    try:
        return _post_decision_locked(body, api_key=api_key, base_url=base_url,
                                     model=model, timeout=timeout)
    finally:
        _OUTBOUND_SEMAPHORE.release()


def _post_decision_locked(body: dict, *, api_key: str, base_url: str, model: str,
                          timeout: float) -> dict:
    """``post_decision`` 的原实现（已持并发闸；测试注入 fake 替换 ``post_decision``）。"""
    url = systemone_url(base_url)
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(  # noqa: S310  base_url 经用户配置并限 http(s)
        url, data=payload, method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        })
    try:
        with _OPENER.open(request, timeout=timeout) as response:  # noqa: S310
            raw = response.read(MAX_PROVIDER_BYTES + 1)
            if len(raw) > MAX_PROVIDER_BYTES:
                raise ProviderError(
                    f"决策模型响应超过 {MAX_PROVIDER_BYTES // 1024} KiB 读取上限。")
            parsed = json.loads(raw.decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        if 300 <= exc.code < 400:
            raise ProviderError(
                f"决策模型端点 HTTP {exc.code}: 重定向被拒绝（为保护 API 钥匙不外发，"
                "请直接配置最终端点）。") from None
        # 非 2xx 只回状态码（与 MM-06 同口径：不回显响应体，避免 SSRF 读预言机）。
        raise ProviderError(f"决策模型端点 HTTP {exc.code}（详情不回显）",
                            status=exc.code) from None
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", None)
        text = f"决策模型端点连接失败: {reason}" if reason else "决策模型端点连接失败"
        raise ProviderError(text) from None
    except http.client.HTTPException as exc:  # InvalidURL 等 URL 级错误绝不冒泡 500
        raise ProviderError(f"决策模型端点 URL 非法: {type(exc).__name__}") from None
    except (TimeoutError, OSError) as exc:
        raise ProviderError(
            f"决策模型端点超时（{int(timeout)}s）: {type(exc).__name__}") from None
    except ValueError as exc:
        raise ProviderError("决策模型响应不是合法 JSON。") from None
    if not isinstance(parsed, dict):
        raise ProviderError("决策模型响应不是 JSON 对象。")
    return parsed


def call_decision(body: dict, *, api_key: str, base_url: str, model: str,
                  timeout: float) -> dict:
    """带重试（瞬时错误重试 1 次，与 LLM 通道同一 ``PROVIDER_RETRIES`` 口径）
    与脱敏兜底的包装；测试替换 ``post_decision`` 注入 fake。

    sec29b W4④：并发闸超限（``_ProviderBusyError``）不重试——与
    ``ai_suggest_api._call_provider`` 同判（重试只会把闸位占得更久）。"""
    last: ProviderError | None = None
    for attempt in range(PROVIDER_RETRIES):
        try:
            return post_decision(body, api_key=api_key, base_url=base_url,
                                 model=model, timeout=timeout)
        except _ProviderBusyError:
            raise          # sec29b W4④：并发满不重试（同 _call_provider 口径）
        except ProviderError as exc:
            last = exc
            transient = exc.status is None or exc.status >= 500 or exc.status == 429
            # 与 ai_suggest_api._call_provider 同判：非瞬时错误立即停；
            # 瞬时错误重试到最后一档（此前 `attempt == 0` 写反导致重试永远
            # 不发生——评审 BLOCKER 修复）。
            if not transient or attempt == PROVIDER_RETRIES - 1:
                break
    message = str(last or "决策模型调用失败")
    if api_key and api_key in message:  # 双保险：错误消息绝不携带明文
        message = message.replace(api_key, "***")
    raise ProviderError(message, status=last.status if last else None)


# ---------------------------------------------------------------------------
# 响应解析（概率→confidence 映射 + 低置信回落；纯函数）
# ---------------------------------------------------------------------------

def _mapped_confidence(answer: dict, choice: str) -> float | None:
    """概率→confidence 映射（**两个信号都可用时取小**，更保守）。

    - ``probabilities[选中项]`` = 选中候选的校准概率；
    - 答案的 ``confidence`` = 提供方由概率分布推导的置信统计量（官方
      ``/confidence``：全概率集中一项=1、均匀分摊=0；三选项 Choice 为
      ``(3×最大概率−1)/2``，系统性低于选中项概率）；
    - 取小 = 绝不比提供方自身校准信号更宽松（例如选中概率 0.65 而 Jev
      confidence≈0.475 时按 0.475 判、低于阈值即回落 unsure——评审观察采纳）；
    - 只有一方可用时用该方；两方都缺/非法（NaN/Inf/bool/非数）→ None
      （一律按缺置信降级，绝不硬给）。
    """
    probabilities = answer.get("probabilities")
    candidates: list[float] = []
    if isinstance(probabilities, dict):
        value = probabilities.get(choice)
        if value is not None and not isinstance(value, bool):
            candidates.append(_as_confidence(value))
    fallback = answer.get("confidence")
    if fallback is not None and not isinstance(fallback, bool):
        candidates.append(_as_confidence(fallback))
    usable = [c for c in candidates if c is not None]
    return min(usable) if usable else None


def _as_confidence(value: Any) -> float | None:
    """合法置信度归一（[0,1]）；非法（NaN/Inf/非数）→ None。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return min(1.0, max(0.0, number))


def _unsure_shape(rationale: str, warnings: list[str]) -> dict:
    return {"verdict": "unsure", "confidence": None, "rationale_zh": rationale,
            "field_picks": None, "warnings": list(warnings), "degraded": True}


def parse_duplicate_answer(body: Any, *,
                           min_confidence: float = DEFAULT_MIN_CONFIDENCE) -> dict:
    """解析 duplicate_group 的 systemone 响应 → ``parse_reply`` 同形 dict。

    非法返回/缺置信/低置信一律 unsure（绝不硬给 same/different）；决策模型
    无理由文本，``rationale_zh`` 用模板串。
    """
    answers = body.get("answers") if isinstance(body, dict) else None
    answer = answers.get("duplicate_verdict") if isinstance(answers, dict) else None
    if not isinstance(answer, dict):
        return _unsure_shape("决策模型响应无法解析，无法判断。", ["decision_response_invalid"])

    warnings: list[str] = []
    degraded = False
    choice = str(answer.get("choice") or "").strip().lower()
    verdict = _DUPLICATE_VERDICTS.get(choice)
    confidence = _mapped_confidence(answer, choice)
    if verdict is None:
        verdict, degraded = "unsure", True
        warnings.append("invalid_verdict")
    if confidence is None:
        verdict, degraded = "unsure", True
        warnings.append("confidence_missing")
        rationale = "决策模型未给出置信度，无法判断。"
    elif verdict != "unsure" and confidence < min_confidence:
        verdict, degraded = "unsure", True
        warnings.append("low_confidence")
        rationale = (f"决策模型置信度 {confidence:.2f} 低于 {min_confidence:.2f}，"
                     "无法判断。")
    elif verdict == "unsure":
        rationale = f"决策模型判定无法判断，置信 {confidence:.2f}。"
    else:
        rationale = f"决策模型判定，置信 {confidence:.2f}。"
    return {"verdict": verdict, "confidence": confidence, "rationale_zh": rationale,
            "field_picks": None, "warnings": warnings, "degraded": degraded}


def parse_merge_answers(body: Any, asked_fields: list[str], *,
                        min_confidence: float = DEFAULT_MIN_CONFIDENCE) -> dict:
    """解析 merge_conflict 的 systemone 响应 → ``parse_merge_reply`` 同形 dict。

    每个字段一个答案；整体置信度 = 各字段映射置信度的最小值（任一字段缺置信
    → 整体缺置信；任一字段低置信 → 整个 AI 层 unsure）。值不在
    {left,right,unsure} 的答案丢弃按 unsure；未作答字段按 unsure。
    """
    answers = body.get("answers") if isinstance(body, dict) else None
    if not isinstance(answers, dict):
        return {"picks": {}, "confidence": None,
                "rationale_zh": "决策模型响应无法解析，无法判断。",
                "warnings": ["decision_response_invalid"], "degraded": True}

    warnings: list[str] = []
    degraded = False
    picks: dict[str, str] = {}
    confidences: list[float] = []
    for field in asked_fields:
        answer = answers.get(field)
        if not isinstance(answer, dict):
            warnings.append("field_picks_invalid")
            degraded = True
            continue
        token = str(answer.get("choice") or "").strip().lower()
        if token not in ("left", "right", "unsure"):
            warnings.append("field_picks_invalid")
            degraded = True
            continue
        picks[field] = token
        confidences.append(_mapped_confidence(answer, token))
    missing = [field for field in asked_fields if field not in picks]
    if missing:
        warnings.append("field_picks_incomplete")
        degraded = True

    confidence = min(confidences) if confidences and all(
        c is not None for c in confidences) else None
    if confidence is None:
        warnings.append("confidence_missing")
        degraded = True
        picks = {field: "unsure" for field in asked_fields}
        rationale = "决策模型未给出置信度，无法判断。"
    elif confidence < min_confidence:
        warnings.append("low_confidence")
        degraded = True
        picks = {field: "unsure" for field in asked_fields}
        rationale = (f"决策模型置信度 {confidence:.2f} 低于 {min_confidence:.2f}，"
                     "无法判断。")
    else:
        rationale = f"决策模型判定，置信 {confidence:.2f}。"
    return {"picks": picks, "confidence": confidence, "rationale_zh": rationale,
            "warnings": warnings, "degraded": degraded}
