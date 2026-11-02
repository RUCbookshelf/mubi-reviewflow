# A4 全量运行与数值比对报告 — 木笔 ReviewFlow 统计模块第三方体检

- 生成方式：`fixtures/make_report.py` 由 `fixtures/results/*.json` 逐位转录，无手工抄写。
- 被测代码：`E:/rf214-ai`（fix/2.1.4 @ eb11741 静态副本；coscreen 统计内核，直调纯函数/DB 内核，未走 HTTP 层，依据 A2 §0）。
- 解释器：`C:/Users/16073/.codex/preview-data/reviewflow-2-1-1-venv/Scripts/python.exe`（Python 3.11.15；numpy 2.4.6 / scipy 1.17.1 / statsmodels 0.14.5 / pandas 2.3.3）。
- 输入数据：全部硬编码自 A1 §1–§8 字面量表；未访问网络。
- 容差：rel 列 = 生效容差 = max(A1 §9 建议值, 发表值末位半单位换算的相对精度)——发表精度的舍入半单位是可核验精度的下限，比 A1 建议更松时以舍入地板为准（如 2 位小数的发表量无法支撑 1e-4 相对比对）；整型输出要求全等；发表值为 0 的量（如边界 τ²=0.000）用绝对容差 0.5×10^-d。
- 判定口径：统计量严格、p 宽松、整型全等（A1 §9）。“derived” = 由被测代码输出经代数恒等式推导后比对（如 SE=CI 宽/2z、H²=Q/df、log(OR)），推导式在各夹具文件头注明。
- 复跑（幂等，覆盖 results/）：`"C:/Users/16073/.codex/preview-data/reviewflow-2-1-1-venv/Scripts/python.exe" E:/mubi-reviewflow/out/benchmark/fixtures/run_all.py`

## 汇总

- **PASS：159　FAIL：11　NOT-RUN：31　INFO（已执行但无匹配发表参照）：3**（按检查行计数）

| 数据集 | PASS | FAIL | NOT-RUN | INFO |
|---|---|---|---|---|
| Normand1999 | 22 | 2 | 0 | 0 |
| DK2007 | 8 | 2 | 8 | 0 |
| Linde2005 | 15 | 2 | 1 | 0 |
| Raudenbush1985 | 20 | 2 | 0 | 0 |
| BCG | 42 | 0 | 4 | 0 |
| Rothman2008 | 17 | 2 | 4 | 0 |
| Yusuf1985 | 10 | 0 | 0 | 0 |
| Egger2001 | 6 | 1 | 3 | 0 |
| CohenKappa | 4 | 0 | 3 | 0 |
| Senn2013 | 15 | 0 | 2 | 3 |
| FleissLightKappa | 0 | 0 | 6 | 0 |

## Normand1999

参照：A1 §1.2 metafor analyses:normand1999 (declared consistent with Normand 1999 Table X p.352)

| 检查项 | 方法 | 实测 | 发表 | 绝对偏差 | 相对偏差 | 容差 | 判定 |
|---|---|---|---|---|---|---|---|
| normand-ee-pooled | IV fixed (EE) | -3.49386134 | -3.49 | 0.00386134 | 0.0011064 | rel<= 0.00143 (A1 0.0001) | **PASS** |
| normand-ee-se | IV fixed (EE) | 0.78195725 | 0.78 | 0.00195725 | 0.00250929 | rel<= 0.00641 (A1 0.0001) | **PASS** |
| normand-ee-ci-low | IV fixed (EE) | -5.02649755 | -5.03 | 0.00350245 | 0.00069631 | rel<= 0.000994 (A1 0.0001) | **PASS** |
| normand-ee-ci-high | IV fixed (EE) | -1.96122514 | -1.96 | 0.00122514 | 0.00062507 | rel<= 0.00255 (A1 0.0001) | **PASS** |
| normand-ee-q | IV fixed (EE) | 241.05901004 | 241.06 | 0.00098996 | 4.107e-06 | rel<= 0.0001 (A1 0.0001) | **PASS** |
| normand-ee-qdf | IV fixed (EE) | 8 | 8 | 0 | 0.0 | rel<= 0 (A1 0) | **PASS** |
| normand-ee-i2 | IV fixed (EE) | 96.68131052 | 96.68 | 0.00131052 | 1.356e-05 | rel<= 0.001 (A1 0.001) | **PASS** |
| normand-dl-tau2 | IV DL random | 218.72160081 | 218.72 | 0.00160081 | 7.319e-06 | rel<= 0.0001 (A1 0.0001) | **PASS** |
| normand-dl-tau | IV DL random | 14.78923936 | 14.79 | 0.00076064 | 5.143e-05 | rel<= 0.000338 (A1 0.0001) | **PASS** |
| normand-dl-pooled | IV DL random | -14.09722293 | -14.1 | 0.00277707 | 0.00019696 | rel<= 0.000355 (A1 0.0001) | **PASS** |
| normand-dl-se | IV DL random | 5.27979921 | 5.28 | 0.00020079 | 3.803e-05 | rel<= 0.000947 (A1 0.0001) | **PASS** |
| normand-dl-ci-low | IV DL random | -24.44562939 | -24.45 | 0.00437061 | 0.00017876 | rel<= 0.000204 (A1 0.0001) | **PASS** |
| normand-dl-ci-high | IV DL random | -3.74881647 | -3.75 | 0.00118353 | 0.00031561 | rel<= 0.00133 (A1 0.0001) | **PASS** |
| normand-reml-tau2 | IV REML random | 685.19653275 | 685.2 | 0.00346725 | 5.060e-06 | rel<= 0.0001 (A1 0.0001) | **PASS** |
| normand-reml-tau | IV REML random | 26.17625895 | 26.18 | 0.00374105 | 0.0001429 | rel<= 0.000191 (A1 0.0001) | **PASS** |
| normand-reml-pooled | IV REML random | -15.12168493 | -15.12 | 0.00168493 | 0.00011144 | rel<= 0.000331 (A1 0.0001) | **PASS** |
| normand-reml-se | IV REML random | 8.95420099 | 8.95 | 0.00420099 | 0.00046938 | rel<= 0.000559 (A1 0.0001) | **PASS** |
| normand-reml-ci-low | IV REML random | -32.67159637 | -32.67 | 0.00159637 | 4.886e-05 | rel<= 0.000153 (A1 0.0001) | **PASS** |
| normand-reml-ci-high | IV REML random | 2.42822652 | 2.43 | 0.00177348 | 0.00072983 | rel<= 0.00206 (A1 0.0001) | **PASS** |
| normand-reml-i2 | IV REML random | 96.68131052 | 96.68132415 | 1.363e-05 | 1.410e-07 | rel<= 0.001 (A1 0.001) | **PASS** |
| normand-reml-i2-page | IV REML random | 96.68131052 | 98.92 | 2.23868948 | 0.02263131 | rel<= 0.001 (A1 0.001) | **FAIL** |
| normand-reml-h2 | IV REML random | 30.13237626 | 30.1325 | 0.00012374 | 4.107e-06 | rel<= 0.001 (A1 0.001) | **PASS** |
| normand-reml-h2-page | IV REML random | 30.13237626 | 92.26 | 62.12762374 | 0.67339718 | rel<= 0.001 (A1 0.001) | **FAIL** |
| normand-reml-p | IV REML random | 0.0912614 | 0.09 | 0.0012614 | 0.01401552 | rel<= 0.0556 (A1 0.01) | **PASS** |

## DK2007

参照：A1 §1.3 metafor analyses:dersimonian2007 (declared consistent with D&K 2007 Tables 3-4)

