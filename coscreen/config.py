"""全局配置：预设排除理由、去重阈值、界面文案。"""

APP_NAME = "木笔ReviewFlow 协同文献筛选"

# 预设排除理由（筛选界面下拉框；另附"自定义"入口）。
#
# b11·F3-24：这两个常量此前是**死配置**且与实际界面分叉——config 只有 6 条，
# 初筛卡（index.html 的 #excReason）硬编码 8 条、全文阶段（#ftReason）是另一组
# 6 条，改 config 不产生任何界面效果（注释却在宣称它驱动下拉框）。现在把这个
# 词表收敛为唯一文档化的口径并与界面逐条对齐：初筛 = 下列 8 条，全文 = 前置于
# 下列 8 条之前的「全文无法获取」（见 FULLTEXT_EXCLUSION_REASONS）。
# 词表是展示层词表（理由按自由文本落库，服务端不校验枚举），故不做运行时校验；
# 它与 index.html 的一致性由 build/tests/test_r9_lx_cross_boundary.py 的钉子
# 逐项比对（界面再分叉即红），避免再次变成无人对照的死配置。
DEFAULT_EXCLUSION_REASONS = [
    "人群不符",
    "干预不符",
    "主题不符合",
    "领域不符合",
    "研究设计不符",
    "非目标语言",
    "发表类型不符",
    "重复文献",
    "结局指标不符",
    "非实证研究",
]

#: 全文阶段下拉：先给只在全文阶段成立的「全文无法获取」，其余与初筛同一词表。
FULLTEXT_EXCLUSION_REASONS = ["全文无法获取", *DEFAULT_EXCLUSION_REASONS]

#: 「其他（自定义）」选项旁输入框的占位文案（与界面 aria-label/placeholder 同字）。
CUSTOM_REASON_PLACEHOLDER = "请输入自定义排除理由"

# 模糊去重阈值（rapidfuzz，0-100）
FUZZY_TITLE_THRESHOLD = 90
FUZZY_AUTHOR_THRESHOLD = 80

# 决策常量与中文标签
DECISIONS = ("include", "exclude", "maybe")
DECISION_LABELS = {"include": "纳入", "exclude": "排除", "maybe": "待定"}

# ---- 主动学习排序（AL，借鉴 ASReview；Phase 1）----
AL_DEFAULT_STRATEGY = "max"      # max | uncertainty | mixed | cluster（2.1.5 W6：cluster 不要求模型）
AL_SEED = 42                     # mixed 策略随机种子；cluster 用作遍历起点（确定性）
AL_MIN_LABELED = 5               # 开始训练前最少的 include+exclude 标签数
AL_REORDER_INTERVAL = 20         # 每批有变化的文献决定后刷新排序（不改变模型算法）
AL_MIXED_RANDOM_RATIO = 0.05     # mixed：随机抽样占比
AL_STOP_STREAK_EXCLUDE = 0       # 0=关闭(默认); >0 时仅作为进度提示,不作为安全建议

# ---- 模拟基准（Phase 2）----
SIM_RECALL_TARGET = 0.95         # WSS@95 / R@95 的召回目标
