# 依赖合规报告（2.1.5）

- 生成日期：2026-11-10
- 审计环境：`C:/Users/16073/.codex/preview-data/reviewflow-2-1-1-venv`（开发/测试
  venv，Windows；**Python 3.11.15**——`python --version` 实测；共 87 个已装包，
  其中 34 个为开发与审计工具，不属应用依赖）。注意：发行包捆绑的运行时为
  CPython 3.12.7（THIRD_PARTY_NOTICES.md 运行时行），§3 的 53 包闭包与
  依赖标记均在 3.11.15 上求值；`build/requirements-app.txt` 未声明
  `python_version` 类标记（已核对该文件全部 22 条 requirement 的 marker 集，
  仅含 `sys_platform`），闭包成员不受 Python 小版本差异影响，但 3.12 构建
  可能解析到不同版本的 wheel（见 §7 边界）。
- 审计范围：pip-audit 实跑、`build/requirements-app.txt` 安装闭包实测、
  逐包许可证实测（发行元数据 + 随包许可文件）、THIRD_PARTY_NOTICES.md 对账、
  LICENSE/README 表述矛盾修正、README 漂移复查
- 与 2.1.4 报告（out/overnight/deps_compliance.md）的关系：本轮全部重测，
  不照抄旧清单；旧报告登记的「15 个缺失传递依赖、11 处版本漂移」按当前实测
  重新核对后落进 THIRD_PARTY_NOTICES.md

## 1. 结论摘要

| 检查项 | 结果 |
| --- | --- |
| pip-audit | **退出码 1，4 个包有已知漏洞通告**；三种计数口径并列：原始输出 **68 条记录**（stderr 汇总句 "Found 68 known vulnerabilities in 4 packages"）、去除重复记录后 **61 条唯一通告**（pillow 39 / pypdf 14 / pip 6 / setuptools 2）、其中**唯一 CVE 编号 59 个**（2026 年 58 + 2025 年 1，另有 2 条仅有 GHSA 编号）。61 是重复记录去重而非别名去重（并查集实测本批通告间零互为别名）。详见 §2；升级需负责人批准，本轮未动任何依赖 |
| 声明依赖满足性 | `build/requirements-app.txt` 全部 22 条 requirement 在本环境适用标记、已安装且版本满足约束，零告警 |
| 安装闭包 | 22 条直接依赖在本机解析为 **53 个包**（沿 Requires-Dist 传递、按当前环境求值标记；实测脚本见附录） |
| THIRD_PARTY_NOTICES 缺 | 修复前 **16 个闭包内传递依赖未列入**（2.1.4 登记 15 个，本轮新增 packaging），已全部补列（§3.3） |
| THIRD_PARTY_NOTICES 版本漂移 | 写死版本的条目中 **8 项与当前实测不一致**（7 升 1 降），另有 6 项原未写死、本轮落定实测值；已全部更新（§3.2） |
| 许可证纠错 | h11 误归 BSD-3-Clause 组，实测 MIT，已纠正；**apprise 许可证实测为 BSD-2-Clause（非任务假设的 MIT）**（§4） |
| 新引入包 | 2.1.5 **未新增强制运行时依赖**（requirements-app.txt 自 2.1.4 以来零改动）；apprise 为可选懒加载依赖、未随包安装；**`.ics` 生成是仓内自研模块，未引入第三方 ics 库**（§4） |
| LICENSE 表述矛盾 | 根目录 LICENSE 为 MIT、README 十语言一致称 MIT、THIRD_PARTY_NOTICES.md 原写「尚未选定」——已按 LICENSE 文件改为 MIT（§5） |
| README 漂移复查 | 2.1.4 登记的三组失效引用（requirements-dev.txt / 顶层 tests/ / run_pathB.bat）**在当前树已全部修复**；本轮新抽查（版本号、端口、目录表、NAVIGATION_MAP 方法计数）全部属实，无需改动（§6） |

## 2. pip-audit 实跑结果（退出码 1）