| 检查项 | 方法 | 实测 | 发表 | 绝对偏差 | 相对偏差 | 容差 | 判定 |
|---|---|---|---|---|---|---|---|
| dk2007-dl-tau | IV DL random | 0.23235552 | 0.2323 | 5.552e-05 | 0.00023898 | rel<= 0.000215 (A1 0.0001) | **FAIL** |
| dk2007-dl-mu | IV DL random | -0.32401094 | -0.324 | 1.094e-05 | 3.377e-05 | rel<= 0.000154 (A1 0.0001) | **PASS** |
| dk2007-dl-se | IV DL random | 0.15403302 | 0.154 | 3.302e-05 | 0.00021441 | rel<= 0.000325 (A1 0.0001) | **PASS** |
| dk2007-pm-tau | IV PM random | 0.36805402 | 0.3681 | 4.598e-05 | 0.0001249 | rel<= 0.000136 (A1 0.0001) | **PASS** |
| dk2007-pm-mu | IV PM random | -0.38105004 | -0.3811 | 4.996e-05 | 0.00013111 | rel<= 0.000131 (A1 0.0001) | **PASS** |
| dk2007-pm-se | IV PM random | 0.20599013 | 0.206 | 9.872e-06 | 4.792e-05 | rel<= 0.000243 (A1 0.0001) | **PASS** |
| dk2007-reml-tau | IV REML random | 0.18432511 | 0.1843 | 2.511e-05 | 0.00013622 | rel<= 0.000271 (A1 0.0001) | **PASS** |
| dk2007-reml-mu | IV REML random | -0.2980493 | -0.298 | 4.930e-05 | 0.00016543 | rel<= 0.000168 (A1 0.0001) | **PASS** |
| dk2007-reml-se | IV REML random | 0.13435822 | 0.1343 | 5.822e-05 | 0.00043349 | rel<= 0.000372 (A1 0.0001) | **FAIL** |
| dk2007-reml-direct-tau2 | profile_reml_tau2 (direct) | 0.03397574 | 0.03396649 | 9.255e-06 | 0.00027246 | rel<= 0.00147 (A1 0.0001) | **PASS** |
| dk2007-ca | IV CA (HE) tau2 estimator | — |  | — | — | — | **NOT-RUN** |
| dk2007-ca2 | IV CA2 tau2 estimator | — |  | — | — | — | **NOT-RUN** |
| dk2007-dl2 | IV DL2 tau2 estimator | — |  | — | — | — | **NOT-RUN** |
| dk2007-eb | IV EB tau2 estimator | — |  | — | — | — | **NOT-RUN** |
| dk2007-ml | IV ML tau2 estimator | — |  | — | — | — | **NOT-RUN** |
| dk2007-hs | IV HS tau2 estimator | — |  | — | — | — | **NOT-RUN** |
| dk2007-sj | IV SJ tau2 estimator | — |  | — | — | — | **NOT-RUN** |
| dk2007-sj2 | IV SJ2 tau2 estimator | — |  | — | — | — | **NOT-RUN** |

## Linde2005

参照：A1 §1.4 metafor analyses:viechtbauer2007b (EE/DL same page; meta-regression = Viechtbauer 2007 Table 2 p.113)

| 检查项 | 方法 | 实测 | 发表 | 绝对偏差 | 相对偏差 | 容差 | 判定 |
|---|---|---|---|---|---|---|---|
| linde-ee-pooled | IV fixed (EE) | 0.32482627 | 0.33 | 0.00517373 | 0.01567797 | rel<= 0.0152 (A1 0.0001) | **FAIL** |
| linde-ee-se | IV fixed (EE) | 0.04791829 | 0.05 | 0.00208171 | 0.04163425 | rel<= 0.1 (A1 0.0001) | **PASS** |
| linde-ee-ci-low | IV fixed (EE) | 0.23090643 | 0.23 | 0.00090643 | 0.00394098 | rel<= 0.0217 (A1 0.0001) | **PASS** |
| linde-ee-ci-high | IV fixed (EE) | 0.41874611 | 0.42 | 0.00125389 | 0.00298545 | rel<= 0.0119 (A1 0.0001) | **PASS** |
| linde-ee-q | IV fixed (EE) | 51.57066369 | 51.55 | 0.02066369 | 0.00040085 | rel<= 0.0001 (A1 0.0001) | **FAIL** |
| linde-ee-qdf | IV fixed (EE) | 16 | 16 | 0 | 0.0 | rel<= 0 (A1 0) | **PASS** |
| linde-ee-i2 | IV fixed (EE) | 68.9746091 | 68.96 | 0.0146091 | 0.00021185 | rel<= 0.001 (A1 0.001) | **PASS** |
| linde-ee-h2 | IV fixed (EE) | 3.22316648 | 3.22 | 0.00316648 | 0.00098338 | rel<= 0.00155 (A1 0.001) | **PASS** |
| linde-dl-tau2 | IV DL random | 0.09027414 | 0.09 | 0.00027414 | 0.00304595 | rel<= 0.0556 (A1 0.0001) | **PASS** |
| linde-dl-tau | IV DL random | 0.30045655 | 0.3 | 0.00045655 | 0.00152182 | rel<= 0.0167 (A1 0.0001) | **PASS** |
| linde-dl-pooled | IV DL random | 0.45245884 | 0.45 | 0.00245884 | 0.00546409 | rel<= 0.0111 (A1 0.0001) | **PASS** |
| linde-dl-se | IV DL random | 0.09295764 | 0.09 | 0.00295764 | 0.03286272 | rel<= 0.0556 (A1 0.0001) | **PASS** |
| linde-dl-ci-low | IV DL random | 0.27026186 | 0.27 | 0.00026186 | 0.00096984 | rel<= 0.0185 (A1 0.0001) | **PASS** |
| linde-dl-ci-high | IV DL random | 0.63465582 | 0.63 | 0.00465582 | 0.0073902 | rel<= 0.00794 (A1 0.0001) | **PASS** |
| linde-dl-or | IV DL random | 1.57217316 | 1.57 | 0.00217316 | 0.00138418 | rel<= 0.00318 (A1 0.0001) | **PASS** |
| linde-dl-or-low | IV DL random | 1.31030752 | 1.31 | 0.00030752 | 0.00023475 | rel<= 0.00382 (A1 0.0001) | **PASS** |
| linde-dl-or-high | IV DL random | 1.88637279 | 1.89 | 0.00362721 | 0.00191916 | rel<= 0.00265 (A1 0.0001) | **PASS** |
| linde-metareg | DL meta-regression (dosage x baseline) | — |  | — | — | — | **NOT-RUN** |

## Raudenbush1985

参照：A1 §2.2 metafor analyses:raudenbush1985 (declared consistent with R&B 1985 eq.(27))

