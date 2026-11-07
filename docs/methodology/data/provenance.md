# SYNERGY v1 基准数据集 Provenance(来源与许可清单)

- 采集人:科研数据智能体 R0;采集日期:**2026-09-25**
- 用途:AL 筛选研究(S0 等效性基座 / S1 停止准则 / S2 双筛员 / S3 双语)的统一基准数据
- 转换脚本:`research/data/scripts/build_synergy_v1.py`(仅标准库,可复现)
- 构建事实存档:`research/data/scripts/synergy_v1_build_facts.json`(逐集 CITATION/metadata 的机器可读版)
- 校验汇总:`research/data/synergy/summary.csv`(含逐集 sha256)

## 1. 来源标识

| 项 | 值 |
| --- | --- |
| 数据集 | SYNERGY — Open machine learning dataset on study selection in systematic reviews(**v1**) |
| 发布仓库 | `github.com/asreview/synergy-dataset` |
| 获取 URL | `https://github.com/asreview/synergy-dataset/archive/refs/heads/releases/v1.tar.gz`(整分支 tarball,经 codeload) |
| 分支/commit | `releases/v1` 分支头 `fcbd981087e5`(2023-04-24;相对 tag `v1.0`=`254b1cc6e11a` 仅删除一个 README.md,**数据内容逐位相同**,已用 GitHub compare API 核实) |
| 等价归档版本 | DataverseNL V1,DOI:`10.34894/HE6NAQ`(官方推荐引用的持久存档) |
| 下载日期 | 2026-09-25 |
| 下载量 | tarball 413 MB(压缩);本目录转换后 CSV 共 244 MB(255,631,540 字节) |

注意:该仓库 `master` 分支已转向"ID 列表 + OpenAlex 组装脚本"的新流水线(datasets.toml,117 个数据集条目),`master` 的 README 仍描述 v1(169,288 条/26 综述)。本基准取 **`releases/v1` = 官方 v1 发布**(即 README 表与 DataverseNL V1),与 Ferdinands et al. 2023 等既有文献所用版本一致,保证可比性。

## 2. 组装约定(与我们研究格式的映射)

源结构:每综述一个目录,含 `labels.csv`(openalex_id,doi,pmid,label_included)、`works_*.zip`(OpenAlex Work 对象分片)、`metadata.json`、`metadata_publication.json`、`CITATION.txt`。

转换为统一 CSV:`record_id,title,abstract,included`,存 `research/data/synergy/<数据集名>.csv`:

- `record_id` = OpenAlex Work ID(URL 形,如 `https://openalex.org/W2402226166`),与 `labels.csv` 精确匹配,行序保持 labels.csv 原序;
- `title` = Work 对象 `title` 字段原文;
- `abstract` = 由 `abstract_inverted_index` 反演的明文(逐位复刻 pyalex `invert_abstract`:按词位稳定排序后空格连接)——这是官方 synergy-dataset 包 1.2 的既定用法;
- `included` = `label_included`(1=综述纳入,0=排除);
- **不做任何文本清洗**(保留大小写/换行/Unicode 原样;官方包导出时会把 `\n`→空格、删 `\r`,我们按"原文"约定不清洗,下游 `coscreen/al/simulation.py` 的 `" ".join(title, abstract, ...)` 组装对两者不敏感)。

## 3. 许可(如实记录)

1. **SYNERGY 编排数据集(我们的直接使用对象):CC0 1.0**。依据:仓库 `LICENSE` 文件(CC0 1.0 Universal 全文)+ README 明示 "SYNERGY dataset is released under the CC0 1.0 license"。
2. **记录级元数据来源 OpenAlex:CC0 1.0**(README 引 OpenAlex FAQ:OpenAlex works metadata 以 CC0 发布)。
3. **数据集级(逐综述)许可:SYNERGY v1 的各集 `metadata.json` 未标明独立数据许可**——各集仅有原始数据存档 DOI(见 §5 表),许可状态需到对应存档页核实;凡 v1 metadata 未标明者,本文件如实标注"**许可未标明(SYNERGY v1 metadata)**",整包按仓库级 CC0-1.0 使用。
4. **综述论文自身的出版许可**(OpenAlex 在 metadata_publication.json 内记录的 `license` 字段,属论文出版许可,**不是**筛选数据许可;列出仅为完整性):见 §5 表"论文许可(OpenAlex)"列;无记录者标"无记录"。

