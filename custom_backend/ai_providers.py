# -*- coding: utf-8 -*-
"""AI 提供商目录（aiux ②·2026-11-12）：内置静态数据，供配置向导自动匹配。

数据口径：
- 本模块是**纯数据**（零 I/O、零副作用，任何层可安全 import——照抄
  coscreen/measure_registry.py 的冻结契约模式）；目录经
  ``GET /api/ai/providers`` 下发快照，前端取一次缓存。
- base_url 均为各厂商**公开文档**所载 OpenAI 兼容/原生端点（核实于
  2026-11-12：MiniMax 国际=api.minimax.io/v1、OpenCode-GO=opencode.ai/
  zen/go/v1 经公开文档复核；CommandCode-GOAT=本项目 Jev 冒烟所用端点）。
  全部为公网主机，必须能通过 ai_keys 的 SSRF 闸（测试有静态复核钉）。
- ``format`` 取值四选：openai_chat（POST {base}/chat/completions）/
  openai_responses（POST {base}/responses）/anthropic（POST {base}/v1/
  messages，x-api-key 头）/systemone（decision 专用，{base}/v1/systemone）。
  目录条目可携带后端尚未支持的格式（向前兼容），端点同时下发
  ``supported_formats`` 声明，前端只放行 厂商∩后端 支持的组合。
- models 仅为**建议清单**（可编辑字段预填），不构成承诺；排名口径
  （Artificial Analysis 前 100 模型官方厂商）为收录依据之一，不落库
  排名数据本身。
- 红线：本目录不含任何凭据；新增条目必须公网、http(s)、过 SSRF 闸。
"""

from __future__ import annotations

#: 目录数据版本（发布线刷新；前端缓存键）。
CATALOG_VERSION = "20261112.2"

#: wire 格式常量（与 ai_keys.KEY_FORMATS 同名同值；此处独立定义避免反向依赖）。
FORMAT_OPENAI_CHAT = "openai_chat"
FORMAT_OPENAI_RESPONSES = "openai_responses"
FORMAT_ANTHROPIC = "anthropic"
FORMAT_SYSTEMONE = "systemone"

#: 本目录支持的 type 取值。
TYPE_OFFICIAL = "official"
TYPE_RELAY = "relay"