| 检查项 | 方法 | 实测 | 发表 | 绝对偏差 | 相对偏差 | 容差 | 判定 |
|---|---|---|---|---|---|---|---|
| rb-reml-tau2 | IV REML random | 0.01881829 | 0.019 | 0.00018171 | 0.00956385 | rel<= 0.0263 (A1 0.0001) | **PASS** |
| rb-reml-pooled | IV REML random | 0.08370124 | 0.084 | 0.00029876 | 0.00355666 | rel<= 0.00595 (A1 0.0001) | **PASS** |
| rb-reml-se | IV REML random | 0.05164067 | 0.052 | 0.00035933 | 0.00691018 | rel<= 0.00962 (A1 0.0001) | **PASS** |
| rb-reml-z | IV REML random | 1.62083954 | 1.621 | 0.00016046 | 9.899e-05 | rel<= 0.000308 (A1 0.0001) | **PASS** |
| rb-reml-ci-low | IV REML random | -0.01751261 | -0.018 | 0.00048739 | 0.02707702 | rel<= 0.0278 (A1 0.0001) | **PASS** |
| rb-reml-ci-high | IV REML random | 0.1849151 | 0.185 | 8.490e-05 | 0.00045895 | rel<= 0.0027 (A1 0.0001) | **PASS** |
| rb-reml-q | IV REML random | 35.8295363 | 35.83 | 0.0004637 | 1.294e-05 | rel<= 0.0001 (A1 0.0001) | **PASS** |
| rb-reml-qdf | IV REML random | 18 | 18 | 0 | 0.0 | rel<= 0 (A1 0) | **PASS** |
| rb-reml-i2 | IV REML random | 49.76211847 | 49.76276863 | 0.00065016 | 1.307e-05 | rel<= 0.001 (A1 0.001) | **PASS** |
| rb-reml-i2-page | IV REML random | 49.76211847 | 41.86 | 7.90211847 | 0.18877493 | rel<= 0.001 (A1 0.001) | **FAIL** |
| rb-reml-h2 | IV REML random | 1.99052979 | 1.99055556 | 2.576e-05 | 1.294e-05 | rel<= 0.00251 (A1 0.001) | **PASS** |
| rb-reml-h2-page | IV REML random | 1.99052979 | 1.72 | 0.27052979 | 0.15728476 | rel<= 0.00291 (A1 0.001) | **FAIL** |
| rb-reml-direct-tau2 | profile_reml_tau2 (direct) | 0.01881829 | 0.019 | 0.00018171 | 0.00956385 | rel<= 0.0263 (A1 0.0001) | **PASS** |
| rb-mr-intrcpt | REML meta-regression (weeks capped 3) | 0.40718534 | 0.407 | 0.00018534 | 0.00045539 | rel<= 0.00123 (A1 0.0001) | **PASS** |
| rb-mr-intrcpt-se | REML meta-regression (weeks capped 3) | 0.08703789 | 0.087 | 3.789e-05 | 0.00043557 | rel<= 0.00575 (A1 0.0001) | **PASS** |
| rb-mr-slope | REML meta-regression (weeks capped 3) | -0.15724443 | -0.157 | 0.00024443 | 0.00155689 | rel<= 0.00318 (A1 0.0001) | **PASS** |
| rb-mr-slope-se | REML meta-regression (weeks capped 3) | 0.03583123 | 0.036 | 0.00016877 | 0.00468817 | rel<= 0.0139 (A1 0.0001) | **PASS** |
| rb-mr-tau2 | REML meta-regression (weeks capped 3) | 0.0 | 0.0 | 0.0 | abs | abs<= 0.0005 | **PASS** |
| rb-mr-qe | REML meta-regression (weeks capped 3) | 16.5708253 | 16.571 | 0.0001747 | 1.054e-05 | rel<= 0.0001 (A1 0.0001) | **PASS** |
| rb-mr-qe-df | REML meta-regression (weeks capped 3) | 17 | 17 | 0 | 0.0 | rel<= 0 (A1 0) | **PASS** |
| rb-mr-qm | REML meta-regression (weeks capped 3) | 19.258711 | 19.258 | 0.000711 | 3.692e-05 | rel<= 0.0001 (A1 0.0001) | **PASS** |
| rb-mr-qm-df | REML meta-regression (weeks capped 3) | 1 | 1 | 0 | 0.0 | rel<= 0 (A1 0) | **PASS** |

## BCG

参照：A1 §1.1 metadat::dat.bcg + Viechtbauer 2010 JSS (yi/vi); A1 §5 metafor trimfill reference page