引用义务(README 要求):使用 SYNERGY 须引用 De Bruin et al. 2023(DataverseNL,DOI:10.34894/HE6NAQ),见 §4。

## 4. 全局引用信息(SYNERGY 数据集本身)

> De Bruin, Jonathan; Ma, Yongchao; Ferdinands, Gerbrich; Teijema, Jelle; Van de Schoot, Rens, 2023, "SYNERGY - Open machine learning dataset on study selection in systematic reviews", https://doi.org/10.34894/HE6NAQ, DataverseNL, V1

BibTeX(仓库 README 给出):

```bib
@data{HE6NAQ_2023,
  author = {De Bruin, Jonathan and Ma, Yongchao and Ferdinands, Gerbrich and Teijema, Jelle and Van de Schoot, Rens},
  publisher = {DataverseNL},
  title = {{SYNERGY - Open machine learning dataset on study selection in systematic reviews}},
  year = {2023},
  version = {V1},
  doi = {10.34894/HE6NAQ},
  url = {https://doi.org/10.34894/HE6NAQ}
}
```

完整致谢名单(各综述数据分享者)见仓库 `ATTRIBUTION.md`(12.8 KB,随 tarball 获取;机器可读版在 `synergy_v1_build_facts.json`)。

## 5. 逐集清单:引用 / 存档 DOI / 许可 / 校验统计

"论文许可(OpenAlex)"= 该综述论文自身在 OpenAlex 记录的出版许可;数据集级许可除另有说明外均为"**未标明(SYNERGY v1 metadata)**",整包按 CC0-1.0。