命令与退出码：venv 内 `python -m pip_audit -f json --progress-spinner off`
（说明：`python -m pip audit` 子命令形式在本环境报 `ERROR: unknown command
"audit"`——pip 24.0 未挂载该子命令，故用等价的模块入口 pip_audit 2.10.1；
任务原文写的 `python -m pip audit` 未能原样执行，此处如实记录）。
文本式复跑 `python -m pip_audit --progress-spinner off` 退出码 1，stderr
汇总句原样为 **"Found 68 known vulnerabilities in 4 packages"**，stdout
68 行通告（存 `E:/rf214tmp/debt-a/pip_audit_text.out/.err`）；完整 JSON 存
`E:/rf214tmp/debt-a/pip_audit.json`（87 个包）。

**三种计数口径（如实并列）**：

| 口径 | 数值 | 说明 |
| --- | --- | --- |
| 原始记录 | **68** | pip-audit 输出的通告行数；其中 7 行为重复记录（pip 的 6 条各出现 2 次、setuptools 的 PYSEC-2026-3447 出现 2 次） |
| 唯一通告（包,通告 ID）去重 | **61** | pillow 39、pypdf 14、pip 6、setuptools 2；**这是重复记录去重，不是别名去重**——对全部通告 ID 及其 aliases 做并查集实测，本批通告之间零互为别名，无 ID 可因别名合并 |
| 唯一 CVE 编号 | **59** | 2026 年 58 个 + 2025 年 1 个；另有 2 条通告仅有 GHSA 编号（pillow 的 GHSA-78g4-xqwv-q3g8、setuptools 的 GHSA-xmj3-9gf7-h52w），不计入 CVE 口径 |

| 包 | 实测版本 | 通告数（唯一） | 修复版本（通告给出的最低修复版本集合） | 是否应用闭包 |
| --- | --- | --- | --- | --- |
| pillow | 12.3.0 | 39 | 13.0.0 | **是**（python-pptx/CairoSVG 依赖，requirements-app.txt 声明） |
| pypdf | 6.20.1 | 14 | 6.21.0 ～ 6.21.4 | **是**（直接依赖） |
| pip | 24.0 | 6（12 条记录） | 25.3 ～ 26.2 | 否（开发 venv 工具；随发行包捆绑的 pip 版本见 THIRD_PARTY_NOTICES 的 CPython/pip 行，本轮未重测发行包） |
| setuptools | 79.0.1 | 2（3 条记录） | 83.0.0 / 84.1.0 | 否（开发 venv 工具） |

全部通告编号清单（如实列出，交负责人裁定；**本轮未升级任何依赖**——按约定
升级需用户批准）：

- pillow 12.3.0：CVE-2026-96638、CVE-2026-96640、CVE-2026-96778、
  CVE-2026-112390、CVE-2026-112391、CVE-2026-112393、CVE-2026-112395、
  CVE-2026-112399、CVE-2026-112400、CVE-2026-112401、CVE-2026-112404、
  CVE-2026-112406、CVE-2026-112891、CVE-2026-112892、CVE-2026-112893、
  CVE-2026-112894、CVE-2026-112895、CVE-2026-112897、CVE-2026-112899、
  CVE-2026-112900、GHSA-78g4-xqwv-q3g8、PYSEC-2026-4326 ～ PYSEC-2026-4343
  （18 条连续编号）
- pypdf 6.20.1：CVE-2026-113786、CVE-2026-113789、CVE-2026-113790、
  CVE-2026-113791、CVE-2026-115572、CVE-2026-115580、CVE-2026-115585、
  CVE-2026-119203 ～ CVE-2026-119209（7 条连续编号）
- pip 24.0：PYSEC-2026-196、PYSEC-2026-1795、PYSEC-2026-1796、
  PYSEC-2026-2875、PYSEC-2026-2876、PYSEC-2026-3721
- setuptools 79.0.1：PYSEC-2026-3447、GHSA-xmj3-9gf7-h52w

