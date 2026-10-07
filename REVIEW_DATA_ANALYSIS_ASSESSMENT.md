# ReviewFlow Analysis Scope and Use Boundaries

[中文](#zh-cn) · [English](#en) · [Français](#fr) · [Русский](#ru) · [Español](#es) · [日本語](#ja) · [Português](#pt) · [Deutsch](#de) · [Српски](#sr) · [한국어](#ko)

<a id="zh-cn"></a>
## 中文

### 本文说明什么
本文列出仓库中可见的分析能力及研究者应检查的事项。它是功能范围说明，不是独立的软件验证报告，也不表示每个模块、选项或研究设计都已通过外部基准验证。统计输出用于支持研究者工作，不能代替方法学判断。

### 功能范围
应用提供研究与效应量资料录入、效应方向确认、效应量计算和格式转换、合成分析及结果导出。具体可用指标和估计选项以当前版本界面的指标注册表和估计器清单为准；不同指标所需的输入字段和变换并不相同。

代码库还包含以下分析模块，模块可用性和前提各异：

| 模块 | 使用前应核实的前提 |
| --- | --- |
| 固定效应与随机效应合成 | 效应量和标准误是否匹配；研究间异质性估计及区间方法是否适合问题。 |
| 亚组分析与 Meta 回归 | 研究层面特征不等于个体层面因果效应；预先设定假设，并检查组内研究数、协变量类型及稀疏类别。 |
| 诊断准确性分析 | 检查 TP/FP/FN/TN 定义、阈值和研究设计；不同阈值或数据结构不能不加判断地混合。 |
| 网络 Meta 分析 | 当前网络实现有其模型范围和连通性要求；检查比较方向、网络连通性、传递性与一致性。 |
| 剂量反应 | 核对剂量、参照水平、效应指标及每项研究的输入结构；非线性结论受数据覆盖范围限制。 |
| IPD 与生存相关模块 | 核对个体数据或摘要输入、时间尺度、删失及模型假设；确认分析实际采用的估计路径。 |
| 多层、贝叶斯和依赖效应模块 | 核对相关结构、先验、层级定义、收敛与不确定性输出；模块名称本身不证明设定合适。 |
| 偏倚风险敏感性分析 | 确认偏倚评估框架、记录版本、结果匹配关系和缺失/歧义处理。 |

这些类别是代码功能的概览，不构成逐项的版本认证。界面会对部分输入条件作限制或提示；不能满足门槛时应据实报告，不应以放宽门槛制造确定性。

### 结果核查清单
1. **冻结分析数据**：核对研究、比较、结局、时间点、效应方向、单位和排除记录；保存原始提取表与修订依据。
2. **复核效应量**：抽查输入、计算尺度、方差/标准误和置信区间，并确认软件方向约定与研究问题一致。
3. **检查独立性**：识别同一研究内多个结局、时间点、比较或效应量。普通单层合成不能自动消除依赖；选择合适的依赖模型或预先规定的汇总策略。
4. **检查模型选择**：记录固定/随机模型、异质性估计、区间方法、亚组定义及敏感性方案。模型选择应由设计与问题驱动，而不是根据显著性切换。
5. **解释探索性结果**：研究层面关联、亚组差异和小研究效应不自动说明因果关系或发表偏倚。结合研究数量、精度、异质性和证据来源解释。
6. **保留可复现材料**：导出录入数据、模型选项、结果和分析记录，并记录应用版本。关键结论应以独立计算或已知示例复核。

### 已知边界
- 本文不提供覆盖所有统计方法、软件版本、操作系统和边界输入的独立验证证明。
- 实现某个分析入口不代表适用于所有设计。效应量定义、研究单位、相关结构、缺失数据和模型假设需要研究者判断。
- 数值结果不能发现录入错误，也不能决定学科上的实质意义。
- 发表前应由研究团队审阅结果，并引用实际采用方法的原始方法学来源。

### 可追溯的代码入口
指标和估计器注册表位于 `coscreen/measure_registry.py`、`coscreen/estimators.py`；分析 API 位于 `custom_backend/main.py`；分析实现位于 `coscreen/` 下的 `advanced_analysis.py`、`dta_analysis.py`、`dose_analysis.py`、`network_analysis.py`、`ipd_analysis.py` 等文件。模块会随版本变化，请以实际发布版本源代码为准。

<a id="en"></a>
## English

### Purpose
This page summarizes analysis capabilities visible in the repository and checks researchers should make. It describes scope; it is not an independent software-validation report and does not mean every module, option, or study design has been benchmarked externally. Statistical output supports research work but does not replace methodological judgment.

### Scope
The application supports study and effect-size data entry, effect-direction checks, effect-size calculation and conversion, synthesis, and export. Available measures and estimators are defined by the registry and estimator list in the current version; required fields and transformations differ by measure.

The repository also contains modules with different availability and assumptions:

| Module | Check before use |
| --- | --- |
| Fixed- and random-effects synthesis | Confirm effect sizes and standard errors are compatible, and heterogeneity and interval methods fit the question. |
| Subgroups and meta-regression | Study-level associations are not individual-level causal effects. Prespecify hypotheses; check studies per group, covariate types, and sparse categories. |
| Diagnostic test accuracy | Check TP/FP/FN/TN definitions, thresholds, and design. Do not combine different thresholds or data structures without justification. |
| Network meta-analysis | Check model scope, comparison direction, network connectivity, transitivity, and consistency. |
| Dose-response | Verify dose, reference level, effect measure, and study input structure; nonlinear conclusions are limited by data coverage. |
| IPD and survival-related modules | Check individual-level or summary inputs, time scale, censoring, assumptions, and the estimator actually used. |
| Multilevel, Bayesian, and dependent-effect modules | Check dependence structure, priors, levels, convergence, and uncertainty output; a module name does not validate a setup. |
| Risk-of-bias sensitivity analysis | Confirm the assessment framework, record version, outcome mapping, and treatment of missing or ambiguous entries. |

These categories summarize code and are not version-by-version certification. The interface may restrict or flag some inputs. Report unmet requirements accurately; do not relax them to create unwarranted certainty.

### Results checklist
1. **Freeze the analysis data:** verify studies, comparisons, outcomes, time points, effect direction, units, and exclusions; retain the extraction table and revision rationale.
2. **Review effect sizes:** spot-check inputs, scale, variance/standard error, and confidence intervals; confirm the software's direction convention matches the question.
3. **Check independence:** identify multiple outcomes, times, comparisons, or effects from one study. A conventional single-level synthesis does not remove dependence; choose an appropriate model or prespecified aggregation.
4. **Check model choices:** record fixed/random model, heterogeneity estimator, interval method, subgroup definitions, and sensitivities. Choices should follow design and question, not significance.
5. **Interpret exploratory results:** study-level associations, subgroup differences, and small-study effects do not by themselves establish causality or publication bias. Consider study count, precision, heterogeneity, and evidence sources.
6. **Keep reproducible materials:** export data, options, results, and analysis records; record the app version. Independently reproduce key results or compare them with known examples.

### Boundaries
- This page does not certify every method, app version, operating system, or edge-case input.
- An analysis entry point is not suitable for every design. Researchers must judge effect definitions, units of analysis, dependence, missing data, and assumptions.
- Numerical output cannot detect data-entry errors or determine substantive meaning.
- The research team should review results before publication and cite the original methodological sources actually used.

### Code entry points
Measure and estimator registries: `coscreen/measure_registry.py` and `coscreen/estimators.py`; analysis API: `custom_backend/main.py`; implementations include `coscreen/advanced_analysis.py`, `dta_analysis.py`, `dose_analysis.py`, `network_analysis.py`, and `ipd_analysis.py`. Modules change over time; consult the source for the released version in use.

<a id="fr"></a>
## Français

### Objet
Cette page résume les capacités d’analyse visibles dans le dépôt et les vérifications à effectuer. Elle décrit le périmètre ; ce n’est ni un rapport indépendant de validation logicielle ni la preuve que chaque module, option ou plan d’étude a été validé par des références externes. Les résultats statistiques aident la recherche, sans remplacer le jugement méthodologique.

### Périmètre
L’application permet de saisir les études et tailles d’effet, de vérifier leur direction, de calculer et convertir des tailles d’effet, de réaliser des synthèses et d’exporter les résultats. Les mesures et estimateurs disponibles sont définis par les registres de la version utilisée ; les champs et transformations varient selon la mesure.

Le dépôt contient aussi des modules soumis à des conditions différentes :

| Module | Vérifications préalables |
| --- | --- |
| Synthèse à effets fixes et aléatoires | Compatibilité des tailles d’effet et erreurs types ; pertinence de l’hétérogénéité et des intervalles. |
| Sous-groupes et méta-régression | Une association au niveau des études n’est pas un effet causal individuel. Préspécifier les hypothèses et vérifier les effectifs et catégories rares. |
| Exactitude diagnostique | Vérifier TP/FP/FN/TN, seuils et plans d’étude ; ne pas mélanger sans justification des seuils ou structures différents. |
| Méta-analyse en réseau | Vérifier la portée du modèle, le sens des comparaisons, la connexité, la transitivité et la cohérence. |
| Dose-réponse | Vérifier dose, référence, mesure d’effet et structure des données ; les conclusions non linéaires dépendent de la couverture des données. |
| IPD et survie | Vérifier les données individuelles ou résumées, l’échelle temporelle, la censure, les hypothèses et l’estimateur utilisé. |
| Modèles multiniveaux, bayésiens et effets dépendants | Vérifier dépendance, a priori, niveaux, convergence et incertitude ; le nom du module ne valide pas le paramétrage. |
| Sensibilité au risque de biais | Vérifier le cadre d’évaluation, la version des données, la correspondance des résultats et les valeurs manquantes ou ambiguës. |

Ces catégories décrivent le code, sans certifier chaque version. L’interface peut signaler ou limiter certaines entrées. Signaler honnêtement les conditions non remplies, sans les assouplir pour créer une certitude injustifiée.

### Liste de vérification
1. **Figer les données** : vérifier études, comparaisons, critères, temps, direction, unités et exclusions ; conserver l’extraction et les motifs de révision.
2. **Vérifier les tailles d’effet** : contrôler des entrées, échelles, variances/erreurs types et intervalles ; vérifier la convention de direction.
3. **Vérifier l’indépendance** : repérer les résultats multiples d’une étude. Une synthèse simple ne supprime pas la dépendance ; choisir un modèle ou regroupement préspécifié.
4. **Documenter les modèles** : noter modèle fixe/aléatoire, estimateur d’hétérogénéité, intervalles, sous-groupes et sensibilités. Les choix suivent le plan et la question, pas la significativité.
5. **Interpréter avec prudence** : associations au niveau des études, différences entre sous-groupes et effets des petites études ne prouvent seuls ni causalité ni biais de publication.
6. **Préserver la reproductibilité** : exporter données, options, résultats et journaux ; noter la version et reproduire indépendamment les résultats clés.

### Limites
- Cette page ne certifie pas toutes les méthodes, versions, plateformes ni entrées limites.
- La présence d’une fonction ne la rend pas adaptée à tous les plans. Les chercheurs jugent définitions, unité d’analyse, dépendance, données manquantes et hypothèses.
- Un résultat numérique ne détecte pas les erreurs de saisie et ne détermine pas la portée scientifique.
- Avant publication, l’équipe doit examiner les résultats et citer les sources méthodologiques originales utilisées.

### Repères dans le code
Registres : `coscreen/measure_registry.py` et `coscreen/estimators.py` ; API : `custom_backend/main.py` ; implémentations notamment dans `coscreen/advanced_analysis.py`, `dta_analysis.py`, `dose_analysis.py`, `network_analysis.py` et `ipd_analysis.py`. Consulter le code de la version publiée utilisée.

<a id="ru"></a>
## Русский

### Назначение
Здесь описаны возможности анализа, видимые в репозитории, и проверки, которые следует выполнить исследователю. Это описание области функций, а не независимый отчёт о валидации ПО и не подтверждение внешней проверки каждого модуля, параметра или дизайна. Статистические результаты помогают исследованию, но не заменяют методологическое суждение.

### Область функций
Приложение поддерживает ввод данных исследований и эффектов, проверку направления эффекта, расчёт и преобразование эффектов, метаанализ и экспорт. Доступные показатели и оцениватели определяются реестрами текущей версии; поля и преобразования зависят от показателя.

В репозитории есть модули с различными условиями применения:

| Модуль | Что проверить |
| --- | --- |
| Модели с фиксированными и случайными эффектами | Совместимость эффекта и стандартной ошибки; соответствие оценки гетерогенности и интервалов вопросу. |
| Подгруппы и метарегрессия | Связь на уровне исследования не является индивидуальным причинным эффектом. Заранее задайте гипотезы и проверьте размеры групп и редкие категории. |
| Диагностическая точность | Проверьте TP/FP/FN/TN, пороги и дизайн; не объединяйте разные пороги или структуры без обоснования. |
| Сетевой метаанализ | Проверьте модель, направление сравнений, связность сети, транзитивность и согласованность. |
| «Доза — ответ» | Проверьте дозу, референс, меру эффекта и структуру данных; нелинейные выводы ограничены покрытием данных. |
| IPD и выживаемость | Проверьте индивидуальные или сводные данные, шкалу времени, цензурирование, допущения и фактически использованный оцениватель. |
| Многоуровневые, байесовские и зависимые эффекты | Проверьте зависимости, априорные распределения, уровни, сходимость и неопределённость; название модуля не подтверждает настройку. |
| Анализ чувствительности к риску смещения | Проверьте систему оценки, версию записей, соответствие результатов и обработку пропусков/неоднозначностей. |

Категории описывают код, но не сертифицируют каждую версию. Интерфейс может ограничивать или отмечать ввод. Честно указывайте невыполненные требования; не ослабляйте их ради ложной определённости.

### Проверка результатов
1. **Зафиксируйте данные:** проверьте исследования, сравнения, исходы, моменты времени, направление, единицы и исключения; сохраните таблицу извлечения и причины правок.
2. **Проверьте эффекты:** выборочно проверьте ввод, шкалу, дисперсии/стандартные ошибки и интервалы; сверьте соглашение о направлении.
3. **Проверьте независимость:** найдите несколько исходов или эффектов из одного исследования. Обычный одноуровневый синтез не устраняет зависимость; задайте подходящую модель или план агрегации.
4. **Документируйте модели:** запишите модель, оценку гетерогенности, интервалы, подгруппы и анализы чувствительности. Выбор должен определяться вопросом, а не значимостью.
5. **Осторожно интерпретируйте:** связи на уровне исследований, различия подгрупп и эффекты малых исследований сами по себе не доказывают причинность или публикационное смещение.
6. **Сохраняйте воспроизводимость:** экспортируйте данные, настройки, результаты и журнал, укажите версию и независимо проверьте ключевые результаты.

### Ограничения
- Эта страница не подтверждает валидацию всех методов, версий, ОС и граничных входных данных.
- Наличие функции не означает пригодность для любого дизайна. Исследователь оценивает определения эффектов, единицы анализа, зависимости, пропуски и допущения.
- Численный результат не обнаруживает ошибки ввода и не определяет научное значение.
- Перед публикацией команда должна проверить результаты и сослаться на использованные первичные методологические источники.

### Путь к коду
Реестры показателей и оценивателей: `coscreen/measure_registry.py`, `coscreen/estimators.py`; API: `custom_backend/main.py`; реализации включают `coscreen/advanced_analysis.py`, `dta_analysis.py`, `dose_analysis.py`, `network_analysis.py`, `ipd_analysis.py`. Сверяйтесь с исходным кодом используемой версии.

<a id="es"></a>
## Español

### Propósito
Esta página resume las capacidades de análisis visibles en el repositorio y las comprobaciones para investigadores. Describe el alcance; no es una validación independiente del software ni acredita que cada módulo, opción o diseño haya sido contrastado externamente. Los resultados estadísticos apoyan la investigación, pero no sustituyen el criterio metodológico.

### Alcance
La aplicación permite introducir datos de estudios y tamaños del efecto, comprobar su dirección, calcularlos y convertirlos, realizar síntesis y exportar resultados. Las medidas y estimadores disponibles dependen de los registros de la versión actual; los campos y transformaciones varían según la medida.

El repositorio incluye módulos con distintos requisitos:

| Módulo | Comprobaciones previas |
| --- | --- |
| Síntesis de efectos fijos y aleatorios | Compatibilidad entre efecto y error estándar; adecuación de la heterogeneidad y los intervalos. |
| Subgrupos y metarregresión | Una asociación a nivel de estudio no es un efecto causal individual. Preespecifique hipótesis y revise tamaños y categorías escasas. |
| Exactitud diagnóstica | Revise TP/FP/FN/TN, umbrales y diseño; no mezcle umbrales o estructuras distintos sin justificación. |
| Metaanálisis en red | Revise el alcance del modelo, dirección de comparaciones, conectividad, transitividad y consistencia. |
| Dosis-respuesta | Compruebe dosis, referencia, medida de efecto y estructura de datos; las conclusiones no lineales dependen de la cobertura. |
| IPD y supervivencia | Revise datos individuales o resumidos, escala temporal, censura, supuestos y estimador empleado. |
| Modelos multinivel, bayesianos y efectos dependientes | Revise dependencia, distribuciones previas, niveles, convergencia e incertidumbre; el nombre del módulo no valida la configuración. |
| Sensibilidad al riesgo de sesgo | Confirme el marco de evaluación, versión de registros, correspondencia de resultados y tratamiento de datos ausentes o ambiguos. |

Estas categorías describen el código y no certifican cada versión. La interfaz puede limitar o señalar entradas. Informe de los requisitos incumplidos sin relajarlos para aparentar certeza.

### Lista de revisión
1. **Fije los datos:** revise estudios, comparaciones, resultados, tiempos, dirección, unidades y exclusiones; conserve la extracción y sus cambios.
2. **Revise los efectos:** compruebe entradas, escala, varianza/error estándar e intervalos; confirme la convención de dirección.
3. **Compruebe independencia:** identifique resultados múltiples por estudio. La síntesis simple no elimina dependencia; elija un modelo o agregación preespecificados.
4. **Documente modelos:** registre modelo, estimador de heterogeneidad, intervalos, subgrupos y sensibilidades. La elección debe seguir el diseño y la pregunta, no la significación.
5. **Interprete con cautela:** asociaciones por estudio, diferencias entre subgrupos y efectos de estudios pequeños no prueban por sí solos causalidad ni sesgo de publicación.
6. **Conserve la reproducibilidad:** exporte datos, opciones, resultados y registros; anote la versión y reproduzca independientemente los resultados clave.

### Límites
- Esta página no valida todos los métodos, versiones, sistemas operativos ni entradas límite.
- Que exista una función no significa que sirva para cualquier diseño. El investigador debe juzgar definiciones, unidad de análisis, dependencia, datos ausentes y supuestos.
- Los resultados numéricos no detectan errores de entrada ni determinan el significado sustantivo.
- Antes de publicar, el equipo debe revisar los resultados y citar las fuentes metodológicas originales utilizadas.

### Puntos de entrada del código
Registros: `coscreen/measure_registry.py` y `coscreen/estimators.py`; API: `custom_backend/main.py`; implementaciones, entre otras: `coscreen/advanced_analysis.py`, `dta_analysis.py`, `dose_analysis.py`, `network_analysis.py` e `ipd_analysis.py`. Consulte el código de la versión publicada que usa.

<a id="ja"></a>
## 日本語

### この文書について
この文書は、リポジトリで確認できる分析機能と研究者が確認すべき点をまとめたものです。機能範囲の説明であり、独立したソフトウェア検証報告ではありません。統計出力は研究を支援しますが、方法論上の判断に代わるものではありません。

### 機能範囲
研究・効果量データの入力、効果方向の確認、効果量の計算と変換、統合分析、結果のエクスポートに対応します。利用可能な指標と推定方法は使用中の版のレジストリに従い、必要な入力項目や変換は指標ごとに異なります。

リポジトリには前提条件の異なる分析モジュールもあります。

| モジュール | 使用前の確認事項 |
| --- | --- |
| 固定効果・ランダム効果の統合 | 効果量と標準誤差の適合性、異質性推定と区間推定の妥当性。 |
| サブグループ・メタ回帰 | 研究レベルの関連は個人レベルの因果効果ではありません。仮説を事前に定め、群の研究数と少数カテゴリを確認します。 |
| 診断精度 | TP/FP/FN/TN、閾値、研究デザインを確認し、異なる閾値や構造を根拠なく混合しません。 |
| ネットワークメタ分析 | モデル範囲、比較の方向、ネットワークの連結性、推移性、一貫性を確認します。 |
| 用量反応 | 用量、参照値、効果指標、研究ごとの入力構造を確認します。非線形の結論はデータ範囲に制約されます。 |
| IPD・生存時間関連 | 個票または要約データ、時間尺度、打ち切り、仮定、実際に使われた推定経路を確認します。 |
| 多層・ベイズ・依存効果 | 依存構造、事前分布、階層、収束、不確実性を確認します。モジュール名だけでは設定の妥当性は保証されません。 |
| バイアスリスク感度分析 | 評価枠組み、記録版、結果との対応、欠損・曖昧値の処理を確認します。 |

これらはコード機能の概要であり、各版の認証ではありません。画面は一部の入力を制限・警告する場合があります。条件を満たさない場合は、その事実を報告してください。

### 結果の確認
1. **データを確定する：** 研究、比較、アウトカム、時点、方向、単位、除外を確認し、抽出表と修正理由を保存します。
2. **効果量を確認する：** 入力、尺度、分散・標準誤差、信頼区間を点検し、方向の規約が研究課題と一致するか確認します。
3. **独立性を確認する：** 同一研究内の複数アウトカム等を識別します。通常の単層統合は依存を解消しないため、適切なモデルか事前規定の集約方法を選びます。
4. **モデル選択を記録する：** 固定/ランダム、異質性推定、区間法、サブグループ、感度分析を記録します。有意性を見て選択を変えないでください。
5. **探索的結果を慎重に解釈する：** 研究レベルの関連、サブグループ差、小規模研究効果だけで因果関係や出版バイアスは証明されません。
6. **再現性を保つ：** データ、設定、結果、記録を出力し、アプリ版を記録します。重要結果は独立計算などで確認します。

### 制約
- すべての方法、版、OS、境界入力を網羅した検証を示すものではありません。
- 機能が存在しても、すべての研究デザインに適するとは限りません。定義、分析単位、依存、欠損、モデル仮定は研究者が判断します。
- 数値結果は入力ミスを検出せず、学術的な意味も決定しません。
- 公表前に研究チームが結果を確認し、実際に用いた方法の一次資料を引用してください。

### コード参照先
指標・推定方法のレジストリは `coscreen/measure_registry.py`、`coscreen/estimators.py`、分析 API は `custom_backend/main.py` にあります。実装には `coscreen/advanced_analysis.py`、`dta_analysis.py`、`dose_analysis.py`、`network_analysis.py`、`ipd_analysis.py` などが含まれます。利用版のソースを参照してください。

<a id="pt"></a>
## Português

### Objetivo
Esta página resume os recursos de análise visíveis no repositório e as verificações recomendadas aos pesquisadores. Descreve o escopo; não é uma validação independente do software nem certifica externamente todos os módulos, opções ou desenhos. Os resultados apoiam a pesquisa, mas não substituem o julgamento metodológico.

### Escopo
A aplicação permite inserir dados de estudos e tamanhos de efeito, verificar a direção, calcular e converter efeitos, realizar sínteses e exportar resultados. Medidas e estimadores disponíveis dependem dos registros da versão atual; campos e transformações variam conforme a medida.

O repositório também inclui módulos com pressupostos diferentes:

| Módulo | Verificações antes do uso |
| --- | --- |
| Síntese de efeitos fixos e aleatórios | Compatibilidade entre efeito e erro-padrão; adequação da heterogeneidade e dos intervalos. |
| Subgrupos e meta-regressão | Associação no nível do estudo não é efeito causal individual. Especifique hipóteses e verifique tamanhos dos grupos e categorias raras. |
| Acurácia diagnóstica | Confira TP/FP/FN/TN, limiares e desenho; não combine estruturas diferentes sem justificativa. |
| Meta-análise em rede | Confira escopo do modelo, direção das comparações, conectividade, transitividade e consistência. |
| Dose-resposta | Confira dose, referência, medida de efeito e estrutura dos dados; conclusões não lineares dependem da cobertura dos dados. |
| IPD e sobrevivência | Confira dados individuais ou resumidos, escala temporal, censura, pressupostos e estimador usado. |
| Modelos multinível, bayesianos e efeitos dependentes | Confira dependência, priors, níveis, convergência e incerteza; o nome do módulo não valida a configuração. |
| Sensibilidade ao risco de viés | Confira estrutura de avaliação, versão dos registros, correspondência dos resultados e dados ausentes ou ambíguos. |

As categorias resumem o código e não certificam cada versão. A interface pode restringir ou sinalizar entradas. Relate requisitos não atendidos sem flexibilizá-los para sugerir certeza indevida.

### Lista de verificação
1. **Congele os dados:** confira estudos, comparações, desfechos, tempos, direção, unidades e exclusões; preserve a extração e as justificativas das alterações.
2. **Revise os efeitos:** verifique entradas, escala, variância/erro-padrão e intervalos; confirme a convenção de direção.
3. **Confira independência:** identifique múltiplos resultados de um estudo. Uma síntese simples não elimina dependência; escolha modelo ou agregação predefinidos.
4. **Registre os modelos:** anote modelo, estimador de heterogeneidade, intervalos, subgrupos e sensibilidades. A escolha deve seguir o desenho e a pergunta, não a significância.
5. **Interprete com cautela:** associações entre estudos, diferenças de subgrupo e efeitos de estudos pequenos não provam, por si, causalidade ou viés de publicação.
6. **Preserve a reprodutibilidade:** exporte dados, opções, resultados e registros, anote a versão e confira resultados-chave de forma independente.

### Limites
- Esta página não valida todos os métodos, versões, sistemas operacionais ou entradas extremas.
- A existência de uma função não garante adequação a todo desenho. O pesquisador julga definições, unidade de análise, dependência, dados ausentes e pressupostos.
- Resultados numéricos não detectam erros de entrada nem determinam significado substantivo.
- Antes da publicação, a equipe deve revisar os resultados e citar as fontes metodológicas originais utilizadas.

### Referências no código
Registros: `coscreen/measure_registry.py` e `coscreen/estimators.py`; API: `custom_backend/main.py`; implementações incluem `coscreen/advanced_analysis.py`, `dta_analysis.py`, `dose_analysis.py`, `network_analysis.py` e `ipd_analysis.py`. Consulte o código da versão publicada em uso.

<a id="de"></a>
## Deutsch

### Zweck
Diese Seite fasst sichtbare Analysefunktionen des Repositorys und erforderliche Prüfungen zusammen. Sie beschreibt den Funktionsumfang, ist aber kein unabhängiger Validierungsbericht und bestätigt keine externe Prüfung jedes Moduls, jeder Option oder jedes Studiendesigns. Statistische Ergebnisse unterstützen die Forschung, ersetzen jedoch keine methodische Beurteilung.

### Umfang
Die Anwendung unterstützt die Eingabe von Studien- und Effektgrößendaten, die Prüfung der Effektrichtung, Berechnung und Umrechnung von Effektgrößen, Synthesen und Ergebnisexport. Verfügbare Maße und Schätzer ergeben sich aus den Registern der verwendeten Version; Eingabefelder und Transformationen unterscheiden sich je nach Maß.

Das Repository enthält weitere Module mit unterschiedlichen Voraussetzungen:

| Modul | Vor der Nutzung prüfen |
| --- | --- |
| Synthese mit festen und zufälligen Effekten | Passung von Effektgröße und Standardfehler sowie Eignung von Heterogenitäts- und Intervallverfahren. |
| Subgruppen und Metaregression | Studienbezogene Zusammenhänge sind keine individuellen kausalen Effekte. Hypothesen vorab festlegen und Gruppengrößen sowie seltene Kategorien prüfen. |
| Diagnostische Genauigkeit | TP/FP/FN/TN, Schwellenwerte und Studiendesign prüfen; unterschiedliche Strukturen nicht unbegründet vermischen. |
| Netzwerk-Metaanalyse | Modellumfang, Vergleichsrichtung, Netzwerkkonnektivität, Transitivität und Konsistenz prüfen. |
| Dosis-Wirkung | Dosis, Referenz, Effektmaß und Eingabestruktur prüfen; nichtlineare Aussagen hängen von der Datenabdeckung ab. |
| IPD und Überleben | Individual- oder aggregierte Daten, Zeitskala, Zensierung, Annahmen und tatsächlich verwendeten Schätzer prüfen. |
| Mehrebenen-, Bayes- und abhängige Effekte | Abhängigkeit, Priors, Ebenen, Konvergenz und Unsicherheit prüfen; der Modulname bestätigt keine passende Konfiguration. |
| Sensitivität gegenüber Verzerrungsrisiken | Bewertungsrahmen, Datensatzversion, Zuordnung der Ergebnisse und fehlende/unklare Werte prüfen. |

Diese Kategorien beschreiben den Code und zertifizieren keine einzelne Version. Die Oberfläche kann Eingaben einschränken oder markieren. Nicht erfüllte Bedingungen sind korrekt anzugeben und nicht für scheinbare Sicherheit aufzuweichen.

### Prüfliste
1. **Daten festschreiben:** Studien, Vergleiche, Endpunkte, Zeitpunkte, Richtung, Einheiten und Ausschlüsse prüfen; Extraktion und Änderungsgründe sichern.
2. **Effektgrößen prüfen:** Eingaben, Skala, Varianz/Standardfehler und Intervalle stichprobenartig kontrollieren; Richtungskonvention abgleichen.
3. **Unabhängigkeit prüfen:** mehrere Ergebnisse derselben Studie erkennen. Eine einfache Synthese beseitigt Abhängigkeit nicht; geeignetes Modell oder vorab festgelegte Aggregation wählen.
4. **Modelle dokumentieren:** Modell, Heterogenitätsschätzer, Intervalle, Subgruppen und Sensitivitäten festhalten. Die Frage und das Design, nicht die Signifikanz, sollten die Wahl bestimmen.
5. **Explorative Ergebnisse vorsichtig deuten:** Studienbezogene Zusammenhänge, Subgruppenunterschiede und Small-Study-Effekte belegen allein weder Kausalität noch Publikationsbias.
6. **Reproduzierbarkeit sichern:** Daten, Optionen, Ergebnisse und Protokolle exportieren, Version festhalten und Schlüsselresultate unabhängig prüfen.

### Grenzen
- Diese Seite validiert nicht alle Methoden, Versionen, Betriebssysteme oder Grenzwerteingaben.
- Eine vorhandene Funktion passt nicht automatisch zu jedem Design. Forschende beurteilen Effektdefinition, Analyseeinheit, Abhängigkeit, fehlende Daten und Annahmen.
- Zahlenwerte erkennen keine Eingabefehler und bestimmen keine inhaltliche Bedeutung.
- Vor einer Veröffentlichung sollte das Team Ergebnisse prüfen und die tatsächlich verwendeten Originalquellen zitieren.

### Code-Einstiegspunkte
Register: `coscreen/measure_registry.py` und `coscreen/estimators.py`; Analyse-API: `custom_backend/main.py`; Implementierungen unter anderem `coscreen/advanced_analysis.py`, `dta_analysis.py`, `dose_analysis.py`, `network_analysis.py` und `ipd_analysis.py`. Maßgeblich ist der Quellcode der verwendeten Veröffentlichung.

<a id="sr"></a>
## Српски

### Svrha
Ova stranica sažima analitičke mogućnosti vidljive u repozitorijumu i provere koje istraživači treba da obave. Opisuje opseg funkcija; nije nezavisni izveštaj o validaciji softvera niti potvrda da je svaki modul, opcija ili dizajn spolja proveravan. Statistički rezultati podržavaju istraživanje, ali ne zamenjuju metodološko rasuđivanje.

### Opseg
Aplikacija podržava unos podataka o studijama i veličinama efekta, proveru smera efekta, računanje i konverziju efekata, sintezu i izvoz rezultata. Dostupne mere i procenjivači određeni su registrima tekuće verzije; polja i transformacije zavise od mere.

Repozitorijum sadrži i module sa različitim pretpostavkama:

| Modul | Proveriti pre upotrebe |
| --- | --- |
| Sinteza fiksnih i slučajnih efekata | Usklađenost efekta i standardne greške; prikladnost procene heterogenosti i intervala. |
| Podgrupe i meta-regresija | Povezanost na nivou studije nije individualni uzročni efekat. Unapred odrediti hipoteze i proveriti veličine grupa i retke kategorije. |
| Dijagnostička tačnost | Proveriti TP/FP/FN/TN, pragove i dizajn; ne spajati različite strukture bez obrazloženja. |
| Mrežna meta-analiza | Proveriti opseg modela, smer poređenja, povezanost mreže, tranzitivnost i konzistentnost. |
| Odnos doze i odgovora | Proveriti dozu, referencu, meru efekta i strukturu podataka; nelinearni zaključci zavise od pokrivenosti podataka. |
| IPD i preživljavanje | Proveriti individualne ili zbirne podatke, vremensku skalu, cenzurisanje, pretpostavke i korišćeni procenjivač. |
| Višenivojski, Bayesovi i zavisni efekti | Proveriti zavisnost, apriorne raspodele, nivoe, konvergenciju i neizvesnost; naziv modula ne potvrđuje ispravnost podešavanja. |
| Analiza osetljivosti na rizik od pristrasnosti | Proveriti okvir procene, verziju zapisa, povezivanje rezultata i nedostajuće/nejasne vrednosti. |

Ove kategorije opisuju kod, a ne sertifikuju svaku verziju. Interfejs može ograničiti ili označiti unose. Neispunjene uslove treba tačno prijaviti, a ne ublažavati radi prividne sigurnosti.

### Kontrolna lista rezultata
1. **Zaključajte podatke:** proverite studije, poređenja, ishode, vremenske tačke, smer, jedinice i isključenja; sačuvajte ekstrakciju i razloge izmena.
2. **Proverite efekte:** uzorkujte unose, skalu, varijansu/standardnu grešku i intervale; potvrdite konvenciju smera.
3. **Proverite nezavisnost:** pronađite više ishoda iz iste studije. Jednostavna sinteza ne uklanja zavisnost; izaberite odgovarajući model ili unapred definisano grupisanje.
4. **Zabeležite modele:** model, procenu heterogenosti, intervale, podgrupe i analize osetljivosti. Izbor treba da prati dizajn i pitanje, a ne značajnost.
5. **Oprezno tumačite istraživačke rezultate:** povezanosti na nivou studija, razlike podgrupa i efekti malih studija sami ne dokazuju uzročnost ni pristrasnost objavljivanja.
6. **Sačuvajte ponovljivost:** izvezite podatke, opcije, rezultate i zapise, zabeležite verziju i nezavisno proverite ključne nalaze.

### Ograničenja
- Ova stranica ne potvrđuje sve metode, verzije, operativne sisteme ni granične unose.
- Postojanje funkcije ne znači da odgovara svakom dizajnu. Istraživači procenjuju definicije, jedinicu analize, zavisnost, nedostajuće podatke i pretpostavke.
- Brojčeni rezultati ne otkrivaju greške unosa niti određuju suštinsko značenje.
- Pre objavljivanja tim treba da pregleda rezultate i navede izvorne metodološke izvore koje je zaista koristio.

### Putanje do koda
Registri mera i procenjivača: `coscreen/measure_registry.py` i `coscreen/estimators.py`; API: `custom_backend/main.py`; implementacije uključuju `coscreen/advanced_analysis.py`, `dta_analysis.py`, `dose_analysis.py`, `network_analysis.py` i `ipd_analysis.py`. Koristite izvorni kod konkretne objavljene verzije.

<a id="ko"></a>
## 한국어

### 문서의 목적
이 문서는 저장소에서 확인되는 분석 기능과 연구자가 점검해야 할 사항을 요약합니다. 기능 범위 설명이며 독립적인 소프트웨어 검증 보고서가 아니고, 모든 모듈·옵션·연구 설계가 외부 기준으로 검증되었다는 뜻도 아닙니다. 통계 결과는 연구를 지원하지만 방법론적 판단을 대신하지 않습니다.

### 기능 범위
연구 및 효과크기 자료 입력, 효과 방향 확인, 효과크기 계산과 변환, 종합 분석 및 결과 내보내기를 지원합니다. 사용 가능한 지표와 추정 방법은 해당 버전의 레지스트리에 따르며, 필요한 입력 항목과 변환은 지표마다 다릅니다.

저장소에는 전제 조건이 서로 다른 분석 모듈도 포함되어 있습니다.

| 모듈 | 사용 전 확인 사항 |
| --- | --- |
| 고정효과 및 랜덤효과 종합 | 효과크기와 표준오차의 호환성, 이질성 추정 및 구간 방법의 적절성. |
| 하위그룹 및 메타회귀 | 연구 수준 연관성은 개인 수준 인과효과가 아닙니다. 가설을 사전 설정하고 그룹별 연구 수와 희소 범주를 확인하세요. |
| 진단 정확도 | TP/FP/FN/TN 정의, 임계값, 연구 설계를 확인하고 근거 없이 다른 구조를 합치지 마세요. |
| 네트워크 메타분석 | 모델 범위, 비교 방향, 네트워크 연결성, 전이성 및 일관성을 확인하세요. |
| 용량-반응 | 용량, 기준값, 효과 지표와 연구별 입력 구조를 확인하세요. 비선형 결론은 데이터 범위의 영향을 받습니다. |
| IPD 및 생존 분석 | 개인자료 또는 요약자료, 시간 척도, 검열, 가정 및 실제 사용된 추정 방법을 확인하세요. |
| 다층·베이지안·종속 효과 | 의존 구조, 사전분포, 계층, 수렴 및 불확실성을 확인하세요. 모듈 이름만으로 설정의 타당성이 보장되지는 않습니다. |
| 비뚤림 위험 민감도 분석 | 평가 체계, 기록 버전, 결과 매핑, 결측 또는 모호한 값의 처리를 확인하세요. |

이 범주는 코드 기능의 개요이며 버전별 인증이 아닙니다. 인터페이스는 일부 입력을 제한하거나 표시할 수 있습니다. 조건을 충족하지 못했다면 정확히 보고하고 임의로 완화해 확실성을 만들어내지 마세요.

### 결과 점검 목록
1. **분석 자료 확정:** 연구, 비교, 결과, 시점, 방향, 단위와 제외 기록을 확인하고 추출표 및 수정 근거를 보존합니다.
2. **효과크기 검토:** 입력, 척도, 분산/표준오차와 신뢰구간을 표본 점검하고 방향 규칙이 연구 질문과 맞는지 확인합니다.
3. **독립성 확인:** 동일 연구의 여러 결과나 시점을 식별합니다. 일반적인 단일 수준 종합은 의존성을 제거하지 않으므로 적절한 모형이나 사전 지정된 집계 전략을 선택합니다.
4. **모형 선택 기록:** 고정/랜덤 모형, 이질성 추정량, 구간 방법, 하위그룹과 민감도 분석을 기록합니다. 유의성에 따라 선택을 바꾸지 마세요.
5. **탐색 결과를 신중히 해석:** 연구 수준 연관성, 하위그룹 차이, 소규모 연구 효과만으로 인과관계나 출판 비뚤림이 입증되지는 않습니다.
6. **재현성 보존:** 자료, 설정, 결과와 기록을 내보내고 앱 버전을 기록합니다. 핵심 결과는 독립 계산 등으로 확인합니다.

### 한계
- 이 문서는 모든 통계 방법, 버전, 운영체제 및 경계 입력을 검증했다는 증거가 아닙니다.
- 기능이 구현되어 있어도 모든 연구 설계에 적합한 것은 아닙니다. 효과 정의, 분석 단위, 의존성, 결측자료와 모형 가정은 연구자가 판단해야 합니다.
- 수치 결과는 입력 오류를 찾아내거나 실질적 의미를 결정하지 않습니다.
- 논문 발표 전에 연구팀이 결과를 검토하고 실제 사용한 방법론 원자료를 인용해야 합니다.

### 코드 위치
지표 및 추정 방법 레지스트리: `coscreen/measure_registry.py`, `coscreen/estimators.py`; 분석 API: `custom_backend/main.py`; 구현 파일은 `coscreen/advanced_analysis.py`, `dta_analysis.py`, `dose_analysis.py`, `network_analysis.py`, `ipd_analysis.py` 등을 포함합니다. 사용 중인 릴리스의 소스 코드를 기준으로 확인하세요.
