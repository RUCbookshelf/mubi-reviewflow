# Topic-cluster ordering (cluster) — behaviour and in-app help source

2.1.5 W6. Topic-cluster ordering is the fourth queue-ordering strategy, next to
`max`, `uncertainty` and `mixed`. It is the only strategy that needs **no model
configuration at all**: no provider, no key, no model file, no model call.

This file is the **source of the in-app help wording** (§二.6 of the design).
The dictionary entries and the help page are implemented by the frontend from
`out/release_plan/drafts/frontend_contract_2.1.5-w6-topic-cluster.md`; the
backend ships no user-visible string for this feature except the structured
message keys listed below.

## What it does (product semantics)

- **Selection.** Settings → sorting settings → strategy **「主题聚类 (cluster)」**.
  It can be selected with no AI provider/key configured; the other three
  strategies train a model and therefore need labels and a profile.
- **Effect.** The initial-screening queue (and the home queue, which shares the
  same order) becomes **same-topic runs**: records whose titles/abstracts share
  vocabulary sit next to each other, so the inclusion decision keeps the same
  frame for a stretch instead of switching between “qualitative interviews” and
  “the RCT pool”. No invented topic labels are shown — only the order changes.
- **Refresh.** The existing “ranking refresh interval” (default: every 20 changed
  decisions) is reused unchanged. Records that already have a decision **keep
  their position**; only the undecided segment is reordered.
- **Guards unchanged.** The consecutive-exclusion reminder still fires, and the
  existing `seed` setting is reused: the same data + same seed + same decided
  set gives the same order, so the method can be reported reproducibly.
- **Full-text stage.** The same strategy value applies to the full-text queue
  (ordering after eligibility filtering). Records already decided at stage 2
  keep their import positions.

## Honest disclosure (原文, design §二.6)

> 主题聚类按标题+摘要的词法相似度把同主题文献排在一起，纯本地计算、不联网、不使用模型；它只改变阅读顺序，不影响任何筛选决定的记录与统计。

English rendering for the English help page:

> Topic clustering arranges records that share title/abstract wording next to
> each other. It is computed purely locally, without network access and without
> a model; it only changes the reading order and never affects the recording or
> statistics of any screening decision.

## Structured message keys (frontend dictionary slots)

| key | value (zh-CN source) | used by |
| --- | --- | --- |
| `al.settings.section_title` | 排序设置 | settings block title (replaces 「机器学习排序设置」 — the only wording change in that block) |
| `al.strategy.cluster` | 主题聚类 (cluster) | strategy dropdown option |
| `al.strategy.cluster.hint` | 同主题排在一起；纯本地计算，无需模型 | option `title` / hint |
| `al.cluster.disclosure` | 主题聚类按标题+摘要的词法相似度把同主题文献排在一起，纯本地计算、不联网、不使用模型；它只改变阅读顺序，不影响任何筛选决定的记录与统计。 | settings hint + in-app help “排序” section |

No backend response carries this text: it lives in the dictionary / help page
only.

## How it is computed (backend)

- `coscreen/topic_order.py` — pure functions: `topic_text` (title + abstract +
  keywords if present), `vectorize` (reuses `coscreen.al.features.
  TfidfVectorizer` with its default parameters — word-level TF-IDF, CJK
  bigrams, built-in English stopwords, L2-normalised), `cluster_order`
  (greedy nearest-neighbour traversal).
- Zero file/network/model IO, no cache: every refresh recomputes.
- **Numeric kernel = existing runtime dependencies.** Similarity and traversal
  run on `numpy` + `scipy.sparse` (versions already pinned in
  `build/requirements-app.txt`; the statistics core imports them at module
  level too). 2026-11-08 owner ruling (B′): one path only, **no pure-stdlib
  fallback**, so no two implementations can disagree on float accumulation
  order. The design's "no new component / model file / new third-party
  dependency / no network" bar is met — these are existing dependencies, not
  new ones; `features.py` and the whole `coscreen/al` stack stay untouched.
- **Determinism hardening.** Rows are sorted into canonical CSR order before
  multiplication; each candidate's cosine is quantised with `np.round(…, 12)`
  before comparison; ties after quantisation are broken by `base_order`
  position (`argmax` keeps the earliest undecided record); the start is
  `random.Random(seed).randrange(...)`. Same data + same seed + same decided
  set ⇒ bit-identical order; an artificial exact-tie case is nailed in
  `TopicOrderUnitNails.test_exact_ties_are_broken_by_base_order`.
- **Performance (measured on this machine, 2026-11-08).** 1784 records:

  | corpus | vectorisation (existing `TfidfVectorizer`) | traversal (this change) | full rerun |
  |---|---|---|---|
  | realistic (60 % records with 60–150-word abstracts) | ≈0.9–1.0 s | **≈0.10 s** | ≈1.0–1.1 s |
  | dense (250-word abstracts, ≈403–415 tokens/record) | ≈3.9–4.6 s | **≈0.38–0.63 s** | ≈4.3–5.2 s |

  The traversal is what this change adds and is nailed at **<2 s on both
  corpora**; the full path is nailed at **<2 s on the realistic corpus**. On
  the dense corpus the full path only carries a regression ceiling
  (measured × 1.5 ≈ 7.9 s) because vectorisation is inherited from the shared
  feature stack — ML ranking pays exactly the same cost on every rerank.
  Probe: `E:/rf214tmp/g3/benchproto10.py`; nails:
  `build/tests/test_2_1_5_w6_topic_cluster.py::PerformanceNails`.
- Rankings are advisory only. They never write a decision, never change a
  decision's recorded value, and never feed any statistic: the audit nail
  `AuditDecisionSetNails` drives the same decision rule over **max-ordered**,
  **cluster-ordered** and import-ordered queues (max after 5 real labels so
  the model genuinely ranks) through the real decision endpoints and asserts
  identical final include/exclude sets.

## Endpoints touched

| endpoint | change |
| --- | --- |
| `GET /al/rank` | `strategy=cluster` branch: no model, `available` always `true`, `score: null`, order = cluster order of pending records |
| `GET /al/status` | `available: true` for `cluster` (nothing to train) |
| `PUT /al/settings` | accepts `strategy: cluster`; existing stored values need no migration |
| `GET /stage2/queue` | order only, when the caller's strategy is `cluster` |
| `GET /work-queue/fulltext` | order only, when the caller's strategy is `cluster` (the `screen` queue is untouched; its AI order comes from `/al/rank`) |
| `GET /al/shared-ranking` | `cluster` handled explicitly (returns cluster order) instead of failing on an unknown query strategy |

## Limits

- No topic labels or automatic topic naming (deliberate non-goal, v1).
- No embeddings / neural model, and no cluster-aware active learning (deferred;
  see design §六/§七).
- ML+cluster fusion (bucket by model score, then cluster inside a bucket) is
  deferred by the product owner (design §七 档 A).
- Memory: the similarity matrix is materialised once for queues up to 5000
  records (≈200 MB peak); above that the same maths runs row-by-row in O(m)
  memory (slower). Both branches are nailed to produce the same order
  (`test_rowwise_memory_path_matches_dense_path`).