维护性说明：pip-audit 是 OSV/PyPI Advisory 的时点快照，发布前应重跑；
pillow/pypdf 属应用闭包，升级会改变发行包内容，需负责人批准后另行验证
（requirements-app.txt 对 pillow `>=10`、pypdf `>=4.0` 均为下限式约束，
升到修复版本不违反声明，但须按仓库惯例跑定向回归后再定）。

## 3. THIRD_PARTY_NOTICES.md 对账（修复前 → 修复后）

### 3.1 直接依赖 22/22 满足

`build/requirements-app.txt` 全部 22 条（本机适用全部平台标记，含 win32
条件组 reportlab/svglib/rl-renderPM）均已安装且版本满足约束；3 个精确锁定
（statsmodels==0.14.5、svglib==1.5.1、rl-renderPM==4.0.3）实测一致。

### 3.2 版本漂移（写死版本 8 项不一致 + 6 项落定）

| 包 | notices 原记载 | 当前实测 | 方向 |
| --- | --- | --- | --- |
| FastAPI | 0.141.1 | 0.143.0 | 升 |
| Pydantic | 2.13.5 | 2.14.1 | 升 |
| pydantic-core | 2.46.5 | 2.50.1 | 升 |
| PyJWT | 2.14.0 | 2.15.1 | 升 |
| Uvicorn | 0.53.0 | 0.54.0 | 升 |
| Starlette | 1.7.0 | 1.8.0 | 升 |
| pypdf | 6.19.0 | 6.20.1 | 升 |
| NumPy | 2.5.3 | 2.4.6 | **降** |

原未写死、本轮落定实测值 **6 项**：pandas 2.3.3、SciPy 1.17.1、Patsy
1.0.3、scikit-learn 1.9.1、ReportLab 4.5.1、CairoSVG 2.9.1（前三个在主表
原写「构建时解析版本」，后三个在 Windows 补充节原无版本）。其余写死条目
（anyio、RapidFuzz、
rispy、Click、h11、idna、bcrypt、python-multipart、passlib、statsmodels、
python-docx、python-pptx、lxml、Pillow、XlsxWriter、python-dateutil、six、
annotated-doc、annotated-types、typing-inspection、typing-extensions、
svglib、rl-renderPM）与实测一致。

2.1.4 登记的 11 处漂移（FastAPI 0.162.0 等）出自当时的另一个审计环境；
本轮以当前 venv 为准重新对账，未照抄。

### 3.3 notices 缺失的 16 个传递依赖（已补列，含引入关系实测）

| 包 | 实测版本 | 引入关系（当前环境反向实测） | 许可（实测） |
| --- | --- | --- | --- |
| joblib | 1.6.0 | scikit-learn | BSD-3-Clause |
| cloudpickle | 3.1.2 | joblib | BSD-3-Clause |
| threadpoolctl | 3.7.0 | scikit-learn | BSD-3-Clause |
| narwhals | 2.27.1 | scikit-learn | MIT |
| opentelemetry-api | 1.45.1 | FastAPI | Apache-2.0 |
| pytz | 2026.5 | pandas | MIT |
| tzdata | 2026.5 | pandas | Apache-2.0 |
| packaging | 26.3 | statsmodels、Patsy | Apache-2.0 OR BSD-2-Clause |
| charset-normalizer | 3.5.2 | ReportLab | MIT |
| cffi | 2.1.1 | cairocffi | MIT-0 |
| cairocffi | 1.7.1 | CairoSVG | BSD（分类器） |
| cssselect2 | 0.10.1 | CairoSVG、svglib | BSD（分类器） |
| tinycss2 | 1.5.1 | CairoSVG、cssselect2、svglib | BSD（分类器） |
| webencodings | 0.6.1 | cssselect2、tinycss2 | BSD（分类器） |
| pycparser | 3.11 | cffi | BSD-3-Clause |
| defusedxml | 0.7.1 | CairoSVG | PSFL |