| 数据集 | 记录数 | 纳入数 | 纳入% | 缺title/缺abstract(纳入中) | 重复ID | 原始数据存档 DOI | 论文许可(OpenAlex) |
| --- | ---: | ---: | ---: | --- | ---: | --- | --- |
| Appenzeller-Herzog_2019 | 2873 | 26 | 0.90 | 5/676 (0/1) | 0 | 10.5281/zenodo.3625931 | 无记录 |
| Bos_2018 | 4878 | 10 | 0.21 | 0/62 (0/0) | 0 | 未标明 | 无记录 |
| Brouwer_2019 | 38114 | 62 | 0.16 | 38/426 (0/0) | 0 | 10.17605/OSF.IO/R45YZ | 无记录 |
| Chou_2003 | 1908 | 15 | 0.79 | 2/110 (0/0) | 0 | 未标明 | elsevier-specific |
| Chou_2004 | 1630 | 9 | 0.55 | 8/189 (0/0) | 0 | 未标明 | elsevier-specific |
| Donners_2021 | 258 | 15 | 5.81 | 1/8 (0/1) | 0 | 未标明 | cc-by-nc |
| Hall_2012 | 8793 | 104 | 1.18 | 0/0 (0/0) | 0 | 10.5281/zenodo.1162952 | cc-by-nc-sa, pd |
| Jeyaraman_2020 | 1175 | 96 | 8.17 | 2/2 (0/0) | 0 | 未标明 | 无记录 |
| Leenaars_2019 | 5812 | 17 | 0.29 | 45/49 (0/0) | 0 | 10.17605/OSF.IO/PH56S | cc-by |
| Leenaars_2020 | 7216 | 583 | 8.08 | 47/376 (1/6) | 0 | 10.17605/OSF.IO/T7GYR | cc-by |
| Meijboom_2021 | 882 | 37 | 4.20 | 0/57 (0/1) | 0 | 未标明 | cc-by-nc |
| Menon_2022 | 975 | 74 | 7.59 | 0/6 (0/0) | 0 | 未标明 | cc-by |
| Moran_2021 | 5214 | 111 | 2.13 | 15/43 (0/0) | 0 | 10.17605/OSF.IO/3TPHJ | cc-by-nc, cc-by-sa |
| Muthu_2021 | 2719 | 336 | 12.36 | 0/24 (0/0) | 0 | 未标明 | 无记录 |
| Nelson_2002 | 366 | 80 | 21.86 | 1/8 (0/0) | 0 | 未标明 | 无记录 |
| Oud_2018 | 952 | 20 | 2.10 | 2/15 (0/0) | 0 | 未标明 | cc-by-nc |
| Radjenovic_2013 | 5935 | 48 | 0.81 | 0/0 (0/0) | 0 | 10.5281/zenodo.1162952 | 无记录 |
| Sep_2021 | 271 | 40 | 14.76 | 0/1 (0/0) | 0 | 10.17605/OSF.IO/GY2MC | cc-by |
| Smid_2020 | 2627 | 27 | 1.03 | 5/110 (0/0) | 0 | 10.17605/OSF.IO/PUQ7X | cc-by-nc |
| Walker_2018 | 48375 | 762 | 1.58 | 222/1533 (3/82) | 0 | 10.1186/s13643-016-0263-z | 无记录 |
| Wassenaar_2017 | 7668 | 111 | 1.45 | 11/429 (0/6) | 0 | 10.1186/s13643-016-0263-z | pd |
| Wolters_2018 | 4280 | 19 | 0.44 | 8/381 (0/0) | 0 | 未标明 | 无记录 |
| van_Dis_2020 | 9128 | 72 | 0.79 | 14/166 (0/0) | 0 | 未标明 | 无记录 |
| van_de_Schoot_2018 | 4544 | 38 | 0.84 | 6/71 (0/0) | 0 | 10.17605/OSF.IO/VW3T7 | cc-by-nc-nd |
| van_der_Valk_2021 | 725 | 89 | 12.28 | 0/10 (0/1) | 0 | 10.17605/OSF.IO/WDZH5 | cc-by |
| van_der_Waal_2022 | 1970 | 33 | 1.68 | 0/13 (0/1) | 0 | 10.5281/zenodo.7308296 | 无记录 |
| **合计** | **169,288** | **2,834** | **1.67** | 432/4,765(4/98) | **0** | | |

### 5.1 各综述原始引用(来自各集 CITATION.txt,原文照录)