#: 条目冻结契约（README 口径同 measure_registry：字段只增不删，删改须升版本）：
#: {"id": str 唯一, "type": official|relay, "name_key": 'ai_providers.<id>',
#:  "kinds": ["llm"]|["decision"], "formats": [{"format": …, "base_url": …,
#:  "models": [str, …]}]}
_CATALOG: tuple[dict, ...] = (
    {
        "id": "openai",
        "type": TYPE_OFFICIAL,
        "name_key": "ai_providers.openai",
        "kinds": ["llm"],
        "formats": [
            {"format": FORMAT_OPENAI_CHAT, "base_url": "https://api.openai.com/v1",
             "models": ["gpt-4.1-mini", "gpt-4o-mini"]},
            {"format": FORMAT_OPENAI_RESPONSES, "base_url": "https://api.openai.com/v1",
             "models": ["gpt-4.1-mini", "gpt-4o-mini"]},
        ],
    },
    {
        "id": "anthropic",
        "type": TYPE_OFFICIAL,
        "name_key": "ai_providers.anthropic",
        "kinds": ["llm"],
        "formats": [
            {"format": FORMAT_ANTHROPIC, "base_url": "https://api.anthropic.com",
             "models": ["claude-sonnet-4-5", "claude-haiku-4-5"]},
            {"format": FORMAT_OPENAI_CHAT, "base_url": "https://api.anthropic.com/v1",
             "models": ["claude-sonnet-4-5", "claude-haiku-4-5"]},
        ],
    },
    {
        "id": "google",
        "type": TYPE_OFFICIAL,
        "name_key": "ai_providers.google",
        "kinds": ["llm"],
        "formats": [
            {"format": FORMAT_OPENAI_CHAT,
             "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
             "models": ["gemini-2.5-flash", "gemini-2.5-pro"]},
        ],
    },
    {
        "id": "deepseek",
        "type": TYPE_OFFICIAL,
        "name_key": "ai_providers.deepseek",
        "kinds": ["llm"],
        "formats": [
            {"format": FORMAT_OPENAI_CHAT, "base_url": "https://api.deepseek.com",
             "models": ["deepseek-chat", "deepseek-reasoner"]},
        ],
    },
    {
        "id": "qwen",
        "type": TYPE_OFFICIAL,
        "name_key": "ai_providers.qwen",
        "kinds": ["llm"],
        "formats": [
            {"format": FORMAT_OPENAI_CHAT,
             "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
             "models": ["qwen-plus", "qwen-max", "qwen-flash"]},
        ],
    },
    {
        "id": "zhipu",
        "type": TYPE_OFFICIAL,
        "name_key": "ai_providers.zhipu",
        "kinds": ["llm"],
        "formats": [
            {"format": FORMAT_OPENAI_CHAT, "base_url": "https://open.bigmodel.cn/api/paas/v4",
             "models": ["glm-4.6", "glm-4.5-air"]},
        ],
    },
    {
        "id": "moonshot",
        "type": TYPE_OFFICIAL,
        "name_key": "ai_providers.moonshot",
        "kinds": ["llm"],
        "formats": [
            {"format": FORMAT_OPENAI_CHAT, "base_url": "https://api.moonshot.cn/v1",
             "models": ["kimi-k2-0905-preview"]},
        ],
    },
    {
        "id": "minimax",
        "type": TYPE_OFFICIAL,
        "name_key": "ai_providers.minimax",
        "kinds": ["llm"],
        "formats": [
            {"format": FORMAT_OPENAI_CHAT, "base_url": "https://api.minimax.io/v1",
             "models": ["MiniMax-M2"]},
        ],
    },
    {
        "id": "xai",
        "type": TYPE_OFFICIAL,
        "name_key": "ai_providers.xai",
        "kinds": ["llm"],
        "formats": [
            {"format": FORMAT_OPENAI_CHAT, "base_url": "https://api.x.ai/v1",
             "models": ["grok-4", "grok-4-fast"]},
        ],
    },
    {
        "id": "mistral",
        "type": TYPE_OFFICIAL,
        "name_key": "ai_providers.mistral",
        "kinds": ["llm"],
        "formats": [
            {"format": FORMAT_OPENAI_CHAT, "base_url": "https://api.mistral.ai/v1",
             "models": ["mistral-large-latest", "mistral-small-latest"]},
        ],
    },
    {
        "id": "opencode_go",
        "type": TYPE_RELAY,
        "name_key": "ai_providers.opencode_go",
        "kinds": ["llm"],
        "formats": [
            {"format": FORMAT_OPENAI_CHAT, "base_url": "https://opencode.ai/zen/go/v1",
             "models": ["deepseek-v4.1-flash"]},
        ],
    },
    {
        "id": "commandcode_goat",
        "type": TYPE_RELAY,
        "name_key": "ai_providers.commandcode_goat",
        "kinds": ["decision"],
        "formats": [
            {"format": FORMAT_SYSTEMONE, "base_url": "https://api.commandcode.ai/provider",
             "models": ["typesafe/jev-1.14.0", "jev-latest"]},
        ],
    },
    {
        # 目检反馈④（2026-11-12）：Jev 官方厂商补全——TypeSafe 官方 API 与
        # systemone 适配器同线（POST {base}/v1/systemone + Bearer，docs.
        # typesafe.ai quickstart 实核），与 CommandCode 中转并存供选。
        "id": "typesafe",
        "type": TYPE_OFFICIAL,
        "name_key": "ai_providers.typesafe",
        "kinds": ["decision"],
        "formats": [
            {"format": FORMAT_SYSTEMONE, "base_url": "https://api.typesafe.ai",
             "models": ["jev-latest"]},
        ],
    },
    {
        # F（用户裁定 2026-11-12）：本机 Ollama——桌面版离线大模型卖点；
        # 回环端点仅 desktop 放行（server 模式 SSRF 闸拦截），desktop_only 标记
        # 由前端据此在服务器模式置灰并说明。
        "id": "local_ollama",
        "type": TYPE_OFFICIAL,
        "name_key": "ai_providers.local_ollama",
        "kinds": ["llm"],
        "desktop_only": True,
        "formats": [
            {"format": FORMAT_OPENAI_CHAT, "base_url": "http://127.0.0.1:11434/v1",
             "models": ["qwen3:8b", "llama3.1:8b"]},
        ],
    },
)


def snapshot() -> list[dict]:
    """目录快照（两级深拷贝——返回值可安全 JSON 化/被调用方持有）。"""
    return [
        {**entry, "kinds": list(entry["kinds"]),
         "formats": [
             {**fmt, "models": list(fmt["models"])}
             for fmt in entry["formats"]
         ]}
        for entry in _CATALOG
    ]


def format_ids() -> tuple[str, ...]:
    """目录中出现过的全部 format 取值（供目录完整性测试）。"""
    seen: list[str] = []
    for entry in _CATALOG:
        for fmt in entry["formats"]:
            if fmt["format"] not in seen:
                seen.append(fmt["format"])
    return tuple(seen)