（2.1.4 登记 15 个；packaging 为本轮对账新增——它在 2.1.4 审计环境中未进入
对账清单。）

### 3.4 闭包外但已装的 34 个包

pip、setuptools、pytest 及其插件、httpx/httpcore（测试客户端）、pip-audit
及其依赖链（cyclonedx-python-lib、license-expression、boolean.py、
pip-requirements-parser、pip_api、py-serializable、packageurl-python、
sortedcontainers、rich、Pygments、markdown-it-py、mdurl、msgpack、
CacheControl、filelock、platformdirs、truststore、tomli、tomli_w、requests、
urllib3、certifi、colorama、iniconfig、pluggy、pyparsing、httpx2、
httpcore2 等——均为开发/审计工具，不进发行包，不在 notices 列举范围。

## 4. 2.1.5 新引入包核查：apprise（可选）与 ics（实为自研）

- **requirements-app.txt 自 2.1.4 发布提交（9cb44cc）以来零改动**
  （`git log 9cb44cc..HEAD -- build/requirements-app.txt` 为空），2.1.5 没有
  新的强制运行时依赖。
- **apprise**：每日提醒「通知通道」的发送引擎，代码中为懒加载可选依赖
  （coscreen/notify.py:421 `import apprise`，未安装时该通道结构化降级为
  「通道不可用」，其余功能不受影响）；未列入 requirements-app.txt、本 venv
  未安装。**许可证实测：BSD-2-Clause**（pip download 拉取 1.9.2 与 2.2.0
  两个 wheel，METADATA 的 License/License-Expression 字段与随包 LICENSE
  文件首行「BSD 2-Clause License」一致；任务假设的 MIT 不成立）。已在
  notices 补「可选」条目。是否随发行包内置 apprise 属打包决策，本轮未改
  requirements-app.txt，交负责人裁定。
- **ics**：`.ics` 日历订阅由仓内自研模块生成（coscreen/ics_feed.py 仅
  import 标准库与 coscreen.streak，无第三方 ics 库），全仓无
  `import ics`/`from ics`；不构成第三方依赖，无需 notices 条目（已在
  notices 加一句说明）。

## 5. LICENSE 表述矛盾（已修正）

- 根目录 `LICENSE`：MIT License，Copyright (c) 2026 RUCbookshelf。
- README 十语言段落均称「MIT 许可证，详见根目录 LICENSE」（README.md:154
  中文、:279 英文等），与 LICENSE 一致，未改。
- THIRD_PARTY_NOTICES.md 原第 5 行写「本项目自身尚未选定许可证」，与上述
  两处矛盾——**按 LICENSE 文件为准修正为「本项目自身采用 MIT 许可证
  （根目录 LICENSE）」**。

## 6. README 漂移复查（本轮实测）

2.1.4 报告登记的三组失效引用在当前树已全部修复，本轮逐项复核：

| 声明 | 2.1.4 状态 | 当前实测 |
| --- | --- | --- |
| `requirements-dev.txt`（4 种语言） | 文件不存在（漂移） | README 已无该引用（grep 零命中）——已修复 |
| 目录表 `tests/`（4 种语言） | 顶层 tests/ 不存在（漂移） | README 目录表现为 `build/tests/`（:147/:275/:618/:720），与实际一致——已修复 |
| `run_pathB.bat`（4 种语言） | 文件不存在（新漂移） | README 已无该引用（grep 零命中）——已修复 |
| NAVIGATION_MAP「31 个方法入口」 | 实为 35 | NAVIGATION_MAP.md:79 现写「共 8 个分析模块、35 个方法入口，与界面『全部方法 35』一致」——已修复 |

本轮新抽查（全部属实，无需改动）：README.md:9 版本 2.1.4 与 VERSION 文件
一致；默认端口 8613 与 build/launcher.py:30 `DEFAULT_PORT = 8613` 一致；
构建输出路径（build_windows.bat/build_macos.sh/build_linux.sh）与
2.1.4 核验一致且本轮未复查脚本（未改动项）；`repair_reviewflow.bat` 存在。

