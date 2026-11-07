# A1 参考数据集清单 — 木笔 ReviewFlow 统计模块第三方体检

调研日期：2026-10-27。所有数据均来自公开 URL（见各条目"来源"），未使用任何付费数据库全文。本环境无法访问 GitHub / Wikipedia / jstatsoft 全文 PDF / rdrr.io / RDocumentation 部分页面，相关缺口在文末汇总表中如实标注。

**可信度声明（重要）**
- 每个数据集的逐条数据与"发表值"均来自 metafor / metadat / netmeta / multinma / irr 官方文档或官方分析复现页（这些页面本身声明与原论文数值逐一核对）。
- 两个已知不一致点已用（⚠）标出：① BCG 的 I² 提取值 92.22% 与由发表 Q 精确导出的 92.12% 矛盾（见 1.2）；② Cohen kappa 列联表整数重算 0.6532 与页面打印 0.6512 有 0.002 出入（见 6.1）。造夹具时请以下载原始页面复核为准。
- 调研中已丢弃两处明显的模型幻觉输出（一个 30×6 diagnoses "矩阵"、一个 normand1999 错误表），未收录。

---

## 1. 通用逆方差合并（固定效应 EE + DerSimonian-Laird）与 BCG 经典数据

### 1.1 数据集 A：BCG 疫苗预防结核（Colditz 1994，13 试验）

**逐试验 2×2 数据**（metadat::dat.bcg，列：tpos/tneg = 接种组结核阳/阴；cpos/cneg = 对照组阳/阴；ablat = 研究地绝对纬度）：

| # | author | year | tpos | tneg | cpos | cneg | ablat | alloc |
|---|---|---|---|---|---|---|---|---|
| 1 | Aronson | 1948 | 4 | 119 | 11 | 128 | 44 | random |
| 2 | Ferguson & Simes | 1949 | 6 | 300 | 29 | 274 | 55 | random |
| 3 | Rosenthal et al | 1960 | 3 | 228 | 11 | 209 | 42 | random |
| 4 | Hart & Sutherland | 1977 | 62 | 13536 | 248 | 12619 | 52 | random |
| 5 | Frimodt-Moller et al | 1973 | 33 | 5036 | 47 | 5761 | 13 | alternate |
| 6 | Stein & Aronson | 1953 | 180 | 1361 | 372 | 1079 | 44 | alternate |
| 7 | Vandiviere et al | 1973 | 8 | 2537 | 10 | 619 | 19 | random |
| 8 | TPT Madras | 1980 | 505 | 87886 | 499 | 87892 | 13 | random |
| 9 | Coetzee & Berjak | 1968 | 29 | 7470 | 45 | 7232 | 27 | random |
| 10 | Rosenthal et al | 1961 | 17 | 1699 | 65 | 1600 | 42 | systematic |
| 11 | Comstock et al | 1974 | 186 | 50448 | 141 | 27197 | 18 | systematic |
| 12 | Comstock & Webster | 1969 | 5 | 2493 | 3 | 2338 | 33 | systematic |
| 13 | Comstock et al | 1976 | 27 | 16886 | 29 | 17825 | 33 | systematic |

来源：https://wviechtb.github.io/metadat/reference/dat.bcg.html

**逐试验 logRR（yi）与抽样方差（vi）**（Viechtbauer 2010 JSS 论文中 `escalc(measure="RR")` 的逐行输出，8 位小数，可直接做逐试验效应量计算的夹具）：

| # | 研究 | yi | vi |
|---|---|---|---|
| 1 | Aronson 1948 | -0.88931133 | 0.325584765 |
| 2 | Ferguson & Simes 1949 | -1.58538866 | 0.194581121 |
| 3 | Rosenthal 1960 | -1.34807315 | 0.415367965 |
| 4 | Hart & Sutherland 1977 | -1.44155119 | 0.020010032 |
| 5 | Frimodt-Moller 1973 | -0.21754732 | 0.051210172 |
| 6 | Stein & Aronson 1953 | -0.78611559 | 0.006905618 |
| 7 | Vandiviere 1973 | -1.62089822 | 0.223017248 |
| 8 | TPT Madras 1980 | 0.01195233 | 0.003961579 |
| 9 | Coetzee & Berjak 1968 | -0.46941765 | 0.056434210 |
| 10 | Rosenthal 1961 | -1.37134480 | 0.073024794 |
| 11 | Comstock 1974 | -0.33935883 | 0.012412214 |
| 12 | Comstock & Webster 1969 | 0.44591340 | 0.532505845 |
| 13 | Comstock 1976 | -0.01731395 | 0.071404660 |

来源：https://cris.maastrichtuniversity.nl/ws/files/64205805/viechtbauer_2010_conducting_meta_analyses_in_R.pdf （数据节 Table：yi/vi 输出；本清单已抽验第 1/2/6/7/8/12 行与 2×2 表完全吻合）

**发表参考统计量**：
- REML 随机效应（rma 默认）：合并 logRR = **-0.7145**，95%CI **[-1.0669, -0.3622]**；τ² = **0.3132**；Q(df=12) = **152.2330**（p<.0001）；RR = **0.4894** [0.3441, 0.6962]。来源：metadat dat.bcg 页（同上）。
- ⚠ I²：页面提取值为 92.22%，但由同页 Q=152.2330、df=12 可精确导出 I²=(Q-df)/Q=**92.12%**、H²=Q/df=**12.69**。两者矛盾（疑为提取笔误），夹具以重算为准并复核原页。
- BCG 的固定效应(EE)与 DL 合并的**原文发表值未能找到**（metafor 全系文档对 BCG 只发 REML/EB）；DL 与 EE 改用下列 1.2/1.3/1.4 三个数据集核对（它们都有与原论文逐一核对的发表值）。

**容差建议**：逐试验 yi/vi 相对误差 ≤1e-6（发表 8 位小数）；合并量/τ²/Q 相对 ≤1e-4；I²/H² 相对 ≤1e-3。

### 1.2 数据集 B：Normand 1999 卒中单元（9 研究，均数差 MD）— EE + DL + REML 全套发表值