| 检查项 | 方法 | 实测 | 发表 | 绝对偏差 | 相对偏差 | 容差 | 判定 |
|---|---|---|---|---|---|---|---|
| bcg-yi-01 | binary_effect (escalc RR) | -0.88931133 | -0.88931133 | 3.920e-09 | 4.408e-09 | rel<= 1e-06 (A1 1e-06) | **PASS** |
| bcg-vi-01 | binary_effect (escalc RR) | 0.32558477 | 0.32558476 | 3.961e-12 | 1.217e-11 | rel<= 1e-06 (A1 1e-06) | **PASS** |
| bcg-yi-02 | binary_effect (escalc RR) | -1.58538866 | -1.58538866 | 2.799e-09 | 1.765e-09 | rel<= 1e-06 (A1 1e-06) | **PASS** |
| bcg-vi-02 | binary_effect (escalc RR) | 0.19458112 | 0.19458112 | 3.981e-10 | 2.046e-09 | rel<= 1e-06 (A1 1e-06) | **PASS** |
| bcg-yi-03 | binary_effect (escalc RR) | -1.34807315 | -1.34807315 | 1.700e-09 | 1.261e-09 | rel<= 1e-06 (A1 1e-06) | **PASS** |
| bcg-vi-03 | binary_effect (escalc RR) | 0.41536797 | 0.41536797 | 3.680e-10 | 8.859e-10 | rel<= 1e-06 (A1 1e-06) | **PASS** |
| bcg-yi-04 | binary_effect (escalc RR) | -1.44155119 | -1.44155119 | 2.131e-11 | 1.478e-11 | rel<= 1e-06 (A1 1e-06) | **PASS** |
| bcg-vi-04 | binary_effect (escalc RR) | 0.02001003 | 0.02001003 | 9.775e-11 | 4.885e-09 | rel<= 1e-06 (A1 1e-06) | **PASS** |
| bcg-yi-05 | binary_effect (escalc RR) | -0.21754732 | -0.21754732 | 2.211e-09 | 1.016e-08 | rel<= 1e-06 (A1 1e-06) | **PASS** |
| bcg-vi-05 | binary_effect (escalc RR) | 0.05121017 | 0.05121017 | 1.696e-10 | 3.312e-09 | rel<= 1e-06 (A1 1e-06) | **PASS** |
| bcg-yi-06 | binary_effect (escalc RR) | -0.78611559 | -0.78611559 | 4.181e-09 | 5.319e-09 | rel<= 1e-06 (A1 1e-06) | **PASS** |
| bcg-vi-06 | binary_effect (escalc RR) | 0.00690562 | 0.00690562 | 4.559e-10 | 6.602e-08 | rel<= 1e-06 (A1 1e-06) | **PASS** |
| bcg-yi-07 | binary_effect (escalc RR) | -1.62089822 | -1.62089822 | 3.598e-09 | 2.220e-09 | rel<= 1e-06 (A1 1e-06) | **PASS** |
| bcg-vi-07 | binary_effect (escalc RR) | 0.22301725 | 0.22301725 | 4.277e-10 | 1.918e-09 | rel<= 1e-06 (A1 1e-06) | **PASS** |
| bcg-yi-08 | binary_effect (escalc RR) | 0.01195233 | 0.01195233 | 3.524e-09 | 2.948e-07 | rel<= 1e-06 (A1 1e-06) | **PASS** |
| bcg-vi-08 | binary_effect (escalc RR) | 0.00396158 | 0.00396158 | 2.978e-10 | 7.518e-08 | rel<= 1e-06 (A1 1e-06) | **PASS** |
| bcg-yi-09 | binary_effect (escalc RR) | -0.46941765 | -0.46941765 | 1.262e-09 | 2.688e-09 | rel<= 1e-06 (A1 1e-06) | **PASS** |
| bcg-vi-09 | binary_effect (escalc RR) | 0.05643421 | 0.05643421 | 4.632e-10 | 8.209e-09 | rel<= 1e-06 (A1 1e-06) | **PASS** |
| bcg-yi-10 | binary_effect (escalc RR) | -1.3713448 | -1.3713448 | 3.473e-09 | 2.532e-09 | rel<= 1e-06 (A1 1e-06) | **PASS** |
| bcg-vi-10 | binary_effect (escalc RR) | 0.07302479 | 0.07302479 | 3.870e-10 | 5.299e-09 | rel<= 1e-06 (A1 1e-06) | **PASS** |
| bcg-yi-11 | binary_effect (escalc RR) | -0.33935883 | -0.33935883 | 1.662e-09 | 4.896e-09 | rel<= 1e-06 (A1 1e-06) | **PASS** |
| bcg-vi-11 | binary_effect (escalc RR) | 0.01241221 | 0.01241221 | 2.844e-11 | 2.291e-09 | rel<= 1e-06 (A1 1e-06) | **PASS** |
| bcg-yi-12 | binary_effect (escalc RR) | 0.4459134 | 0.4459134 | 5.714e-10 | 1.281e-09 | rel<= 1e-06 (A1 1e-06) | **PASS** |
| bcg-vi-12 | binary_effect (escalc RR) | 0.53250585 | 0.53250584 | 2.002e-10 | 3.759e-10 | rel<= 1e-06 (A1 1e-06) | **PASS** |
| bcg-yi-13 | binary_effect (escalc RR) | -0.01731395 | -0.01731395 | 1.783e-09 | 1.030e-07 | rel<= 1e-06 (A1 1e-06) | **PASS** |
| bcg-vi-13 | binary_effect (escalc RR) | 0.07140466 | 0.07140466 | 3.160e-10 | 4.426e-09 | rel<= 1e-06 (A1 1e-06) | **PASS** |
| bcg-reml-pooled-log | IV REML random | -0.71453234 | -0.7145 | 3.234e-05 | 4.527e-05 | rel<= 0.0001 (A1 0.0001) | **PASS** |
| bcg-reml-ci-low | IV REML random | -1.06689764 | -1.0669 | 2.361e-06 | 2.213e-06 | rel<= 0.0001 (A1 0.0001) | **PASS** |
| bcg-reml-ci-high | IV REML random | -0.36216705 | -0.3622 | 3.295e-05 | 9.098e-05 | rel<= 0.000138 (A1 0.0001) | **PASS** |
| bcg-reml-tau2 | IV REML random | 0.31324326 | 0.3132 | 4.326e-05 | 0.00013811 | rel<= 0.00016 (A1 0.0001) | **PASS** |
| bcg-reml-rr | IV REML random | 0.48942094 | 0.4894 | 2.094e-05 | 4.279e-05 | rel<= 0.000102 (A1 0.0001) | **PASS** |
| bcg-reml-rr-low | IV REML random | 0.34407431 | 0.3441 | 2.569e-05 | 7.467e-05 | rel<= 0.000145 (A1 0.0001) | **PASS** |
| bcg-reml-rr-high | IV REML random | 0.69616607 | 0.6962 | 3.393e-05 | 4.874e-05 | rel<= 0.0001 (A1 0.0001) | **PASS** |
| bcg-q | heterogeneity | 152.2330122 | 152.233 | 1.220e-05 | 8.016e-08 | rel<= 0.0001 (A1 0.0001) | **PASS** |
| bcg-qdf | heterogeneity | 12 | 12 | 0 | 0.0 | rel<= 0 (A1 0) | **PASS** |
| bcg-i2 | heterogeneity | 92.11734707 | 92.12 | 0.00265293 | 2.880e-05 | rel<= 0.001 (A1 0.001) | **PASS** |
| bcg-h2 | heterogeneity | 12.68608435 | 12.69 | 0.00391565 | 0.00030856 | rel<= 0.001 (A1 0.001) | **PASS** |
| bcg-reml-direct-tau2 | profile_reml_tau2 (direct) | 0.31324326 | 0.3132 | 4.326e-05 | 0.00013811 | rel<= 0.00016 (A1 0.0001) | **PASS** |
| bcg-tf-missing | trim-and-fill (EE, right, L0) | 4 | 4 | 0 | 0.0 | rel<= 0 (A1 0) | **PASS** |
| bcg-tf-adjusted | trim-and-fill (EE, right, L0) | -0.29102599 | -0.291 | 2.599e-05 | 8.932e-05 | rel<= 0.000172 (A1 0.0001) | **PASS** |
| bcg-tf-ci-low | trim-and-fill (EE, right, L0) | -0.36602232 | -0.366 | 2.232e-05 | 6.099e-05 | rel<= 0.000137 (A1 0.0001) | **PASS** |
| bcg-tf-ci-high | trim-and-fill (EE, right, L0) | -0.21602967 | -0.216 | 2.967e-05 | 0.00013734 | rel<= 0.000231 (A1 0.0001) | **PASS** |
| bcg-tf-r0 | trim-and-fill R0 estimator / R0 test | — |  | — | — | — | **NOT-RUN** |
| bcg-tf-re | trim-and-fill RE model (missing=1, adjusted -0.6571) and augmented-RE rerun (-0.3422) | — |  | — | — | — | **NOT-RUN** |
| bcg-eb | EB (empirical Bayes) pooling + EB meta-regression (A1 §1.5) | — |  | — | — | — | **NOT-RUN** |
| bcg-ee-dl-published | BCG-specific EE/DL published values | — |  | — | — | — | **NOT-RUN** |

## Rothman2008

参照：A1 §3.1 metafor analyses:rothman2008 (Rothman 2008 Tables 15-1/15-2/15-5)

| 检查项 | 方法 | 实测 | 发表 | 绝对偏差 | 相对偏差 | 容差 | 判定 |
|---|---|---|---|---|---|---|---|
| roth1-rd | MH RD | 0.03490027 | 0.035 | 9.973e-05 | 0.00284945 | rel<= 0.0143 (A1 0.001) | **PASS** |
| roth1-rd-se | MH RD | 0.03189808 | 0.032 | 0.00010192 | 0.00318507 | rel<= 0.0156 (A1 0.001) | **PASS** |
| roth1-rr-log | MH RR | 0.28183148 | 0.28 | 0.00183148 | 0.00654102 | rel<= 0.0179 (A1 0.0001) | **PASS** |
| roth1-rr | MH RR | 1.32555532 | 1.33 | 0.00444468 | 0.00334186 | rel<= 0.00376 (A1 0.001) | **PASS** |
| roth1-rr-se | MH RR | 0.25898163 | 0.26 | 0.00101837 | 0.00391682 | rel<= 0.0192 (A1 0.0001) | **PASS** |
| roth1-rr-ci90-low | MH RR | 0.86575321 | 0.87 | 0.00424679 | 0.00488137 | rel<= 0.00575 (A1 0.001) | **PASS** |
| roth1-rr-ci90-high | MH RR | 2.02955865 | 2.03 | 0.00044135 | 0.00021742 | rel<= 0.00246 (A1 0.001) | **PASS** |
| roth1-cmh | CMH test (correct=FALSE) | — |  | — | — | — | **NOT-RUN** |
| roth2-ird | MH IRD (person-time) | 11.43918826 | 11.44 | 0.00081174 | 7.096e-05 | rel<= 0.001 (A1 0.001) | **PASS** |
| roth2-ird-se | MH IRD (person-time) | 3.07772149 | 3.09 | 0.01227851 | 0.00397363 | rel<= 0.00162 (A1 0.001) | **FAIL** |
| roth2-logirr | MH IRR (person-time) | 0.35394864 | 0.35 | 0.00394864 | 0.01128184 | rel<= 0.0143 (A1 0.0001) | **PASS** |
| roth2-irr | MH IRR (person-time) | 1.42468202 | 1.42 | 0.00468202 | 0.00329719 | rel<= 0.00352 (A1 0.001) | **PASS** |
| roth2-irr-se | MH IRR (person-time) | 0.10671062 | 0.11 | 0.00328938 | 0.02990347 | rel<= 0.0455 (A1 0.0001) | **PASS** |
| roth2-irr-ci90-low | MH IRR (person-time) | 1.19533358 | 1.19 | 0.00533358 | 0.004482 | rel<= 0.0042 (A1 0.001) | **FAIL** |
| roth2-irr-ci90-high | MH IRR (person-time) | 1.69803549 | 1.7 | 0.00196451 | 0.00115559 | rel<= 0.00294 (A1 0.001) | **PASS** |
| roth2-mh-test | MH test (correct=FALSE) | — |  | — | — | — | **NOT-RUN** |
| roth2-q | IRR heterogeneity Q(4)=26.88 | — |  | — | — | — | **NOT-RUN** |
| roth3-logor | MH OR | 1.33003409 | 1.33 | 3.409e-05 | 2.563e-05 | rel<= 0.00376 (A1 0.0001) | **PASS** |
| roth3-or | MH OR | 3.78117227 | 3.78 | 0.00117227 | 0.00031013 | rel<= 0.00132 (A1 0.001) | **PASS** |
| roth3-or-se | MH OR | 0.59098961 | 0.59 | 0.00098961 | 0.0016773 | rel<= 0.00847 (A1 0.0001) | **PASS** |
| roth3-or-ci90-low | MH OR | 1.43038364 | 1.43 | 0.00038364 | 0.00026828 | rel<= 0.0035 (A1 0.001) | **PASS** |
| roth3-or-ci90-high | MH OR | 9.995405 | 10.0 | 0.004595 | 0.0004595 | rel<= 0.001 (A1 0.001) | **PASS** |
| roth3-cmh | CMH test | — |  | — | — | — | **NOT-RUN** |

