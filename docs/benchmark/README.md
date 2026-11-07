# 统计方法验证（统计内核数值对拍材料）

本目录收纳「木笔 ReviewFlow 统计内核与 R 社区（metafor / meta / metadat）公开发表值的数值对拍」全套材料，与 [召回证书方法学报告](../methodology/recall_certificate.md) 并列，作为对外可核查的统计方法验证证据。

- **入库来源**：`out/benchmark/`（任务 A，2026-10-27）原样入库；入库基线 `7535a44`（分支 `fix/2.1.4-r10-ux`）。
- **被测对象**：`fix/2.1.4 @ eb11741` 的 `coscreen` 统计内核（直接调用纯函数／DB 内核，不经 HTTP 层）。注意：**被测内核提交 `eb11741` 与本目录入库基线 `7535a44` 不是同一提交**；夹具把被测树钉在 `fixtures/common.py:15` 的 `CODE_UNDER_TEST`（`E:/rf214-ai` 静态副本），重跑方式见下文「复现」。
- **结论口径**：10 个数据集 × 204 项检查 = **159 PASS · 11 FAIL（全部归因参考侧，内核零算法错误）· 31 NOT-RUN（设计内跳过）· 3 INFO**。逐项明细见 `A4_comparison.md`，归因过程见 `A5_attribution.md`。

## 文件索引

| 文件 | 内容 |
| --- | --- |
| [`验证报告_v1.md`](验证报告_v1.md) | 对外验证报告（v1 草稿）：方法、数据集、结果总览、参考侧差异披露、局限、复现、结论 |
| [`A1_datasets.md`](A1_datasets.md) | 参考数据集清单：逐条数据、来源 URL、发表值、容差建议（含两处 ⚠ 已知不一致标注） |
| [`A2_inventory.md`](A2_inventory.md) | 统计模块调用契约盘点：内核入口与签名、纯函数形态、DB 夹具配方 |
| [`A4_comparison.md`](A4_comparison.md) | 全量运行与数值比对报告：逐行 PASS/FAIL/NOT-RUN、容差与判定口径（由 `fixtures/make_report.py` 从 `fixtures/results/*.json` 转录） |
| [`A5_attribution.md`](A5_attribution.md) | 偏差归因与修复建议：19 项原始 FAIL 的三方对拍归因、A1/A2 勘误记录 |
| `fixtures/` | 可一键复现的基准夹具与结果 JSON（清单见下）。即 A 系列里的 **A3**：`fixtures/common.py:1` 自述为「A3 benchmark fixtures」；`out/benchmark/` 下没有独立的 `A3_*.md`，A3 交付物就是这套夹具包 |

## fixtures 清单（与磁盘逐文件一致）

夹具模块（`fixtures/*.py`，14 个）：

| 模块 | 数据集／用途 |
| --- | --- |
| `run_all.py` | 顺序执行全部夹具（幂等，覆盖 `results/*.json`） |
| `common.py` | 共享工具：`CODE_UNDER_TEST` 钉定、容差与舍入地板判定、结果写出 |
| `make_report.py` | 由 `results/*.json` 生成 `A4_comparison.md`（无手工抄写） |
| `normand1999.py` | Normand 1999 卒中单元（EE/DL/REML、Q/I²） |
| `dk2007.py` | DerSimonian & Kacker 2007 多 τ² 估计量 |
| `linde2005.py` | Linde 2005 子集（元回归） |
| `raudenbush1985.py` | Raudenbush 1985（REML 边界 τ²、元回归） |
| `bcg.py` | BCG 疫苗（13 试验）：EE/DL/REML、剪补法 |
| `rothman2008.py` | Rothman 2008 MH 族 OR/RR/RD/IRD/IRR |
| `yusuf1985.py` | Yusuf 1985 Peto OR |
| `egger2001.py` | Egger 2001 发表偏倚检验（含 DB 入口夹具） |
| `kappa_cohen.py` | Cohen κ 经典例 |
| `senn2013.py` | Senn 2013 网络 Meta（图论／P-score／DB WLS） |
| `kappa_fleiss_light.py` | Fleiss／Light κ（产品未实现，设计内 NOT-RUN） |