- **Appenzeller-Herzog_2019**:Appenzeller‐Herzog, C., Mathes, T., Heeres, M. L. S., Weiss, K. H., Houwen, R. H. J., & Ewald, H. (2019). Comparative effectiveness of common therapies for Wilson disease: A systematic review and meta‐analysis of controlled studies. Liver International, 39(11), 2136–2152. Portico. https://doi.org/10.1111/liv.14179
- **Bos_2018**:Bos, D., Wolters, F. J., Darweesh, S. K. L., Vernooij, M. W., Wolf, F., Ikram, M. A., & Hofman, A. (2018). Cerebral small vessel disease and the risk of dementia: A systematic review and meta‐analysis of population‐based evidence. Alzheimer's & Dementia, 14(11), 1482–1492. Portico. https://doi.org/10.1016/j.jalz.2018.04.007
- **Brouwer_2019**:Brouwer, M. E., Williams, A. D., Kennis, M., Fu, Z., Klein, N. S., Cuijpers, P., & Bockting, C. L. H. (2019). Psychological theories of depressive relapse and recurrence: A systematic review and meta-analysis of prospective studies. Clinical Psychology Review, 74, 101773. https://doi.org/10.1016/j.cpr.2019.101773
- **Chou_2003**:Chou, R., Clark, E., & Helfand, M. (2003). Comparative efficacy and safety of long-acting oral opioids for chronic non-cancer pain: a systematic review. Journal of Pain and Symptom Management, 26(5), 1026–1048. https://doi.org/10.1016/j.jpainsymman.2003.03.003(所属集合引用:Cohen, A. M., Hersh, W. R., Peterson, K., & Yen, P.-Y. (2006). Reducing Workload in Systematic Review Preparation Using Automated Citation Classification. Journal of the American Medical Informatics Association, 13(2), 206–219. https://doi.org/10.1197/jamia.m1929)
- **Chou_2004**:Chou, R., Peterson, K., & Helfand, M. (2004). Comparative efficacy and safety of skeletal muscle relaxants for spasticity and musculoskeletal conditions: a systematic review. Journal of Pain and Symptom Management, 28(2), 140–175. https://doi.org/10.1016/j.jpainsymman.2004.05.002(集合引用同 Chou_2003)
- **Donners_2021**:Donners, A. A. M. T., Rademaker, C. M. A., Bevers, L. A. H., Huitema, A. D. R., Schutgens, R. E. G., Egberts, T. C. G., & Fischer, K. (2021). Pharmacokinetics and Associated Efficacy of Emicizumab in Humans: A Systematic Review. Clinical Pharmacokinetics, 60(11), 1395–1406. https://doi.org/10.1007/s40262-021-01042-w
- **Hall_2012**:Hall, T., Beecham, S., Bowes, D., Gray, D., & Counsell, S. (2012). A Systematic Literature Review on Fault Prediction Performance in Software Engineering. IEEE Transactions on Software Engineering, 38(6), 1276–1304. https://doi.org/10.1109/tse.2011.103(集合引用:Yu, Z., Kraft, N. A., & Menzies, T. (2018). Finding better active learners for faster literature reviews. Empirical Software Engineering, 23(6), 3161–3186. https://doi.org/10.1007/s10664-017-9587-0)
- **Jeyaraman_2020**:Jeyaraman, M., Muthu, S., & Ganie, P. A. (2020). Does the Source of Mesenchymal Stem Cell Have an Effect in the Management of Osteoarthritis of the Knee? Meta-Analysis of Randomized Controlled Trials. CARTILAGE, 13(1_suppl), 1532S-1547S. https://doi.org/10.1177/1947603520951623
- **Leenaars_2019**:Leenaars, C. H. C., Drinkenburg, W. H. P., Nolten, C., Dematteis, M., Joosten, R. N. J. M. A., Feenstra, M. G. P., & De Vries, R. B. M. (2019). Sleep and Microdialysis: An Experiment and a Systematic Review of Histamine and Several Amino Acids. Journal of Circadian Rhythms, 17(1), 7. https://doi.org/10.5334/jcr.183
- **Leenaars_2020**:Leenaars, C., Stafleu, F., de Jong, D., van Berlo, M., Geurts, T., Coenen-de Roo, T., Prins, J.-B., Kempkes, R., Elzinga, J., Bleich, A., de Vries, R., Meijboom, F., & Ritskes-Hoitinga, M. (2020). A Systematic Review Comparing Experimental Design of Animal and Human Methotrexate Efficacy Studies for Rheumatoid Arthritis: Lessons for the Translational Value of Animal Studies. Animals, 10(6), 1047. https://doi.org/10.3390/ani10061047
- **Meijboom_2021**:Meijboom, R. W., Gardarsdottir, H., Egberts, T. C. G., & Giezen, T. J. (2021). Patients Retransitioning from Biosimilar TNFα Inhibitor to the Corresponding Originator After Initial Transitioning to the Biosimilar: A Systematic Review. BioDrugs, 36(1), 27–39. https://doi.org/10.1007/s40259-021-00508-4
- **Menon_2022**:Menon, J. M. L., Struijs, F., & Whaley, P. (2022). The methodological rigour of systematic reviews in environmental health. Critical Reviews in Toxicology, 52(3), 167–187. https://doi.org/10.1080/10408444.2022.2082917
- **Moran_2021**:Moran, N. P., Sánchez‐Tójar, A., Schielzeth, H., & Reinhold, K. (2020). Poor nutritional condition promotes high‐risk behaviours: a systematic review and meta‐analysis. Biological Reviews, 96(1), 269–288. Portico. https://doi.org/10.1111/brv.12655
- **Muthu_2021**:Muthu, S., & Ramakrishnan, E. (2020). Fragility Analysis of Statistically Significant Outcomes of Randomized Control Trials in Spine Surgery. Spine, 46(3), 198–208. https://doi.org/10.1097/brs.0000000000003645
- **Nelson_2002**:Nelson, H. D., Humphrey, L. L., Nygren, P., Teutsch, S. M., & Allan, J. D. (2002). Postmenopausal Hormone Replacement Therapy. JAMA, 288(7), 872. https://doi.org/10.1001/jama.288.7.872(集合引用同 Chou_2003 的 Cohen et al. 2006)
- **Oud_2018**:Oud, M., Arntz, A., Hermens, M. L., Verhoef, R., & Kendall, T. (2018). Specialized psychotherapies for adults with borderline personality disorder: A systematic review and meta-analysis. Australian & New Zealand Journal of Psychiatry, 52(10), 949–961. https://doi.org/10.1177/0004867418791257
- **Radjenovic_2013**:Radjenović, D., Heričko, M., Torkar, R., & Živkovič, A. (2013). Software fault prediction metrics: A systematic literature review. Information and Software Technology, 55(8), 1397–1418. https://doi.org/10.1016/j.infsof.2013.02.009(集合引用同 Hall_2012 的 Yu et al. 2018)
- **Sep_2021**:Sep, M. S. C., Vellinga, M., Sarabdjitsingh, R. A., & Joëls, M. (2021). The rodent object-in-context task: A systematic review and meta-analysis of important variables. PLOS ONE, 16(7), e0249102. https://doi.org/10.1371/journal.pone.0249102
- **Smid_2020**:Smid, S. C., McNeish, D., Miočević, M., & van de Schoot, R. (2019). Bayesian Versus Frequentist Estimation for Structural Equation Models in Small Sample Contexts: A Systematic Review. Structural Equation Modeling: A Multidisciplinary Journal, 27(1), 131–161. https://doi.org/10.1080/10705511.2019.1577140
- **Walker_2018**:Walker, V. R., Boyles, A. L., Pelch, K. E., Holmgren, S. D., Shapiro, A. J., Blystone, C. R., Devito, M. J., Newbold, R. R., Blain, R., Hartman, P., Thayer, K. A., & Rooney, A. A. (2018). Human and animal evidence of potential transgenerational inheritance of health effects: An evidence map and state-of-the-science evaluation. Environment International, 115, 48–69. https://doi.org/10.1016/j.envint.2017.12.032(集合引用:Howard, B. E., Phillips, J., Miller, K., Tandon, A., Mav, D., Shah, M. R., Holmgren, S., Pelch, K. E., Walker, V., Rooney, A. A., Macleod, M., Shah, R. R., & Thayer, K. (2016). SWIFT-Review: a text-mining workbench for systematic review. Systematic Reviews, 5(1). https://doi.org/10.1186/s13643-016-0263-z)
- **Wassenaar_2017**:Wassenaar, P. N. H., Trasande, L., & Legler, J. (2017). Systematic Review and Meta-Analysis of Early-Life Exposure to Bisphenol A and Obesity-Related Outcomes in Rodents. Environmental Health Perspectives, 125(10), 106001. https://doi.org/10.1289/ehp1233
- **Wolters_2018**:Wolters, F. J., Segufa, R. A., Darweesh, S. K. L., Bos, D., Ikram, M. A., Sabayan, B., Hofman, A., & Sedaghat, S. (2018). Coronary heart disease, heart failure, and the risk of dementia: A systematic review and meta‐analysis. Alzheimer's & Dementia, 14(11), 1493–1504. Portico. https://doi.org/10.1016/j.jalz.2018.01.007
- **van_Dis_2020**:van Dis, E. A. M., van Veen, S. C., Hagenaars, M. A., Batelaan, N. M., Bockting, C. L. H., van den Heuvel, R. M., Cuijpers, P., & Engelhard, I. M. (2020). Long-term Outcomes of Cognitive Behavioral Therapy for Anxiety-Related Disorders. JAMA Psychiatry, 77(3), 265. https://doi.org/10.1001/jamapsychiatry.2019.3986
- **van_de_Schoot_2018**:van de Schoot, R., Sijbrandij, M., Depaoli, S., Winter, S. D., Olff, M., & van Loey, N. E. (2018). Bayesian PTSD-Trajectory Analysis with Informed Priors Based on a Systematic Literature Search and Expert Elicitation. Multivariate Behavioral Research, 53(2), 267–291. https://doi.org/10.1080/00273171.2017.1412293
- **van_der_Valk_2021**:Valk, E., Abawi, O., Mohseni, M., Abdelmoumen, A., Wester, V., Voorn, B., Iyer, A., Akker, E., Hoeks, S., Berg, S., Rijke, Y., Stalder, T., & Rossum, E. (2021). Cross‐sectional relation of long‐term glucocorticoids in hair with anthropometric measurements and their possible determinants: A systematic review and meta‐analysis. Obesity Reviews, 23(3). Portico. https://doi.org/10.1111/obr.13376
- **van_der_Waal_2022**:van der Waal, M. S., Seghers, N., Welsing, P. M. J., van Huis, L. H., Emmelot-Vonk, M. H., & Hamaker, M. E. (2023). A meta-analysis on the role older adults with cancer favour in treatment decision making. Journal of Geriatric Oncology, 14(1), 101383. https://doi.org/10.1016/j.jgo.2022.09.012