**逐研究数据**（n1i/m1i/sd1i = 专科照护组例数/均数/标准差；n2i/m2i/sd2i = 常规照护组；yi=MD，抽样方差按合并标准差、vtype="HO"）：

| study | source | n1i | m1i | sd1i | n2i | m2i | sd2i | yi(MD) |
|---|---|---|---|---|---|---|---|---|
| 1 | Edinburgh | 155 | 55 | 47 | 156 | 75 | 64 | -20 |
| 2 | Orpington-Mild | 31 | 27 | 7 | 32 | 29 | 4 | -2 |
| 3 | Orpington-Moderate | 75 | 64 | 17 | 71 | 119 | 29 | -55 |
| 4 | Orpington-Severe | 18 | 66 | 20 | 18 | 137 | 48 | -71 |
| 5 | Montreal-Home | 8 | 14 | 8 | 13 | 18 | 11 | -4 |
| 6 | Montreal-Transfer | 57 | 19 | 7 | 52 | 18 | 4 | 1 |
| 7 | Newcastle | 34 | 52 | 45 | 33 | 41 | 34 | 11 |
| 8 | Umea | 110 | 21 | 16 | 183 | 31 | 27 | -10 |
| 9 | Uppsala | 60 | 30 | 27 | 52 | 23 | 20 | 7 |

**发表参考统计量**（metafor 分析页声明与 Normand 1999 原文 Table X, p.352 一致）：
- 固定效应（EE）：估计 **-3.49**（SE 0.78），95%CI **[-5.03, -1.96]**；Q(df=8) = **241.06**（p<.01）；I² = **96.68%**。
- DL 随机效应：τ² = **218.72**（SE 195.47），τ = **14.79**；估计 **-14.10**（SE 5.28），95%CI **[-24.45, -3.75]**。
- REML 随机效应：τ² = **685.20**（SE 360.37），τ = **26.18**，I² = **98.92%**，H² = **92.26**；估计 **-15.12**（SE 8.95），p = **0.09**，95%CI **[-32.67, 2.43]**。
- 同页附带：Hine 1989 利多卡因 2×2（RD）：估计 **2.94**（SE 1.31），CI **[0.38, 5.51]**，与原文 Table VII p.349 一致（逐格数据见来源页，本清单未抄全）。

来源：https://www.metafor-project.org/doku.php/analyses:normand1999 （数据表经 https://wviechtb.github.io/metadat/reference/dat.normand1999.html 双重核对）

**容差建议**：点估计/SE/CI/τ²/Q 相对 ≤1e-4；p 值相对 ≤1e-2（统计量本身 ≤1e-4）。

### 1.3 数据集 C：DerSimonian & Kacker 2007（CLASP 资料拆层，6 组，logOR）— 多 τ² 估计量发表值

**逐组数据**（ai/n1i、ci/n2i 为事件数/样本量；yi/vi 为 logOR 及其方差）：

| # | n1i | n2i | ai | ci | yi | vi |
|---|---|---|---|---|---|---|
| 1 | 156 | 74 | 5 | 8 | -1.2976 | 0.3468 |
| 2 | 303 | 303 | 5 | 17 | -1.2649 | 0.2657 |
| 3 | 565 | 477 | 12 | 9 | 0.1208 | 0.1984 |
| 4 | 1570 | 1565 | 69 | 94 | -0.3294 | 0.0265 |
| 5 | 103 | 105 | 9 | 11 | -0.2007 | 0.2233 |
| 6 | 4659 | 4650 | 313 | 352 | -0.1285 | 0.0065 |

**发表参考统计量**（页面声明 PM/CA/DL/CA2/DL2 与 D&K 2007 论文 Tables 3-4 完全一致；τ=sqrt(τ²)）：

| 估计量 | τ | μ | SE(μ) |
|---|---|---|---|
| PM | 0.3681 | -0.3811 | 0.2060 |
| CA (HE) | 0.4410 | -0.4035 | 0.2327 |
| **DL** | **0.2323** | **-0.3240** | **0.1540** |
| CA2 | 0.3831 | -0.3861 | 0.2115 |
| DL2 | 0.3254 | -0.3655 | 0.1901 |
| EB | 0.3681 | -0.3811 | 0.2060 |
| ML | 0.0023 | -0.1974 | 0.0694 |
| REML | 0.1843 | -0.2980 | 0.1343 |
| HS | 0.1330 | -0.2666 | 0.1125 |
| SJ | 0.4572 | -0.4079 | 0.2386 |
| SJ2 | 0.4084 | -0.3941 | 0.2208 |

（调研员手算校验：由上表 yi/vi 精确复得 τ_DL=0.2323、μ_ML=-0.1974，与发表值吻合，数据可信。）

来源：https://www.metafor-project.org/doku.php/analyses:dersimonian2007

**容差建议**：τ/μ/SE 相对 ≤1e-4。

### 1.4 数据集 D：Viechtbauer 2007 / Linde 2005 子集（17 研究，logOR，圣约翰草）— EE + DL + DL 元回归

**逐研究数据**（ai/n1i/ci/n2i 与 yi/vi）：

| # | ai | n1i | ci | n2i | yi | vi |
|---|---|---|---|---|---|---|
| 1 | 20 | 25 | 11 | 25 | 0.5978 | 0.0609 |
| 2 | 14 | 20 | 9 | 20 | 0.4418 | 0.0825 |
| 3 | 4 | 25 | 2 | 25 | 0.6931 | 0.6700 |
| 4 | 20 | 32 | 6 | 33 | 1.2347 | 0.1551 |
| 5 | 28 | 50 | 13 | 55 | 0.8626 | 0.0745 |
| 6 | 34 | 48 | 25 | 49 | 0.3281 | 0.0282 |
| 7 | 35 | 53 | 12 | 54 | 1.0891 | 0.0745 |
| 8 | 24 | 49 | 16 | 49 | 0.4055 | 0.0634 |
| 9 | 45 | 80 | 12 | 79 | 1.3092 | 0.0804 |
| 10 | 67 | 106 | 22 | 47 | 0.3004 | 0.0297 |
| 11 | 34 | 60 | 17 | 59 | 0.6763 | 0.0546 |
| 12 | 46 | 70 | 34 | 70 | 0.3023 | 0.0226 |
| 13 | 55 | 123 | 57 | 124 | -0.0276 | 0.0195 |
| 14 | 23 | 37 | 15 | 35 | 0.3719 | 0.0545 |
| 15 | 26 | 98 | 19 | 102 | 0.3537 | 0.0711 |
| 16 | 46 | 113 | 56 | 116 | -0.1705 | 0.0221 |
| 17 | 98 | 186 | 80 | 189 | 0.2189 | 0.0120 |

