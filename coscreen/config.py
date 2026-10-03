"""全局配置：预设排除理由、去重阈值、界面文案。"""

APP_NAME = "木笔ReviewFlow 协同文献筛选"

# 预设排除理由（筛选界面下拉框；另附"自定义"入口）
DEFAULT_EXCLUSION_REASONS = [
    "人群不符",
    "干预不符",
    "研究设计不符",
    "非目标语言",
    "发表类型不符",
    "重复文献",
]
CUSTOM_REASON_PLACEHOLDER = "自定义（请填写）"

# 模糊去重阈值（rapidfuzz，0-100）
FUZZY_TITLE_THRESHOLD = 90
FUZZY_AUTHOR_THRESHOLD = 80

# 决策常量与中文标签
DECISIONS = ("include", "exclude", "maybe")
DECISION_LABELS = {"include": "纳入", "exclude": "排除", "maybe": "待定"}

# ---- 主动学习排序（AL，借鉴 ASReview；Phase 1）----
AL_DEFAULT_STRATEGY = "max"      # max | uncertainty | mixed
AL_SEED = 42                     # mixed 策略随机种子（确定性）
AL_MIN_LABELED = 5               # 开始训练前最少的 include+exclude 标签数
AL_MIXED_RANDOM_RATIO = 0.05     # mixed：随机抽样占比
AL_STOP_STREAK_EXCLUDE = 0       # 0=关闭(默认); >0 时仅作为进度提示,不作为安全建议

# ---- 模拟基准（Phase 2）----
SIM_RECALL_TARGET = 0.95         # WSS@95 / R@95 的召回目标