## 6. 校验方法与结果

逐集执行(脚本 §1):

1. 记录数/纳入数三方核对:实际构建值 = 各集 `metadata.json` 的 `n_records`/`n_records_included` = 仓库 README v1 公布值 → **26/26 全部一致**;
2. `record_id` 唯一性:**26/26 无重复**;
3. label↔work 匹配:无"有标签无 work"行、无"有 work 无标签"行(26/26 干净);
4. 缺失文本:全体缺 title 432(0.26%)、缺 abstract 4,765(2.82%)、两者皆缺 8;**纳入类记录中缺 title 4、缺 abstract 98、两者皆缺 0**(缺失集中在 Walker_2018 等大集;对 AL 回放意味着这些记录将仅以 title 或空文本参与,需在 S0 协议中固定处理约定);
5. 编码:UTF-8 写出 + 严格模式复读往返,**26/26 通过**;抽样与源 Work 对象逐字段比对(title 与反演 abstract 逐位一致);
6. 完整性:总计 169,288 条 / 2,834 纳入,与官方口径(README/DataverseNL V1)完全一致;每份 CSV 的 sha256 见 `summary.csv`。

## 7. 已知数据问题(如实记录)

- 部分记录(尤其 Walker_2018 的 1,533 条)缺 abstract、432 条缺 title——OpenAlex 元数据覆盖所致,非转换损失(与 labels/works 全量匹配一致);
- 各集数据集级许可在 SYNERGY v1 metadata 中未标明(§3 第 3 条);
- 仓库 master 流水线(datasets.toml)与 v1 发布的个别命名/收录不同(如 master 目录含 Kwok_2020、Nagtegaal_2019 等 v1 之外的数据集),本研究一律以 `releases/v1` 26 集为准。

## 8. 复现

```bash
curl -sL -o /tmp/synergy_v1.tar.gz \
  https://github.com/asreview/synergy-dataset/archive/refs/heads/releases/v1.tar.gz
python3 research/data/scripts/build_synergy_v1.py \
  --tar /tmp/synergy_v1.tar.gz \
  --outdir research/data/synergy \
  --workdir /tmp/synergy_v1_work
```

输出确定性:同 tarball 重复运行 → 逐集行数、纳入数、sha256 完全一致(2026-09-25 两次运行核对)。