## Yusuf1985

参照：A1 §3.2 metafor analyses:yusuf1985 (consistent with Yusuf 1985 Table 6 p.343)

| 检查项 | 方法 | 实测 | 发表 | 绝对偏差 | 相对偏差 | 容差 | 判定 |
|---|---|---|---|---|---|---|---|
| yusuf-k | Peto OR | 18 | 18 | 0 | 0.0 | rel<= 0 (A1 0) | **PASS** |
| yusuf-logor | Peto OR | -0.06916763 | -0.0692 | 3.237e-05 | 0.00046774 | rel<= 0.000723 (A1 0.0001) | **PASS** |
| yusuf-or | Peto OR | 0.93317024 | 0.93 | 0.00317024 | 0.00340886 | rel<= 0.00538 (A1 0.001) | **PASS** |
| yusuf-se | Peto OR | 0.11938081 | 0.1194 | 1.919e-05 | 0.00016075 | rel<= 0.000419 (A1 0.0001) | **PASS** |
| yusuf-ci-low | Peto OR | -0.30314971 | -0.3031 | 4.971e-05 | 0.00016401 | rel<= 0.000165 (A1 0.0001) | **PASS** |
| yusuf-ci-high | Peto OR | 0.16481445 | 0.1648 | 1.445e-05 | 8.767e-05 | rel<= 0.000303 (A1 0.0001) | **PASS** |
| yusuf-q | Peto OR (Q via derived IV rows) | 10.82748468 | 10.8275 | 1.532e-05 | 1.415e-06 | rel<= 0.0001 (A1 0.0001) | **PASS** |
| yusuf-qdf | Peto OR (Q via derived IV rows) | 17 | 17 | 0 | 0.0 | rel<= 0 (A1 0) | **PASS** |
| yusuf-i2 | Peto OR (Q via derived IV rows) | 0.0 | 0.0 | 0.0 | abs | abs<= 0.005 | **PASS** |
| yusuf-h2 | Peto OR (Q via derived IV rows) | 0.63691086 | 0.64 | 0.00308914 | 0.00482678 | rel<= 0.00781 (A1 0.001) | **PASS** |

## Egger2001

参照：A1 §8 metafor regtest reference page + metadat::dat.egger2001

| 检查项 | 方法 | 实测 | 发表 | 绝对偏差 | 相对偏差 | 容差 | 判定 |
|---|---|---|---|---|---|---|---|
| egger-reml-tau2 | IV REML random (underlying model) | 0.19106182 | 0.1911 | 3.818e-05 | 0.00019977 | rel<= 0.000262 (A1 0.0001) | **PASS** |
| egger-reml-pooled | IV REML random (underlying model) | -0.87454753 | -0.8745 | 4.753e-05 | 5.435e-05 | rel<= 0.0001 (A1 0.0001) | **PASS** |
| egger-reml-ci-low | IV REML random (underlying model) | -1.28218701 | -1.2822 | 1.299e-05 | 1.013e-05 | rel<= 0.0001 (A1 0.0001) | **PASS** |
| egger-reml-ci-high | IV REML random (underlying model) | -0.46690806 | -0.4669 | 8.060e-06 | 1.726e-05 | rel<= 0.000107 (A1 0.0001) | **PASS** |
| egger-k | Egger regression (small_study_effects) | 15 | 15 | 0 | 0.0 | rel<= 0 (A1 0) | **PASS** |
| egger-p-rma | Egger regression (small_study_effects) | 0.00726444 | 0.005 | 0.00226444 | 0.45288704 | rel<= 0.01 (A1 0.01) | **FAIL** |
| egger-p-lm | Egger regression (small_study_effects) | 0.00726444 | 0.0073 | 3.556e-05 | 0.00487189 | rel<= 0.01 (A1 0.01) | **PASS** |
| egger-z | Egger regression test statistic | — |  | — | — | — | **NOT-RUN** |
| egger-limit-estimates | regtest limit estimates (as sei->0) | — |  | — | — | — | **NOT-RUN** |
| egger-tau2-se | SE(tau2)=0.2106 | — |  | — | — | — | **NOT-RUN** |

## CohenKappa

参照：A1 §6.1 Datanovia cohens-kappa-in-r worked example (vcd::Kappa output)

| 检查项 | 方法 | 实测 | 发表 | 绝对偏差 | 相对偏差 | 容差 | 判定 |
|---|---|---|---|---|---|---|---|
| kappa-n | Cohen kappa | 30 | 30 | 0 | 0.0 | rel<= 0 (A1 0) | **PASS** |
| kappa-po | Cohen kappa | 0.73333333 | 0.73333333 | 0.0 | 0.0 | rel<= 0.001 (A1 0.001) | **PASS** |
| kappa-pe | Cohen kappa | 0.23555556 | 0.23555556 | 0.0 | 0.0 | rel<= 0.001 (A1 0.001) | **PASS** |
| kappa-value | Cohen kappa | 0.65116279 | 0.6512 | 3.721e-05 | 5.714e-05 | rel<= 0.001 (A1 0.001) | **PASS** |
| kappa-ase-z-p-ci | Cohen kappa ASE/z/p/CI | — |  | — | — | — | **NOT-RUN** |
| kappa-weighted | Weighted kappa (equal-spacing weights) | — |  | — | — | — | **NOT-RUN** |
| kappa-mchugh | McHugh 2012 formula example (kappa=0.85 from Pr(a)/Pr(e), n=222) | — |  | — | — | — | **NOT-RUN** |

## Senn2013