结果文件（`fixtures/results/*.json`，11 个）：`bcg.json`、`dk2007.json`、`egger2001.json`、`kappa_cohen.json`、`kappa_fleiss_light.json`、`linde2005.json`、`normand1999.json`、`raudenbush1985.json`、`rothman2008.json`、`senn2013.json`、`yusuf1985.json`。每个文件含 `counts`（PASS/FAIL/NOT-RUN/INFO）与逐项 `checks`。

### 清单钉（sha256，逐文件）

`build/tests/test_r10_u7_benchmark_docs.py` 按本块逐文件核对：磁盘上 `fixtures/` 与 `fixtures/results/` 的文件集合必须与本块**完全相同**（多一个、少一个、内容改一个字节都判红）。新增或修改夹具时必须同步本块，否则钉子红。

```text
fixtures-manifest-v1
    "fixtures/bcg.py": "d671b4c2d20c89cfbce023f73acc4b6618063b753703d2a15e905fab67ac3eb3",
    "fixtures/common.py": "a1e9e769d3437eb5207edbbdf3d51e47c197cead0d3aa67d7c934792a832aeee",
    "fixtures/dk2007.py": "0a19a835c0002078733a0bc89a692a991b5f28db0fa95d80c594df4f78bedb26",
    "fixtures/egger2001.py": "46ea168c54c65d78a9faa9eef8cc8d64aad5719cf7f38a9d79ef8c46ce13d9f9",
    "fixtures/kappa_cohen.py": "3419213d2c97781d514b7e7383f75d6a40ef95208584056a2cc204df86a33e7a",
    "fixtures/kappa_fleiss_light.py": "08c598e05bac98a61bd1874da0d4889c89fecdb4472b65ea60d4cfabe4ba5c2b",
    "fixtures/linde2005.py": "9e98239eb01cb4a3f48b77208d7e422d67f22749245f0e678d23dc0b9af37d90",
    "fixtures/make_report.py": "1f0208f56d27771cf5c9145ba4394a962ab2e45f310434bb2e359dc9bf5cbe6a",
    "fixtures/normand1999.py": "4c2d2b9814e27e3b09a7787051f7d9d16dfe4e75a520ff4fff0ee4217d7f6bbd",
    "fixtures/raudenbush1985.py": "18050c9ea845f8f93ba9e5b6ef0b5284493ab967fab6e70ff4847e24361c15d7",
    "fixtures/rothman2008.py": "53eb9c18194b507aad21f86f0ef8307f900e1dd1e4ad7ad9b4241ccbdb80a863",
    "fixtures/run_all.py": "7be4b375bf48769ba9379675d8b2b366f5dbf5ddf37f2697f75c26b194ef0068",
    "fixtures/senn2013.py": "80428719e064249d33794bec3a2dae5b340cfeeee0a58188e6ced67b37f44f68",
    "fixtures/yusuf1985.py": "ee4a4db58d5785a584350af7b20acf03d5d23759b1bc21d4cd726be444434e01",
    "fixtures/results/bcg.json": "acf2d6617f18d2cf937ee64016e90dd0321f82f0fb6e483a43a0e5285f212121",
    "fixtures/results/dk2007.json": "0aee2f90866c6887d2b8ce53697432a8316f3d76dc7e3573597e6c9c728c0d48",
    "fixtures/results/egger2001.json": "4edbcf50567c510708ebb5f92f5b06d12bdaee8822c986f81834500731a3abba",
    "fixtures/results/kappa_cohen.json": "6da9d3f7754400618a5fbef94baf4a731f191145edd62422903a856c93234501",
    "fixtures/results/kappa_fleiss_light.json": "9969da21bef6e91ba7779fabbcd3c03437e486e0737984343a69010bfe5623c6",
    "fixtures/results/linde2005.json": "3ee3d963e87e0b35243190402c12af848f954adfe292772f770e7c9030c0b016",
    "fixtures/results/normand1999.json": "66232d847479ccebd1fd3b03542ec2905df5f73451bfd061fde146b8aad14c23",
    "fixtures/results/raudenbush1985.json": "b6aa3a6275e3d7f924467af5feeaa318d5d5fc2799463320c0b060e3bae0a265",
    "fixtures/results/rothman2008.json": "4d335c970cb5a8eea2723b569f0e8a91788f4e930425884ce5830462963c2ad0",
    "fixtures/results/senn2013.json": "d8414ed4c517a2e7711f09d8723f316d443f78f2914a0fe8ce29e08792e2e2c4",
    "fixtures/results/yusuf1985.json": "90599b533b0386a17fca34029d2c8ec975865b1c98394e08f74def6056ecfe8f",
```