## 7. 边界与未核验项

- 许可证逐包取自**当前 venv 的发行元数据**（License / License-Expression
  字段、License 分类器、dist-info 内许可文件名）；未逐个打开许可文件全文
  比对版本差异（版本不同的同一包许可条款通常稳定，但未验证）。
- CPython 3.12.7 / pip 24.1.2（notices 运行时行）与 Windows 发行包
  runtime-lock.txt、third_party_sources 内容**未重测**（发行包不在本环境）。
- pip-audit 的通告只含编号与最低修复版本；各通告的严重度/可达性未逐一
  评估（pillow/pypdf 的多数通告针对特定图片/PDF 解析路径，是否可被本应用
  输入触达需另行分析，交负责人裁定升级优先级）。
- 闭包计算按当前环境求值标记（**Python 3.11.15**，Windows）；发行包运行时
  为 CPython 3.12.7——requirements-app.txt 的标记只含 `sys_platform`（本轮
  逐条核对），闭包成员不受 Python 小版本影响，但 3.12 构建解析到的 wheel
  版本可能与本环境不同，且 `pip_audit` 结论是本环境已装版本的时点快照，
  3.12 重解后需重跑。Linux/macOS 构建会另行解析（notices 既有预警继续
  有效）。

## 附录 A：应用闭包 53 包许可证清单（2026-11-10 当前 venv 实测）

「许可（实测）」取值优先级：License-Expression（PEP 639）＞ License 字段简写
＞ License 分类器；标注（分类器）表示仅分类器可用。原始逐字段输出存
`E:/rf214tmp/debt-a/closure_licenses.out`。

| 包 | 实测版本 | 许可（实测） |
| --- | --- | --- |
| annotated-doc | 0.0.5 | MIT |
| annotated-types | 0.8.0 | MIT |
| anyio | 4.15.1 | MIT |
| bcrypt | 5.0.0 | Apache-2.0 |
| cairocffi | 1.7.1 | BSD（分类器） |
| CairoSVG | 2.9.1 | LGPL-3.0-or-later |
| cffi | 2.1.1 | MIT-0 |
| charset-normalizer | 3.5.2 | MIT |
| click | 8.5.0 | BSD-3-Clause |
| cloudpickle | 3.1.2 | BSD-3-Clause |
| cssselect2 | 0.10.1 | BSD（分类器） |
| defusedxml | 0.7.1 | PSFL |
| fastapi | 0.143.0 | MIT |
| h11 | 0.16.0 | MIT |
| idna | 3.20 | BSD-3-Clause |
| joblib | 1.6.0 | BSD-3-Clause |
| lxml | 6.1.3 | BSD-3-Clause |
| narwhals | 2.27.1 | MIT |
| numpy | 2.4.6 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| opentelemetry-api | 1.45.1 | Apache-2.0 |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause |
| pandas | 2.3.3 | BSD-3-Clause（License 字段含全文） |
| passlib | 1.7.4 | BSD |
| patsy | 1.0.3 | BSD-2-Clause（字段写 2-clause BSD） |
| pillow | 12.3.0 | MIT-CMU |
| pycparser | 3.11 | BSD-3-Clause |
| pydantic | 2.14.1 | MIT |
| pydantic_core | 2.50.1 | MIT |
| PyJWT | 2.15.1 | MIT |
| pypdf | 6.20.1 | BSD-3-Clause |
| python-dateutil | 2.9.0.post0 | BSD-3-Clause 或 Apache-2.0 双许可 |
| python-docx | 1.2.0 | MIT |
| python-multipart | 0.0.32 | Apache-2.0 |
| python-pptx | 1.0.2 | MIT |
| pytz | 2026.5 | MIT |
| RapidFuzz | 3.14.6 | MIT |
| reportlab | 4.5.1 | BSD |
| rispy | 0.10.0 | MIT（分类器） |
| rl-renderPM | 4.0.3 | BSD |
| scikit-learn | 1.9.1 | BSD-3-Clause |
| scipy | 1.17.1 | BSD-3-Clause；随包 LICENSE.txt 含 OpenBLAS（BSD-3-Clause）、LAPACK（BSD-3-Clause-Open-MPI）、GCC 运行时（GPL-3.0-or-later WITH GCC-exception-3.1）等全文 |
| six | 1.17.0 | MIT |
| starlette | 1.8.0 | BSD-3-Clause |
| statsmodels | 0.14.5 | BSD-3-Clause |
| svglib | 1.5.1 | LGPL-3.0（字段写 LGPL 3） |
| threadpoolctl | 3.7.0 | BSD-3-Clause |
| tinycss2 | 1.5.1 | BSD（分类器） |
| typing_extensions | 4.16.0 | PSF-2.0 |
| typing-inspection | 0.4.4 | MIT |
| tzdata | 2026.5 | Apache-2.0 |
| uvicorn | 0.54.0 | BSD-3-Clause |
| webencodings | 0.6.1 | BSD（分类器） |
| xlsxwriter | 3.2.9 | BSD-2-Clause |