**发表参考统计量**：
- EE（k=17）：估计 **0.33**（SE 0.05），z=6.78，CI **[0.23, 0.42]**；Q(df=16) = **51.55**（p<.01）；I² = **68.96%**，H² = **3.22**。
- DL 随机效应：τ² = **0.09**（SE 0.05），τ = **0.30**；估计 **0.45**（SE 0.09），z=4.87，CI **[0.27, 0.63]**；OR 尺度 1.57 [1.31, 1.89]。
- DL 元回归（mods = 剂量×基线，页面声明与 Viechtbauer 2007 原文 Table 2 p.113 一致）：残差 τ² = **0.0475**（SE 0.0376），I² = **53.56%**，H² = **2.15**，R² = **47.38%**；QE(13) = **27.9903**（p=0.0091），QM(3) = **10.1280**（p=0.0175）；系数：intrcpt **0.4763**（0.0876）、dosage **-0.0058**（0.0100）、baseline **-0.0672**（0.0352）、交互 **-0.0016**（0.0034）。

来源：https://www.metafor-project.org/doku.php/analyses:viechtbauer2007b

**容差建议**：同 1.2；页面保留 2 位小数的量（0.33/0.45 等）只适合做 0.01 级比对，建议以其 4 位小数的 τ²/QE/QM 与系数为准。

### 1.5 附：BCG 的 EB（经验贝叶斯）发表值（Berkey 1995 论文复现）

- EB 随机效应（rma method="EB"，注意页面用 Berkey 式"平滑方差"替换 escalc 方差）：τ² = **0.2682**（SE 0.1801）；I² = **87.49%**，H² = **7.99**；Q(12) = **85.8625**（p<.0001，Q 随方差平滑而变）；合并 logRR = **-0.5429**（SE 0.1842），CI **[-0.9040, -0.1819]**。页面声明与 Berkey et al. 1995 原文一致。
- EB 元回归（纬度中心化 33.46°）：残差 τ² = **0.1572**（0.1262），I² = **77.42%**，R² = **41.38%**；QE(11) = **35.8827**（p=0.0002），QM(1) = **5.9018**（p=0.0151）；intrcpt **-0.6303**（0.1591），ablat **-0.0268**（0.0110）。

来源：https://www.metafor-project.org/doku.php/analyses:berkey1995 （复现注意事项：vi 需按该页用平滑方差，夹具须先抄录该页的平滑步骤）

---

## 2. REML τ² 专项

已拿到三套带发表值的 REML：
1. Normand 1999（见 1.2）：REML τ² = **685.20**（SE 360.37），μ = -15.12（8.95）；页面注明 metafor 与 SAS 的 τ² SE 差异（期望 Hessian 360.37 vs 观测 Hessian 380）——体检时以 360.37 为准。
2. Raudenbush & Bryk 1985（教师期望，19 研究 SMD，**教科书级 REML 例**）：

**逐研究 yi/vi**（标准化均数差及方差；方差=原文献 SE 的平方）：

| study | author | year | weeks | setting | tester | n1i | n2i | yi | vi |
|---|---|---|---|---|---|---|---|---|---|
| 1 | Rosenthal et al. | 1974 | 2 | group | aware | 77 | 339 | 0.0300 | 0.0156 |
| 2 | Conn et al. | 1968 | 21 | group | aware | 60 | 198 | 0.1200 | 0.0216 |
| 3 | Jose & Cody | 1971 | 19 | group | aware | 72 | 72 | -0.1400 | 0.0279 |
| 4 | Pellegrini & Hicks | 1972 | 0 | group | aware | 11 | 22 | 1.1800 | 0.1391 |
| 5 | Pellegrini & Hicks | 1972 | 0 | group | blind | 11 | 22 | 0.2600 | 0.1362 |
| 6 | Evans & Rosenthal | 1969 | 3 | group | aware | 129 | 348 | -0.0600 | 0.0106 |
| 7 | Fielder et al. | 1971 | 17 | group | blind | 110 | 636 | -0.0200 | 0.0106 |
| 8 | Claiborn | 1969 | 24 | group | aware | 26 | 99 | -0.3200 | 0.0484 |
| 9 | Kester | 1969 | 0 | group | aware | 75 | 74 | 0.2700 | 0.0269 |
| 10 | Maxwell | 1970 | 1 | indiv | blind | 32 | 32 | 0.8000 | 0.0630 |
| 11 | Carter | 1970 | 0 | group | blind | 22 | 22 | 0.5400 | 0.0912 |
| 12 | Flowers | 1966 | 0 | group | blind | 43 | 38 | 0.1800 | 0.0497 |
| 13 | Keshock | 1970 | 1 | indiv | blind | 24 | 24 | -0.0200 | 0.0835 |
| 14 | Henrikson | 1970 | 2 | indiv | blind | 19 | 32 | 0.2300 | 0.0841 |
| 15 | Fine | 1972 | 17 | group | aware | 80 | 79 | -0.1800 | 0.0253 |
| 16 | Grieger | 1970 | 5 | group | blind | 72 | 72 | -0.0600 | 0.0279 |
| 17 | Rosenthal & Jacobson | 1968 | 1 | group | aware | 65 | 255 | 0.3000 | 0.0193 |
| 18 | Fleming & Anttonen | 1971 | 2 | group | blind | 233 | 224 | 0.0700 | 0.0088 |
| 19 | Ginsburg | 1970 | 7 | group | aware | 65 | 67 | -0.0700 | 0.0303 |