## 复现

依赖：Python 3.11；numpy / scipy / statsmodels / pandas（本次验证环境的具体版本见 `A4_comparison.md` 头部）。

```bash
# 仓库根目录；Linux/macOS 把 Scripts/python.exe 换成 bin/python
"C:/Users/16073/.codex/preview-data/reviewflow-2-1-1-venv/Scripts/python.exe" docs/benchmark/fixtures/run_all.py
```

夹具幂等：重跑覆盖 `fixtures/results/*.json`，内容与库内文件逐字节一致（无时间戳等浮动字段）。只跑其中几项时用 `--only normand1999,bcg`。

被测内核不在本仓：`fixtures/common.py:15` 把 `CODE_UNDER_TEST` 钉在 `E:/rf214-ai`（`eb11741` 的静态副本）。若要对本仓工作树复跑同一套夹具，把该常量指向仓库根即可（在本仓工作树复跑过一次，除 `code_under_test` 字段外 11 个结果文件与库内文件逐字段一致）。

## 原始语料不入库（不在 A7 指名范围内 + 体积 + 许可）

A7 指名的入库对象是「验证报告 v1 ＋ A1–A5 交付文档 ＋ fixtures 复现包」，`out/benchmark/datasets/` 的第三方语料不在这三项里，因此不入库；此外它本身是 **20 个文件、共 31,895,238 字节（约 31.9 MB）** 的第三方文献筛选语料（总量在 50 MB 门槛之下，但不属统计验证材料本体），且全部可由公开 URL 重新取得。下表给出逐文件 sha256 与来源，供重新下载后逐字节核对。

| 文件 | 字节 | sha256 | 来源／许可 |
| --- | ---: | --- | --- |
| `datasets/cohen_2006_v1/ACEInhibitors.csv` | 4117098 | `67bc0b3ad9bd9d98610cfe124336fb192a19a921dfb6ac52ee486caac64fd27f` | Cohen 2006 药物类别数据集（ASReview 镜像）；custom open license |
| `datasets/cohen_2006_v1/ADHD.csv` | 1360265 | `762e19ddf4f0ecbaa23c8330e04f438ab7da7a08c20b13dacd4354a366c2abcf` | 同上（ADHD 子集） |
| `datasets/cohen_2006_v1/Antihistamines.csv` | 475126 | `ff9c5afbaa8874f6bc841ecc8f3fd3c9e1316a976f6003e6afe35e9a0187d0a2` | 同上 |
| `datasets/cohen_2006_v1/AtypicalAntipsychotics.csv` | 1672292 | `0c4ed716ef48b958fe5f05fe40a3f59dd444cd196a25c5bb10529ccfe71f326f` | 同上 |
| `datasets/cohen_2006_v1/BetaBlockers.csv` | 3364044 | `72a74deb254ef15fb382458b65982e3ee19de03a7a1bd3ff8a9a3e02266062a7` | 同上 |
| `datasets/cohen_2006_v1/CalciumChannelBlockers.csv` | 1986880 | `478098f4b66b459971d9f3db203a30b35ccd26de32112a91f0b2ecf66f778e41` | 同上 |
| `datasets/cohen_2006_v1/Estrogens.csv` | 683824 | `73283554279fcb256670ef36d13ad7271e2689f40f4ada585eaa0f04cd6520ea` | 同上 |
| `datasets/cohen_2006_v1/NSAIDS.csv` | 623911 | `0986d97f7e85689ef897c8e13f7ac7207d0f1e9cd7c356ef952979aefe1fd8af` | 同上 |
| `datasets/cohen_2006_v1/Opiods.csv` | 2834692 | `be75d6e9dd1bb1f76460f1180700467bbb27e41972a3d654e9483e42cf315945` | 同上 |
| `datasets/cohen_2006_v1/OralHypoglycemics.csv` | 866887 | `47a81abdb8cc2028b1a8d8ffd98c161c7c1adaf4378afdfd07c9151b3d33fa3b` | 同上 |
| `datasets/cohen_2006_v1/ProtonPumpInhibitors.csv` | 2100388 | `37bcfdc76bf7826c1141cbd4ffe22dde75fb354c3c326c900c364c365b97762f` | 同上 |
| `datasets/cohen_2006_v1/SkeletalMuscleRelaxants.csv` | 1869881 | `cf1110f03dc0184d3d6be532a954cb6fa9235d66df1f5f1acc0508d7bbc9f9e9` | 同上 |
| `datasets/cohen_2006_v1/Statins.csv` | 4768961 | `d880b79bef64296a57ee8f0b886ae1f7a7267ce12a3f7a8596ad77539210b470` | 同上 |
| `datasets/cohen_2006_v1/Triptans.csv` | 963899 | `f0fd3f247420b63ae3b389535c068e17963c6da50fb0707e6f429d556d7e1757` | 同上 |
| `datasets/cohen_2006_v1/UrinaryIncontinence.csv` | 468188 | `cdc2af42b63ac845caa49dcc31e0f8192d920718d7efc98a80394e689f229e8f` | 同上 |
| `datasets/nagtegaal_2019/Nagtegaal_2019.csv` | 3638285 | `abfbdb973aa125f26ce872a5193c931d0690f7fe2e1fb75551de9c0acecd200f` | Nagtegaal 2019（CC0，doi:10.7910/DVN/WMGPGZ/HY6N2S） |
| `datasets/synergy_index/index.csv` | 14812 | `cef0e7d8e761eed0657bef0760f5fe8c58a4288e95c884543b4617b8e053d870` | SYNERGY 数据集索引（元数据，含各数据集 URL／许可列） |
| `datasets/synergy_index/index.json` | 23523 | `bf913809f84ed37dfd2179bbb1e8072dcc6b07f913771e38ffc580ae64ae937a` | 同上（JSON 形态） |
| `datasets/synergy_index/index_v1.json` | 23118 | `5d829c669f744cc6e91165b15dd3364554320d684398b841764b2609bb857d4b` | 同上（metadata-v1 版本快照） |
| `datasets/synergy_index/datasets.toml` | 39164 | `8c714ce8619ebd385c0311a99b6320f67e8c7b7346c726e84a4f76007247a1a4` | SYNERGY `datasets.toml`（各数据集纳入/排除标准原始文本） |