闭包外可选依赖：apprise（1.9.2 / 2.2.0 wheel 元数据实测 BSD-2-Clause，见 §4）。

## 附录 B：本轮实际执行的命令

| 检查 | 命令/方式 |
| --- | --- |
| pip-audit | venv `python -m pip_audit -f json --progress-spinner off`（exit 1）；文本式复跑 `python -m pip_audit --progress-spinner off`（stdout 68 行存 `pip_audit_text.out`、stderr 汇总句存 `pip_audit_text.err`）；JSON 存 `E:/rf214tmp/debt-a/pip_audit.json` |
| 通告计数三口径 | 同 venv `-I` 脚本：①原始记录计数 68；②(包,通告 ID) 去重 61（重复 7 行：pip×2 六条、setuptools PYSEC-2026-3447×2）；③对全部 ID+aliases 做并查集——连通分量仍 61（零跨 ID 别名合并）；④仅计 CVE 编号得唯一 CVE 59（2026×58+2025×1）+ 仅 GHSA 通告 2 条 |
| venv Python 版本 | `python --version` → Python 3.11.15（`-I -c "import sys; print(sys.version)"` 同） |
| 已装清单 | `python -m pip list --format=freeze`（87 行，存 `E:/rf214tmp/debt-a/pip_freeze.txt`） |
| 闭包与许可证实测 | `python -I E:/rf214tmp/debt-a/closure_licenses.py`（解析 requirements-app.txt → importlib.metadata 闭包 → 逐包 License/License-Expression/分类器/许可文件；输出存 `E:/rf214tmp/debt-a/closure_licenses.out`） |
| 反向依赖 | 同 venv 内脚本：遍历全部已装包的 Requires-Dist（按当前环境求值标记）建反向索引 |
| apprise 许可证 | `python -m pip download apprise==1.9.2 "apprise>=2" --no-deps -d E:/rf214tmp/debt-a/apprise_meta`，再以 zipfile 只读提取两 wheel 的 METADATA 与 LICENSE 文本（未执行包内任何代码） |
| requirements 变更 | `git log --oneline 9cb44cc..HEAD -- build/requirements-app.txt THIRD_PARTY_NOTICES.md`（空） |
| ics 是否第三方 | `grep -rn "import apprise\|from apprise\|import ics\|from ics" --include="*.py" custom_backend/ coscreen/ build/`；coscreen/ics_feed.py 头部与 import 清单人工核读 |
| LICENSE/README | Read LICENSE；`grep -n "MIT\|许可证\|License" README.md` |
| README 漂移 | `grep -n "requirements-dev\|run_pathB\|tests/" README.md`；`grep -n "31\|35" custom_frontend/NAVIGATION_MAP.md`；`grep -n "2\.1\.\|8613" README.md`；`grep -n DEFAULT_PORT build/launcher.py` |