**发表参考统计量**（页面声明与 R&B 1985 论文式(27) 的受限似然结果一致）：REML τ² = **0.019**（SE 0.016）；I² = **41.86%**，H² = **1.72**；Q(df=18) = **35.830**（p=0.007）；μ = **0.084**（SE 0.052），z = 1.621（p = 0.105），CI **[-0.018, 0.185]**。元回归（先前接触周数，截断 3 周，REML）：残差 τ² = **0.000**；QE(17) = **16.571**（p=0.484），QM(1) = **19.258**（p<.001）；intrcpt **0.407**（0.087），斜率 **-0.157**（0.036）。

来源：https://www.metafor-project.org/doku.php/analyses:raudenbush1985 ；数据表经 https://wviechtb.github.io/metadat/reference/dat.raudenbush1985.html 核对

3. D&K 2007（见 1.3）：REML τ = **0.1843**（即 τ²≈0.0340），μ = -0.2980（0.1343）。

**容差建议**：τ² 相对 ≤1e-4（Normand/R&B 发表位数足够）；SE(τ²) 相对 ≤1e-3。

---

## 3. Mantel-Haenszel OR/RR（含方差公式结果）与 Peto OR

### 3.1 MH 三例（Rothman 2008《Modern Epidemiology》数值例，metafor 官方复现页）

**例 1：Tolbutamide 试验（Table 15-1, p.260），2 分层 2×2**（行=Age<55/Age 55+；顺序 ai, bi, ci, di = 治疗-死亡/治疗-存活/安慰剂-死亡/安慰剂-存活）：
- Age<55：8, 98, 5, 115
- Age 55+：22, 76, 16, 69

发表值：MH **RD = 0.035**（SE 0.032），90%CI **(-0.018, 0.087)**；MH **logRR = 0.28**（SE 0.26），**RR = 1.33**，90%CI **(0.87, 2.03)**；CMH 检验（correct=FALSE）**χ² = 1.19**，df=1，p = **0.28**（书中 χ=1.09）。

**例 2：英国医生吸烟（Table 15-2, p.264），5 分层人时资料**（x1i/t1i 吸烟组死亡数/人年、x2i/t2i 非吸烟组；人年已除以 10000，率按每万人年）：
| 层 | x1i | t1i | x2i | t2i |
|---|---|---|---|---|
| 1 | 32 | 5.2407 | 2 | 1.8790 |
| 2 | 104 | 4.3248 | 12 | 1.0673 |
| 3 | 206 | 2.8612 | 28 | 0.5710 |
| 4 | 186 | 1.2663 | 28 | 0.2585 |
| 5 | 102 | 0.5317 | 31 | 0.1462 |

发表值：MH **IRD = 11.44**（SE 3.09），90%CI **(6.35, 16.53)**（每万人年），Q(4) = **26.88**；MH **logIRR = 0.35**（SE 0.11），**IRR = 1.42**，90%CI **(1.19, 1.70)**；MH 检验（correct=FALSE）**11.0162**，df=1，p = **0.0009**（书值 10.9995，差异属舍入）。

**例 3：Down 综合征/杀精剂（Table 15-5, p.276），2 分层 2×2**：
- 年龄<35：ai=3, bi=9, ci=104, di=1059
- 年龄 35+：ai=1, bi=3, ci=5, di=86

发表值：MH **logOR = 1.33**（SE 0.59），**OR = 3.78**，90%CI **(1.43, 10.00)**；CMH = **5.81**，df=1，p = **0.02**（书中 χ=2.41 的平方）。

来源：https://www.metafor-project.org/doku.php/analyses:rothman2008 （含 Rothman 书页码 260/264/274-278；MH 方差公式/ Greenland-Robins 结果即上述 SE）

**容差建议**：log 尺度估计与 SE 相对 ≤1e-4；率差/比例差等绝对量级数值相对 ≤1e-3（发表值仅 2-3 位小数，可用例 2 的 5 层精确输入校验更低位数）；CMH/MH 检验统计量 ≤1e-4。

### 3.2 Peto OR：Yusuf 1985 β阻滞剂（Table 6，22 项比较）

**逐比较数据**（ai/n1i = 治疗组事件/总数；ci/n2i = 对照组）：

| # | trial | ai | n1i | ci | n2i |
|---|---|---|---|---|---|
| 1 | Balcon | 14 | 56 | 15 | 58 |
| 2 | Clausen | 18 | 66 | 19 | 64 |
| 3 | Multicentre | 15 | 100 | 12 | 95 |
| 4 | Barber | 10 | 52 | 12 | 47 |
| 5 | Norris | 21 | 226 | 24 | 228 |
| 6 | Kahler | 3 | 38 | 6 | 31 |
| 7 | Ledwich | 2 | 20 | 3 | 20 |
| 8 | Snow 1 | NA | NA | NA | NA |
| 9 | Snow 2 | 19 | 76 | 15 | 67 |
| 10 | Fuccella | 15 | 106 | 9 | 114 |
| 11 | Briant | 5 | 62 | 4 | 57 |
| 12 | Pitt | 0 | 9 | 0 | 8 |
| 13 | Lombardo | 8 | 133 | 11 | 127 |
| 14 | Thompson | 3 | 48 | 3 | 49 |
| 15 | Hutton | 0 | 16 | 0 | 13 |
| 16 | Tonkin | 1 | 42 | 1 | 46 |
| 17 | Gupta | 0 | 25 | 3 | 25 |
| 18 | Barber | 14 | 221 | 15 | 228 |
| 19 | Yusuf | 0 | 11 | 0 | 11 |
| 20 | Wilcox 1 | 8 | 259 | 7 | 129 |
| 21 | Wilcox 2 | 6 | 157 | 4 | 158 |
| 22 | CPRG | 3 | 177 | 2 | 136 |