参照：A1 §7.1 metadat::dat.senn2013 (published values = page's random-effects NMA vs placebo)

| 检查项 | 方法 | 实测 | 发表 | 绝对偏差 | 相对偏差 | 容差 | 判定 |
|---|---|---|---|---|---|---|---|
| senn-fe-qe | NMA fixed-effect GLS (QE statistic) | 96.98406507 | 96.98 | 0.00406507 | 4.192e-05 | rel<= 0.0001 (A1 0.0001) | **PASS** |
| senn-fe-qe-df | NMA fixed-effect GLS (QE statistic) | 18 | 18 | 0 | 0.0 | rel<= 0 (A1 0) | **PASS** |
| senn-fe-estimates | NMA fixed-effect GLS estimates | — | — | — | — | — | **INFO** |
| senn-re-rosiglitazone | NMA random (common tau2 REML) | -1.23373871 | -1.2337 | 3.871e-05 | 3.138e-05 | rel<= 0.0001 (A1 0.0001) | **PASS** |
| senn-re-metformin | NMA random (common tau2 REML) | -1.12840382 | -1.1284 | 3.820e-06 | 3.385e-06 | rel<= 0.0001 (A1 0.0001) | **PASS** |
| senn-re-pioglitazone | NMA random (common tau2 REML) | -1.1294046 | -1.1294 | 4.598e-06 | 4.071e-06 | rel<= 0.0001 (A1 0.0001) | **PASS** |
| senn-re-miglitol | NMA random (common tau2 REML) | -0.94988452 | -0.9499 | 1.548e-05 | 1.630e-05 | rel<= 0.0001 (A1 0.0001) | **PASS** |
| senn-re-acarbose | NMA random (common tau2 REML) | -0.84136425 | -0.8414 | 3.575e-05 | 4.248e-05 | rel<= 0.0001 (A1 0.0001) | **PASS** |
| senn-re-benfluorex | NMA random (common tau2 REML) | -0.73690031 | -0.7369 | 3.139e-07 | 4.260e-07 | rel<= 0.0001 (A1 0.0001) | **PASS** |
| senn-re-vildagliptin | NMA random (common tau2 REML) | -0.7 | -0.7 | 0.0 | 0.0 | rel<= 0.0001 (A1 0.0001) | **PASS** |
| senn-re-sitagliptin | NMA random (common tau2 REML) | -0.57 | -0.57 | 1.110e-16 | 1.948e-16 | rel<= 0.0001 (A1 0.0001) | **PASS** |
| senn-re-sulfonylurea | NMA random (common tau2 REML) | -0.41752773 | -0.4175 | 2.773e-05 | 6.642e-05 | rel<= 0.00012 (A1 0.0001) | **PASS** |
| senn-re-tau2 | NMA random (common tau2 REML) | 0.09991455 | 0.0999 | 1.455e-05 | 0.00014563 | rel<= 0.000501 (A1 0.0001) | **PASS** |
| senn-re-nstudies | NMA random (common tau2 REML) | 26 | 26 | 0 | 0.0 | rel<= 0 (A1 0) | **PASS** |
| senn-re-residual-q | NMA random residual_q (RE-weighted diagnostic) | — | — | — | — | — | **INFO** |
| senn-pscore-rosiglitazone | P-score (lower=better) | 0.89778044 | 0.898 | 0.00021956 | 0.0002445 | rel<= 0.001 (A1 0.001) | **PASS** |
| senn-pscore-placebo | P-score (lower=better) | 0.01200212 | 0.012 | 2.122e-06 | 0.00017679 | rel<= 0.0417 (A1 0.001) | **PASS** |
| senn-db-wls | NMA DB WLS (network_analysis.synthesize) | — | — | — | — | — | **INFO** |
| senn-bayes | Bayesian NMA (gemtc thrombolytics, A1 §7.2) | — |  | — | — | — | **NOT-RUN** |
| senn-netmeta | netmeta graph-theoretic NMA (A1 §7.3) | — |  | — | — | — | **NOT-RUN** |

## FleissLightKappa

参照：A1 §6.2/§6.3 irr vignette + Datanovia; data blocked on irr::diagnoses export

| 检查项 | 方法 | 实测 | 发表 | 绝对偏差 | 相对偏差 | 容差 | 判定 |
|---|---|---|---|---|---|---|---|
| fleiss-6raters | Fleiss kappa (6 raters) | — |  | — | — | — | **NOT-RUN** |
| fleiss-exact | Fleiss kappa exact (irr exact=TRUE) | — |  | — | — | — | **NOT-RUN** |
| fleiss-3raters | Fleiss kappa (first 3 raters subset) | — |  | — | — | — | **NOT-RUN** |
| fleiss-wiki | Fleiss kappa Wikipedia 10x14x5 worked example | — |  | — | — | — | **NOT-RUN** |
| light-6raters | Light kappa (6 raters) | — |  | — | — | — | **NOT-RUN** |
| light-3raters | Light kappa (first 3 raters) | — |  | — | — | — | **NOT-RUN** |

## FAIL 明细（供归因，全部关键数字 8 位）

### dk2007-dl-tau — DK2007 / IV DL random / tau

- 实测 = `0.23235552`；发表 = `0.2323`（A1 §1.3 metafor analyses:dersimonian2007 (declared consistent with D&K 2007 Tables 3-4)）
- 绝对偏差 = `5.552e-05`；相对偏差 = `0.00023898`；生效相对容差 = `0.000215239`（A1 建议 0.0001）
- 备注：inputs are A1's 4-dp yi/vi table; fixture-side independent recomputation reproduces the kernel values exactly (raw.independent_DL_check) — deviation vs the 4-dp published table is input-rounding sized

### dk2007-reml-se — DK2007 / IV REML random / SE(pooled logOR)

- 实测 = `0.13435822`；发表 = `0.1343`（A1 §1.3 metafor analyses:dersimonian2007 (declared consistent with D&K 2007 Tables 3-4)）
- 绝对偏差 = `5.822e-05`；相对偏差 = `0.00043349`；生效相对容差 = `0.000372301`（A1 建议 0.0001）
- 备注：4-dp input rounding propagates; marginal

### egger-p-rma — Egger2001 / Egger regression (small_study_effects) / p (vs regtest rma-version z=-2.8062)

- 实测 = `0.00726444`；发表 = `0.005`（A1 §8 metafor regtest reference page + metadat::dat.egger2001）
- 绝对偏差 = `0.00226444`；相对偏差 = `0.45288704`；生效相对容差 = `0.01`（A1 建议 0.01）
- 备注：kernel regresses y/se on 1/se unweighted (classic Egger 1997 scaled form), not the REML-weighted metafor regtest default

### linde-ee-pooled — Linde2005 / IV fixed (EE) / pooled logOR

- 实测 = `0.32482627`；发表 = `0.33`（A1 §1.4 metafor analyses:viechtbauer2007b (EE/DL same page; meta-regression = Viechtbauer 2007 Table 2 p.113)）
- 绝对偏差 = `0.00517373`；相对偏差 = `0.01567797`；生效相对容差 = `0.0151515`（A1 建议 0.0001）
- 备注：published at 2 dp only (A1 §1.4 tolerance note); computed 0.3248 rounds to 0.32, not 0.33 — independent recomputation in raw agrees with the kernel

### linde-ee-q — Linde2005 / IV fixed (EE) / Q

- 实测 = `51.57066369`；发表 = `51.55`（A1 §1.4 metafor analyses:viechtbauer2007b (EE/DL same page; meta-regression = Viechtbauer 2007 Table 2 p.113)）
- 绝对偏差 = `0.02066369`；相对偏差 = `0.00040085`；生效相对容差 = `0.0001`（A1 建议 0.0001）
- 备注：marginal: gap 0.021 is 4-dp-input-rounding sized (independent recomputation in raw)

### normand-reml-i2-page — Normand1999 / IV REML random / I2 % (page-printed, secondary)

- 实测 = `96.68131052`；发表 = `98.92`（A1 §1.2 metafor analyses:normand1999 (declared consistent with Normand 1999 Table X p.352)）
- 绝对偏差 = `2.23868948`；相对偏差 = `0.02263131`；生效相对容差 = `0.001`（A1 建议 0.001）
- 备注：page value not derivable from the page's own Q=241.06 (df=8); flagged for attribution

### normand-reml-h2-page — Normand1999 / IV REML random / H2 (page-printed, secondary)

- 实测 = `30.13237626`；发表 = `92.26`（A1 §1.2 metafor analyses:normand1999 (declared consistent with Normand 1999 Table X p.352)）
- 绝对偏差 = `62.12762374`；相对偏差 = `0.67339718`；生效相对容差 = `0.001`（A1 建议 0.001）
- 备注：page value not derivable from the page's own Q; flagged for attribution

### rb-reml-i2-page — Raudenbush1985 / IV REML random / I2 % (page-printed, secondary)

- 实测 = `49.76211847`；发表 = `41.86`（A1 §2.2 metafor analyses:raudenbush1985 (declared consistent with R&B 1985 eq.(27))）
- 绝对偏差 = `7.90211847`；相对偏差 = `0.18877493`；生效相对容差 = `0.001`（A1 建议 0.001）
- 备注：page value not derivable from the page's own Q=35.830 (df=18); flagged for attribution

### rb-reml-h2-page — Raudenbush1985 / IV REML random / H2 (page-printed, secondary)

- 实测 = `1.99052979`；发表 = `1.72`（A1 §2.2 metafor analyses:raudenbush1985 (declared consistent with R&B 1985 eq.(27))）
- 绝对偏差 = `0.27052979`；相对偏差 = `0.15728476`；生效相对容差 = `0.00290698`（A1 建议 0.001）
- 备注：page value not derivable from the page's own Q; flagged for attribution

### roth2-ird-se — Rothman2008 / MH IRD (person-time) / SE(IRD)

- 实测 = `3.07772149`；发表 = `3.09`（A1 §3.1 metafor analyses:rothman2008 (Rothman 2008 Tables 15-1/15-2/15-5)）
- 绝对偏差 = `0.01227851`；相对偏差 = `0.00397363`；生效相对容差 = `0.00161812`（A1 建议 0.001）
- 备注：Greenland-Robins SE reference

### roth2-irr-ci90-low — Rothman2008 / MH IRR (person-time) / 90% CI low (derived z90)

- 实测 = `1.19533358`；发表 = `1.19`（A1 §3.1 metafor analyses:rothman2008 (Rothman 2008 Tables 15-1/15-2/15-5)）
- 绝对偏差 = `0.00533358`；相对偏差 = `0.004482`；生效相对容差 = `0.00420168`（A1 建议 0.001）

## NOT-RUN / INFO 清单（原因）

### DK2007

- **NOT-RUN** IV CA (HE) tau2 estimator：A2 §1.1: kernel models are fixed / random(DL) / random_pm / random_reml only; no entry point for this estimator in E:/rf214-ai coscreen
- **NOT-RUN** IV CA2 tau2 estimator：A2 §1.1: kernel models are fixed / random(DL) / random_pm / random_reml only; no entry point for this estimator in E:/rf214-ai coscreen
- **NOT-RUN** IV DL2 tau2 estimator：A2 §1.1: kernel models are fixed / random(DL) / random_pm / random_reml only; no entry point for this estimator in E:/rf214-ai coscreen
- **NOT-RUN** IV EB tau2 estimator：A2 §1.1: kernel models are fixed / random(DL) / random_pm / random_reml only; no entry point for this estimator in E:/rf214-ai coscreen
- **NOT-RUN** IV ML tau2 estimator：A2 §1.1: kernel models are fixed / random(DL) / random_pm / random_reml only; no entry point for this estimator in E:/rf214-ai coscreen
- **NOT-RUN** IV HS tau2 estimator：A2 §1.1: kernel models are fixed / random(DL) / random_pm / random_reml only; no entry point for this estimator in E:/rf214-ai coscreen
- **NOT-RUN** IV SJ tau2 estimator：A2 §1.1: kernel models are fixed / random(DL) / random_pm / random_reml only; no entry point for this estimator in E:/rf214-ai coscreen
- **NOT-RUN** IV SJ2 tau2 estimator：A2 §1.1: kernel models are fixed / random(DL) / random_pm / random_reml only; no entry point for this estimator in E:/rf214-ai coscreen

### Linde2005

- **NOT-RUN** DL meta-regression (dosage x baseline)：A1 §1.4 published the meta-regression but did not transcribe the per-study dosage/baseline covariates; inputs unavailable (fixture must not invent data). Kernel entry exists (coscreen/meta_regression_model.py fit_meta_regression, needs >=10 studies) but cannot run.

### BCG

- **NOT-RUN** trim-and-fill R0 estimator / R0 test：kernel implements L0 only (coscreen/trim_fill.py estimator='L0'); R0 count 4 (SE 3.1623) and R0 test p=0.0312 have no kernel entry
- **NOT-RUN** trim-and-fill RE model (missing=1, adjusted -0.6571) and augmented-RE rerun (-0.3422)：kernel trim_and_fill is fixed-effect only (pooling_method='FE', observed_tau2=0)
- **NOT-RUN** EB (empirical Bayes) pooling + EB meta-regression (A1 §1.5)：kernel has no EB tau2 estimator; A1 §1.5 also notes the Berkey smoothed-variance procedure was not transcribed, so inputs are unavailable anyway
- **NOT-RUN** BCG-specific EE/DL published values：A1 §1.1: metafor publishes only REML/EB for BCG; EE/DL verified on Normand/D&K/Linde instead (EE/DL outputs stored in results JSON for reference)

### Rothman2008

- **NOT-RUN** CMH test (correct=FALSE)：pool_mantel_haenszel returns no test statistic (A2 §2 output fields)
- **NOT-RUN** MH test (correct=FALSE)：no test statistic in pool_mantel_haenszel output
- **NOT-RUN** IRR heterogeneity Q(4)=26.88：pool_mantel_haenszel emits no Q statistic
- **NOT-RUN** CMH test：no test statistic in pool_mantel_haenszel output

### Egger2001

- **NOT-RUN** Egger regression test statistic：small_study_effects returns intercept and p only; no z/t statistic field to compare (z=-2.8062 / t=-3.1783 recorded here)
- **NOT-RUN** regtest limit estimates (as sei->0)：no kernel output corresponds to the limit estimate b (-0.1639 rma / -0.1512 lm)
- **NOT-RUN** SE(tau2)=0.2106：kernel does not expose SE(tau2) (A1 §8 publishes 0.2106)

### CohenKappa

- **NOT-RUN** Cohen kappa ASE/z/p/CI：KappaResult carries kappa/po/pe/n/confusion only — no ASE, z, p or CI field (published: ASE=0.09968, z=6.532, p=6.474e-11, CI [0.4558, 0.8465])
- **NOT-RUN** Weighted kappa (equal-spacing weights)：no weighted-kappa implementation in E:/rf214-ai (published: 0.6331, ASE 0.11939)
- **NOT-RUN** McHugh 2012 formula example (kappa=0.85 from Pr(a)/Pr(e), n=222)：no raw contingency data (only Pr(a)=0.94, Pr(e)=0.57 published); not a kernel input form

### Senn2013

- **INFO** NMA fixed-effect GLS estimates（应比对象：—）：executed; A1 §7.1 publishes no FE vs-placebo estimates (random-effects only), so no verdict assignable; FE estimates stored in raw for attribution
- **INFO** NMA random residual_q (RE-weighted diagnostic)（应比对象：—）：executed; observed 17.438145 (df 18) — this RE-weighted diagnostic is a different statistic from metafor's model-independent QE=96.98 (checked above on the FE GLS path), so no verdict is assigned here
- **INFO** NMA DB WLS (network_analysis.synthesize)（应比对象：—）：executed with the A2 §1.4 DB fixture recipe; multi-arm studies are rejected by this entry, so Willms 1999 was split into two pseudo-studies (placebo arm double-counted, within-study covariance ignored) — a documented approximation, no verdict assignable; outputs stored in raw
- **NOT-RUN** Bayesian NMA (gemtc thrombolytics, A1 §7.2)：A1 §7.2: arm-level data only obtainable via the gemtc R package; no R runtime on this machine and no network access; MCMC posteriors only sanity-level tolerance anyway
- **NOT-RUN** netmeta graph-theoretic NMA (A1 §7.3)：A1 §7.3: contrast-level TE/seTE table and versus-placebo printout not obtained (vignette PDF unparseable in A1 environment); only fragments I2=84%, p=0.0021

### FleissLightKappa

- **NOT-RUN** Fleiss kappa (6 raters)：no R runtime on this machine (Rscript not found) and fixtures must not use the network; irr::diagnoses (30x6 matrix, Fleiss 1971) exists only as an in-package rda binary — only its first 4 rows are public in A1. Data unavailable => not fabricated.; additionally A2 §5: no Fleiss kappa implementation exists in E:/rf214-ai (case-insensitive grep 'fleiss' over *.py = 0 hits)
- **NOT-RUN** Fleiss kappa exact (irr exact=TRUE)：no R runtime on this machine (Rscript not found) and fixtures must not use the network; irr::diagnoses (30x6 matrix, Fleiss 1971) exists only as an in-package rda binary — only its first 4 rows are public in A1. Data unavailable => not fabricated.
- **NOT-RUN** Fleiss kappa (first 3 raters subset)：no R runtime on this machine (Rscript not found) and fixtures must not use the network; irr::diagnoses (30x6 matrix, Fleiss 1971) exists only as an in-package rda binary — only its first 4 rows are public in A1. Data unavailable => not fabricated.
- **NOT-RUN** Fleiss kappa Wikipedia 10x14x5 worked example：A1 §6.2: Wikipedia full table unreachable in the A1 environment; only kappa~=0.21 (0.2099) known, no full input matrix
- **NOT-RUN** Light kappa (6 raters)：no R runtime on this machine (Rscript not found) and fixtures must not use the network; irr::diagnoses (30x6 matrix, Fleiss 1971) exists only as an in-package rda binary — only its first 4 rows are public in A1. Data unavailable => not fabricated.; kernel light_kappa exists (coscreen/stats/kappa.py:147) but the diagnoses matrix is unavailable
- **NOT-RUN** Light kappa (first 3 raters)：no R runtime on this machine (Rscript not found) and fixtures must not use the network; irr::diagnoses (30x6 matrix, Fleiss 1971) exists only as an in-package rda binary — only its first 4 rows are public in A1. Data unavailable => not fabricated.

## 结果文件

- `fixtures/results/normand1999.json`（Normand1999：含被测代码全部原始输出 raw 块，8 位以上精度，供逐位对拍）
- `fixtures/results/dk2007.json`（DK2007：含被测代码全部原始输出 raw 块，8 位以上精度，供逐位对拍）
- `fixtures/results/linde2005.json`（Linde2005：含被测代码全部原始输出 raw 块，8 位以上精度，供逐位对拍）
- `fixtures/results/raudenbush1985.json`（Raudenbush1985：含被测代码全部原始输出 raw 块，8 位以上精度，供逐位对拍）
- `fixtures/results/bcg.json`（BCG：含被测代码全部原始输出 raw 块，8 位以上精度，供逐位对拍）
- `fixtures/results/rothman2008.json`（Rothman2008：含被测代码全部原始输出 raw 块，8 位以上精度，供逐位对拍）
- `fixtures/results/yusuf1985.json`（Yusuf1985：含被测代码全部原始输出 raw 块，8 位以上精度，供逐位对拍）
- `fixtures/results/egger2001.json`（Egger2001：含被测代码全部原始输出 raw 块，8 位以上精度，供逐位对拍）
- `fixtures/results/kappa_cohen.json`（CohenKappa：含被测代码全部原始输出 raw 块，8 位以上精度，供逐位对拍）
- `fixtures/results/senn2013.json`（Senn2013：含被测代码全部原始输出 raw 块，8 位以上精度，供逐位对拍）
- `fixtures/results/kappa_fleiss_light.json`（FleissLightKappa：含被测代码全部原始输出 raw 块，8 位以上精度，供逐位对拍）

## 执行中发现的 A1/A2 文档与实际不符 / 需交接事项

以下为执行期核实的事实（不做算法对错结论，归因留给下一阶段）：

1. **A1 §6.1 Cohen kappa ⚠ 已定向**：A1 手算用 pe=208/900，但打印表的行边际为 (13,10,2,1,4)、列边际为 (7,9,5,5,4)，Σ行×列=13·7+10·9+2·5+1·5+4·4=**212**，正确 pe=212/900=0.235556；由此 κ=(22/30−212/900)/(1−212/900)=**0.651163**，与页面打印 0.6512 一致。A1 的 208 属手算笔误，页面值正确，被测 cohen_kappa 与页面一致（PASS）。
2. **A1 §1.2/§2.2 REML 行的 I²/H² 与本页 Q 不可互推**：Normand 页 REML 行印 I²=98.92%/H²=92.26，但同页 Q(8)=241.06 ⇒ (Q−df)/Q=96.68%、Q/df=30.13；R&B 页印 41.86%/1.72，同页 Q(18)=35.830 ⇒ 49.76%/1.99。两处页面印值彼此自洽（1−1/H²=I²）但都不能由本页 Q 导出，与 A1 §1.1 对 BCG I² 的 ⚠（92.22 vs 92.12）同类。对照：Yusuf 页 H²=0.64 恰等于 Q/df，说明各页 I²/H² 口径不一致（或 A1 转录自不同输出段）。按 A1 重算政策，主判据用 Q 导出值（PASS），页面印值保留为 secondary 行（FAIL，供归因）。被测内核 i2_percent 恒为 (Q−df)/Q。
3. **A1 §7.1 Senn2013 的 escalc 口径已实证**：metadat 示例页的对比方差为 metafor escalc(measure='MD') 默认的**非合并**方差 sd1²/n1+sd2²/n2（用该口径本夹具精确复现 τ²=0.099915、九个 vs 安慰剂估计至 4 位、QE=96.9841）；合并 SD（HO）口径则偏离（原始块 `variant_pooled_sd_ho` 留档）。A1 §1.2 对 Normand 标注 vtype='HO' 同样被实证（HO 得 Q=241.059≈发表 241.06，非合并得 238.916）。两数据集口径不同并非矛盾——是两页各自的分析选择。
4. **A1 §3.2 Yusuf1985 悬案已破（A5 阶段，2026-10-27）**：原 8 项 FAIL 根因为 A1 将 Wilcox 1 治疗组事件数转录为 1，实际为 8（源页 dat.yusuf1985 行 "Wilcox 1 8 259 7 129"，metadat 文档交叉确认）。修正后 Σ(O−E)=−4.855、ΣV=70.13，与发表值反推的 −4.85/70.13 吻合，10 项检查全部 PASS（含 logOR=−0.0692/SE=0.1194/Q(17)=10.8275）。被测 pool_peto 全程正确——修正前后独立教科书重算均与内核逐位一致。
5. **A2 §7 Egger 口径补充**：被测 small_study_effects 为 y/se 对 1/se 的**无权重 OLS**（经典 Egger 1997 缩放式）。其 p（0.007264）与 metafor regtest 的 classic lm 版 （t=−3.1783, p=0.0073）吻合，与默认 rma 版（z=−2.8062, p=0.0050）有 45% 相对差 —— 两行参照均保留在报告中。
6. **产品效应量构造器的口径备注（非错误）**：coscreen.review_analysis.continuous_effect(measure='MD') 的抽样方差为非合并式 sd_t²/n_t+sd_c²/n_c（与 metafor escalc MD 默认一致）；Normand1999 发表分析用的是 vtype='HO' 合并式，故若端到端走产品构造器会得到与发表分析略不同的 vi（本夹具按 A1 指定的 HO 由夹具侧构造 vi，只测合并内核）。
7. **DB 夹具配方补充**：A2 §1.4 样例的 `with _connect(...)` 只提交事务不关闭连接，Windows 下会锁住 db 文件；夹具改用 `contextlib.closing`+事务并已回写 fixtures/common 说明。
8. **Fleiss/Light 数据缺口确认**：本机无 R（Rscript 不存在）、且夹具禁网，irr::diagnoses 30×6 矩阵无法导出（A1 §6.2 仅公开前 4 行）→ Fleiss/Light 全部 NOT-RUN；仓库亦无 Fleiss 实现（A2 §5 已标注，grep 复核为 0 命中）。