重新取得方式：Cohen 2006 各子集取自 ASReview `systematic-review-datasets`（metadata-v1-final）对应 `output/online/<子集>.csv`，原始出处 <https://dmice.ohsu.edu/cohenaa/systematic-drug-class-review-data.html>；Nagtegaal 2019 取自同一镜像的 `output/Nagtegaal_2019.csv`。许可为 CC0 与「custom open license」（见 `index.csv` 的 `license` 列），库内不再复制原始语料，仅保留索引与摘要。

> 说明：这 20 个文件是**文献筛选数据集**（综述语料），不属于统计内核数值对拍的可复现输入——数值对拍输入全部以字面量硬编码在夹具里（`A1_datasets.md` 逐条转录），夹具离线、幂等、不读 `datasets/`。

## 存疑标注保持原样

`A1_datasets.md` 对两处已知不一致（BCG 的 I² 提取值 92.22% 与 Q 精确导出的 92.12%；Cohen κ 手算 pe 笔误 0.6532 与页面 0.6512）保留了原有（⚠）标注；`A4_comparison.md` 勘误 #1 已定向该 κ 笔误、`A5_attribution.md` 记录解除过程。`A2_inventory.md` §0 提到把基准与 R meta/metafor、RevMan、sklearn 对齐的**取向**（未引用任何竞品实测数值，也无竞品对比结论）；入库不改写这些段落，保持入库前口径。

## 与产品代码的关系

本目录是**证据材料**，不被产品运行时读取，也不进入应用打包：Windows 构建把 `custom_backend` / `custom_frontend` / `coscreen` 三个目录与九个入口文件复制进 `staging`（`build/build_windows.bat:271`、`:281-283`），Linux/macOS 走 PyInstaller `--add-data` 同样只收这三个目录（`build/build_linux.sh:75-78`、`build/build_macos.sh:172-175`）——`docs/` 不在任何一条里。它被引用之处只有根 [`README.md`](../../README.md) 的「统计方法验证」小节与 `build/tests/test_r10_u7_benchmark_docs.py` 的钉子；`conftest.py` 只影响在仓库根裸跑 `pytest` 时的收集面，对 `pytest build/tests` 惰性。