**发表参考统计量**（`rma(ai, n1i, ci, n2i, measure="PETO")`；与 `escalc(measure="PETO", add=0)` + `rma(method="EE")` 两条路径结果完全相同；与 Yusuf 1985 原文 Table 6 下方数值（p.343）一致）：
- 合并 logOR = **-0.0692**（SE **0.1194**），z = **-0.5794**，p = **0.5623**，95%CI **[-0.3031, 0.1648]**；OR = **0.93** [0.74, 1.18]。
- 异质性：I² = **0.00%**，H² = **0.64**，**Q(df=17) = 10.8275**（p = **0.8654**）。
- 口径注意：22 行中 Snow 1 全 NA 剔除；Pitt/Hutton/Yusuf 双臂零事件——`escalc(add=0)` 路径下被剔除（k=18，df=17）；默认 `rma(measure="PETO")` 因连续性校正纳入（k=21），两种口径合并值相同。Peto 适用条件提示：Wilcox 1 组比 2.01 失衡，原页有警示。

来源：https://www.metafor-project.org/doku.php/analyses:yusuf1985

**容差建议**：估计/SE/Q 相对 ≤1e-4；p 相对 ≤1e-2。

---

## 4. Q / I² / τ² 异质性统计量

随上述数据集复用即可，逐项发表值集中记录：
- BCG logRR：Q(12)=152.2330（p<.0001）；REML τ²=0.3132；EB τ²=0.2682；I² 见 1.1 ⚠ 注。
- Normand MD：Q(8)=241.06；I²=96.68%；DL τ²=218.72；REML τ²=685.20（I²=98.92%，H²=92.26）。
- Raudenbush SMD：Q(18)=35.830（p=0.007）；I²=41.86%；H²=1.72；REML τ²=0.019。
- Linde17 logOR：Q(16)=51.55；I²=68.96%；H²=3.22；DL τ²=0.09。
- Yusuf Peto：Q(17)=10.8275（p=0.8654）；I²=0.00%；H²=0.64。
- D&K2007 logOR（DL τ²=0.2323²≈0.0540）。
**容差建议**：Q/τ² 相对 ≤1e-4；I²/H² 相对 ≤1e-3；Q 的 p 值相对 ≤1e-2（或用 Q 统计量严格比对）。

---

## 5. 剪补法（trim-and-fill，BCG）

输入数据 = 1.1 的 BCG logRR。**发表参考统计量**（metafor 官方 trimfill 文档示例）：
- EE 模型：估计右侧缺失 **4** 项（SE 2.3853）；合并后 EE（k=17）估计 **-0.2910**，95%CI **[-0.3660, -0.2160]**（p<.0001）；R0 估计量同样 4 项（SE 3.1623），R0 检验 p = **0.0312**。
- RE 模型（REML）：估计缺失 **1** 项（SE 2.4528）；合并后（k=14）估计 **-0.6571**，95%CI **[-1.0070, -0.3072]**（p=0.0002）。
- 增广数据（13 观测 + 4 填充）重跑 RE：估计 **-0.3422**，95%CI **[-0.7780, 0.0936]**（p=0.1237）。

来源：https://wviechtb.github.io/metafor/reference/trimfill.html

**容差建议**：缺失研究数必须完全一致（整数）；合并量相对 ≤1e-4。

---

## 6. 三种 Kappa（Cohen / Fleiss / Light）

### 6.1 Cohen kappa — 完整（数据 + 发表值）

**数据**：两位医生对 30 例患者的五分类精神诊断列联表（行=医生1，列=医生2；类别序：Depression, Personality Disorder, Schizophrenia, Neurosis, Other）：

| | Dep | PD | Schizo | Neuro | Other |
|---|---|---|---|---|---|
| **Depression** | 7 | 1 | 2 | 3 | 0 |
| **Pers. Disorder** | 0 | 8 | 1 | 1 | 0 |
| **Schizophrenia** | 0 | 0 | 2 | 0 | 0 |
| **Neurosis** | 0 | 0 | 0 | 1 | 0 |
| **Other** | 0 | 0 | 0 | 0 | 4 |

**发表参考统计量**（同页 vcd::Kappa 输出）：
- 未加权 κ = **0.6512**，ASE = **0.09968**，z = **6.532**，p = **6.474e-11**，95%CI **[0.4558, 0.8465]**。
- 加权 κ（vcd 默认等距权重）= **0.6331**，ASE = **0.11939**，z = **5.303**，p = **1.140e-07**，95%CI **[0.3991, 0.8671]**。
- ⚠ 调研员整数重算：po=22/30=0.73333，pe=208/900=0.23111，κ=452/692=**0.65318**，与页面打印 0.6512 差 0.002（疑页面表或输出微异）。造夹具时以本表重算并复核原页。

**第二来源（系数+SE 公式例，无原始格）**：McHugh 2012：Pr(a)=0.94，Pr(e)=0.57，n=222 → κ = **0.85**，SE = **0.037**，95%CI **[0.78, 0.92]**。
来源：https://www.datanovia.com/learn/biostatistics/inter-rater-reliability/cohens-kappa-in-r/ ；https://pmc.ncbi.nlm.nih.gov/articles/PMC3900052/

**容差建议**：κ/ASE/z 相对 ≤1e-3（发表值 3-4 位）；CI ≤1e-3。

### 6.2 Fleiss kappa — 发表值完整，数据矩阵需从 irr 包导出（部分）

**发表参考统计量**（irr 官方 vignette，数据=Fleiss 1971 原文的 30 例×6 评定员精神诊断 `diagnoses`）：
- `kappam.fleiss(diagnoses)`：Subjects=30，Raters=6，**κ = 0.430**，z = **2.533**，p = **0.011**。
- 同 vignette 另有 `kappam.fleiss(diagnoses, exact=TRUE)` 的精确 kappa = **0.428**（z=2.521，p=0.012）。
- 教程页补充（前 3 个评定员子集）：κ = **0.534**，z = **9.89**，p = **0**；分类别 κ：Depression **0.416**（z 3.946）、PD **0.591**（5.608）、Schizophrenia **0.577**（5.475）、Neurosis **0.236**（2.240, p 0.025）、Other **1.000**（9.487）。
- 数据障碍：`diagnoses` 的 30×6 原始矩阵只存在于 irr 包二进制数据（rda）；本环境 GitHub 不可达，公开网页仅打印前 4 行（1:4/4/4, 2:2/2/2, 3:2/3/3, 4:5/5/5，按 1-5 编码）。**夹具制作时用 R 导出**：`data(diagnoses, package="irr"); write.csv(diagnoses, ...)`。
- 另一经典例：Wikipedia「Fleiss' kappa」工作例（10 项×14 评定员×5 类别，含全同意行 0/0/0/0/14，κ≈**0.21**；多个教程复算 0.2099）——本环境无法访问 Wikipedia 全文，仅记录 URL 供后续人工核对：https://en.wikipedia.org/wiki/Fleiss%27_kappa

来源：https://cran.r-project.org/web/packages/irr/vignettes/irr.pdf ；https://www.datanovia.com/learn/biostatistics/inter-rater-reliability/fleiss-kappa-in-r/

### 6.3 Light kappa — 发表值完整，数据同上（部分）

- `kappam.light(diagnoses)`（6 评定员，成对 Cohen κ 均值）：**κ = 0.378**，z = **2.622**，p = **0.009**（irr vignette）。
- 前 3 评定员子集：κ = **0.555**（Datanovia；z/p 显示 NaN）。
- 数据障碍同 6.2（需从 irr 包导出 diagnoses）。

来源：https://cran.r-project.org/web/packages/irr/vignettes/irr.pdf ；https://www.datanovia.com/learn/biostatistics/inter-rater-reliability/fleiss-kappa-in-r/

---

## 7. 网络 Meta 分析（NMA）

### 7.1 首选：Senn 2013 糖尿病网络（26 试验、10 治疗）— metadat::dat.senn2013 臂级全表 + metafor rma.mv NMA 发表值

**逐臂数据（53 行）**（study | ni | treatment | comment | mi | sdi；comment 指结局为 change=治疗前后变化 或 raw=终点值）：

```
De Fronzo (1995) | 213 | metformin | change | -1.70 | 1.459
De Fronzo (1995) | 209 | placebo | change | 0.20 | 1.446
Lewin (2007) | 431 | metformin | change | -0.74 | 1.106
Lewin (2007) | 144 | placebo | change | 0.08 | 1.004
Willms (1999) | 29 | metformin | change | -2.50 | 0.862
Willms (1999) | 31 | acarbose | change | -2.30 | 1.782
Willms (1999) | 29 | placebo | change | -1.30 | 1.831
Davidson (2007) | 117 | rosiglitazone | change | -1.20 | 1.097
Davidson (2007) | 116 | placebo | change | 0.14 | 1.093
Wolffenbuttel (1999) | 183 | rosiglitazone | change | -0.90 | 1.100
Wolffenbuttel (1999) | 192 | placebo | change | 0.20 | 1.110
Kipnes (2001) | 182 | pioglitazone | change | -1.20 | 1.369
Kipnes (2001) | 181 | placebo | change | 0.10 | 1.024
Kerenyi (2004) | 160 | rosiglitazone | change | -0.91 | 0.990
Kerenyi (2004) | 154 | placebo | change | -0.14 | 0.920
Hanefeld (2004) | 319 | pioglitazone | raw | 7.61 | 1.072
Hanefeld (2004) | 320 | metformin | raw | 7.45 | 1.073
Derosa (2004) | 45 | pioglitazone | raw | 6.80 | 0.800
Derosa (2004) | 42 | rosiglitazone | raw | 6.70 | 0.900
Baksi (2004) | 218 | rosiglitazone | change | -1.20 | 1.112
Baksi (2004) | 233 | placebo | change | 0.10 | 1.036
Rosenstock (2008) | 59 | rosiglitazone | change | -1.17 | 1.229
Rosenstock (2008) | 57 | placebo | change | -0.08 | 1.208
Zhu (2003) | 210 | rosiglitazone | change | -1.90 | 1.470
Zhu (2003) | 105 | placebo | change | -0.40 | 1.300
Yang (2003) | 102 | rosiglitazone | change | -1.09 | 1.650
Yang (2003) | 96 | metformin | change | -0.95 | 1.500
Vongthavaravat (2002) | 164 | rosiglitazone | change | -1.10 | 1.559
Vongthavaravat (2002) | 170 | sulfonylurea | change | 0.10 | 0.992
Oyama (2008) | 41 | acarbose | change | -0.70 | 0.800
Oyama (2008) | 43 | sulfonylurea | change | -0.30 | 0.600
Costa (1997) | 36 | acarbose | change | -1.10 | 0.360
Costa (1997) | 29 | placebo | change | -0.30 | 0.700
Hermansen (2007) | 106 | sitagliptin | change | -0.30 | 0.940
Hermansen (2007) | 106 | placebo | change | 0.27 | 0.940
Garber (2008) | 132 | vildagliptin | change | -0.63 | 1.034
Garber (2008) | 144 | placebo | change | 0.07 | 1.080
Alex (1998) | 291 | metformin | change | 0.13 | 1.428
Alex (1998) | 300 | sulfonylurea | change | 0.50 | 1.450
Johnston (1994) | 68 | miglitol | change | -0.41 | 1.072
Johnston (1994) | 63 | placebo | change | 0.33 | 1.032
Johnston (1998a) | 91 | miglitol | change | -0.43 | 0.954
Johnston (1998a) | 43 | placebo | change | 0.98 | 1.311
Kim (2007) | 57 | rosiglitazone | change | -1.10 | 1.341
Kim (2007) | 56 | metformin | change | -1.10 | 1.139
Johnston (1998b) | 49 | miglitol | change | -0.12 | 1.400
Johnston (1998b) | 34 | placebo | change | 0.56 | 1.166
Gonzalez-Ortiz (2004) | 34 | metformin | change | -1.30 | 1.861
Gonzalez-Ortiz (2004) | 37 | placebo | change | -0.90 | 1.803
Stucci (1996) | 28 | benfluorex | raw | 8.03 | 1.290
Stucci (1996) | 30 | placebo | raw | 8.26 | 1.350
Moulin (2006) | 161 | benfluorex | change | -0.82 | 1.028
Moulin (2006) | 156 | placebo | change | 0.19 | 1.374
```

**发表参考统计量**（metadat 文档示例页的对比基线（contrast-based）随机效应 NMA，相对安慰剂的合并 MD；含 τ²、QE 与 P-score）：
- rosiglitazone **-1.2337**（p<.0001）、metformin **-1.1284**、pioglitazone **-1.1294**、miglitol **-0.9499**、acarbose **-0.8414**、benfluorex **-0.7369**、vildagliptin **-0.7000**、sitagliptin **-0.5700**（p=.095）、sulfonylurea **-0.4175**（p=.073）。
- τ² = **0.0999**；QE(18) = **96.98**（p<.0001）。
- P-score：rosiglitazone 最高 **0.898**，安慰剂最低 **0.012**。

来源：https://wviechtb.github.io/metadat/reference/dat.senn2013.html （该页同时给出 rma.mv/yi/vi 的构造代码，夹具务必按其 escalc→to.wide→contrmat 流程复现）

**容差建议**：NMA 点估计/τ²/QE 相对 ≤1e-4（frequentist 确定性计算）；P-score 相对 ≤1e-3。

### 7.2 备选：溶栓剂网络（gemtc::thrombolytics / multinma；50 研究、9 治疗，二分类）— 贝叶斯一致性/不一致发表值

**数据**：`data(thrombolytics)`（gemtc 包，44 行 arm-based：study/treatment/responders/sampleSize；multinma 版本 50 研究，参照治疗 SK）。源文献 Dias et al. 2010（doi:10.1002/sim.3767）。**逐行数据表在公开网页无文本版，需 R 包导出。**

**发表参考统计量**（multinma 官方示例页，FE 一致性模型、binomial/logit，logOR vs SK）：
- Acc t-PA **-0.18** [-0.26, -0.09]；ASPAC **0.02** [-0.06, 0.09]；PTCA **-0.48** [-0.68, -0.29]；r-PA **-0.12** [-0.24, -0.01]；SK+t-PA **-0.05** [-0.14, 0.04]；t-PA **0.00** [-0.05, 0.06]；TNK **-0.17** [-0.33, -0.02]；UK **-0.20** [-0.62, 0.24]。
- 拟合：残差偏差 **105.9**（102 数据点），pD **58.7**，DIC **164.6**。
- 不一致：UME 模型 DIC 165.4；节点拆分（14 项）最显著为 ASPAC vs Acc t-PA：直接 **1.41** [0.63, 2.29]、间接 **0.16** [0.05, 0.28]、ω **1.25** [0.44, 2.15]，贝叶斯 p **<0.01**；其余拆分 p 在 0.096–0.9。

来源：https://dmphillippo.github.io/multinma/articles/example_thrombolytics.html ；网络与数据出处描述另见 https://pmc.ncbi.nlm.nih.gov/articles/PMC11487772

**容差建议**：MCMC 后验均值与 CrI 不能按 1e-4 卡：均值差 |Δ|≤0.01–0.02、CrI 端点 |Δ|≤0.03 仅作 sanity；严格比对应落在 7.1 的 frequentist 数据集上。

### 7.3 附注：netmeta 图论法（Senn2013 同源数据）

netmeta 包 Senn2013（对比级 TE/seTE，26 行）是确定性 frequentist NMA 的标准夹具，但本环境未能取到逐行 TE/seTE 表与打印的"versus placebo"输出（vignette PDF 无法解析、RPubs 有登录墙）。已核对到的发表值片段：全网络 I² = **84%**；设计间异质性 p = **0.0021**；单研究 Q 最大 DeFronzo1995 = **30.89**（IntechOpen 章节）。来源：https://www.intechopen.com/chapters/80086 ；数据文档 https://search.r-project.org/CRAN/refmans/netmeta/html/Senn2013.html

---

## 8. 发表偏倚检验：Egger 回归检验（含数据表）；Peters 未获取

**数据集：dat.egger2001（镁制剂心肌梗死 16 试验，2×2；Egger 2001）**：

| id | study | year | ai | n1i | ci | n2i |
|---|---|---|---|---|---|---|
| 1 | Morton | 1984 | 1 | 40 | 2 | 36 |
| 2 | Rasmussen | 1986 | 9 | 135 | 23 | 135 |
| 3 | Smith | 1986 | 2 | 200 | 7 | 200 |
| 4 | Abraham | 1987 | 1 | 48 | 1 | 46 |
| 5 | Feldstedt | 1988 | 10 | 150 | 8 | 148 |
| 6 | Shechter | 1989 | 1 | 59 | 9 | 56 |
| 7 | Ceremuzynski | 1989 | 1 | 25 | 3 | 23 |
| 8 | Bertschat | 1989 | 0 | 22 | 1 | 21 |
| 9 | Singh | 1990 | 6 | 76 | 11 | 75 |
| 10 | Pereira | 1990 | 1 | 27 | 7 | 27 |
| 11 | Shechter | 1991 | 2 | 89 | 12 | 80 |
| 12 | Golf | 1991 | 5 | 23 | 13 | 33 |
| 13 | Thogersen | 1991 | 4 | 130 | 8 | 122 |
| 14 | LIMIT-2 | 1992 | 90 | 1159 | 118 | 1157 |
| 15 | Shechter | 1995 | 4 | 107 | 17 | 108 |
| 16 | ISIS-4 | 1995 | 2216 | 29011 | 2103 | 29039 |

**发表参考统计量**（metafor regtest 官方文档示例：去掉第 16 号 ISIS-4 后 k=15，escalc measure="OR"，rma REML）：
- 底层模型：τ² = **0.1911**（SE 0.2106）；合并 logOR = **-0.8745**，95%CI **[-1.2822, -0.4669]**。
- Egger 检验（默认，混合效应元回归版）：**z = -2.8062，p = 0.0050**；极限估计（sei→0）b = **-0.1639** [-0.5681, 0.2402]。
- 经典 Egger（model="lm"，加权回归+乘数离散）：**t = -3.1783，df = 13，p = 0.0073**；极限估计 b = **-0.1512** [-0.5130, 0.2106]。

来源：https://wviechtb.github.io/metafor/reference/regtest.html ；数据表 https://wviechtb.github.io/metadat/reference/dat.egger2001.html

**容差建议**：z/t/df 相对 ≤1e-4；p 相对 ≤1e-2；底层合并量 ≤1e-4。

**Peters 检验**：未找到带发表数值的公开例（Peters 2006 检验的复现数据集在 metafor 文档中无独立示例）——列为"未获取"。

---

## 9. 容差建议（汇总）

| 量 | 建议容差 | 说明 |
|---|---|---|
| 逐试验效应量 yi/vi（escalc 类） | 相对 ≤1e-6 | JSS 2010 给 8 位小数，可严格卡 |
| 合并点估计、SE、CI 端点（log 尺度） | 相对 ≤1e-4 | 发表值多为 4 位小数 |
| τ²、τ、Q、QE、QM | 相对 ≤1e-4 | |
| I²、H²、P-score、kappa、ASE | 相对 ≤1e-3 | 中间舍入敏感；发表位数 2-4 位 |
| p 值（一般） | 相对 ≤1e-2，且以统计量（z/t/Q）≤1e-4 为主判据 | p<0.0001 只验量级与方向 |
| 检验统计量（z/t/χ²/CMH） | 相对 ≤1e-4 | |
| 整数型输出（剪补缺失研究数、k） | 必须全等 | |
| 反变换（RR/OR 及其 CI） | 相对 ≤1e-4 | |
| 贝叶斯 NMA（MCMC） | 均值 |Δ|≤0.01–0.02，CrI 端点 |Δ|≤0.03 | 仅 sanity；严格比对用 frequentist NMA（7.1） |

通用建议：以"统计量严格、p 宽松、整型全等"为原则；所有比对用双精度实现并在 log/原尺度分别比对一次。

---

## 10. 可行性汇总表

| 方法 | 数据集 | 数据（逐条输入） | 发表值 | 结论 |
|---|---|---|---|---|
| 逆方差 EE + DL（首选 BCG） | BCG/Colditz 1994 | 完整（2×2 与 yi/vi 8 位） | REML/EB 完整；**BCG 的 EE/DL 原文值未找到**（I² 有 ⚠） | **部分**（数据完整；EE/DL 用下三行替代验证） |
| 逆方差 EE + DL | Normand 1999（MD） | 完整（9 行 + yi） | EE/DL/REML 全套，声明与原文 Table X 一致 | **完整** |
| 多 τ² 估计量（DL/PM/HE/CA2/DL2/EB/ML/REML/HS/SJ） | D&K 2007（6 组 logOR） | 完整（2×2 与 yi/vi） | 11 个估计量的 τ/μ/SE，声明与论文 Tables 3-4 一致；已手算抽查吻合 | **完整** |
| EE + DL + DL 元回归 | Linde 2005 子集（17 研究 logOR） | 完整 | EE/DL/元回归全套，声明与原文 Table 2 p.113 一致 | **完整** |
| REML τ² | Normand 1999；Raudenbush 1985；D&K 2007 | 完整 | 三套 REML 发表值（0.019/685.20/τ=0.1843），R&B 声明与原文一致 | **完整** |
| Mantel-Haenszel OR/RR/RD/IRD/IRR | Rothman 2008 三例（2×2 与人时） | 完整（含书页码） | RD/RR/IRR/OR + CMH/MH 检验 | **完整** |
| Peto OR | Yusuf 1985 Table 6（22 比较） | 完整 | logOR/SE/CI/Q/I²，声明与原文 p.343 一致；含零事件口径说明 | **完整** |
| Q/I²/τ² | 复用上述 | — | 六套发表值集中记录 | **完整** |
| 剪补法 | BCG | 完整（复用 1.1） | EE/RE 两种模型缺失数与调整后合并值 | **完整** |
| Egger 检验 | dat.egger2001（镁，16→15 试验） | 完整 | rma 版 z 与经典版 t/p | **完整** |
| Peters 检验 | — | 未获取 | 未获取 | **放弃**（未找到公开数值例） |
| Cohen kappa | 30 例×2 医生 5×5 列联表 | 完整 | κ=0.6512/加权 0.6331 + ASE/z/p/CI（有 0.002 级 ⚠ 重算出入） | **完整（带 ⚠）** |
| Fleiss kappa | irr `diagnoses`（30×6，Fleiss 1971 原文） | **矩阵在包内二进制**（网页仅前 4 行） | κ=0.430/精确 0.428/3 评定员 0.534 + 分类别 | **部分**（发表值全；数据需 R 一行导出） |
| Light kappa | 同上 | 同上 | κ=0.378（6 评定员）/0.555（3 评定员） | **部分**（同上） |
| NMA（frequentist，确定性） | Senn 2013（53 臂行） | 完整（本清单已全文转录） | 9 个比较合并 MD + τ² + QE + P-score | **完整** |
| NMA（贝叶斯一致性/不一致） | gemtc thrombolytics（50 研究 9 治疗） | **逐行表需 R 包导出** | FE 一致性 logOR×8 + DIC + UME/节点拆分 | **部分**（数据需导出；MCMC 只能宽松容差） |
| netmeta 图论法参考 | netmeta Senn2013 | 未获取（vignette PDF 不可解析） | 仅 I²=84% 等片段 | **部分（低优先）** |

**放弃项**：Peters 检验（无公开数值例）；Wikipedia 14 评定员 Fleiss 例全表与 jstatsoft 全文（网络不可达，已留 URL 待人工补）；BCG 专属 EE/DL 发表值（以 Normand/D&K/Linde 三套替代，功能覆盖不受影响）。

**环境限制备忘**：本环境 GitHub、Wikipedia、rdrr.io、RDocumentation、jstatsoft 全文、RPubs 正文均不可达；irr/gemtc/netmeta 的内置数据集（diagnoses、thrombolytics、Senn2013 对比级）为包内 rda 二进制，需在有 R 的环境用 `data()` 导出后补入夹具（各条目已给出一行导出命令）。
