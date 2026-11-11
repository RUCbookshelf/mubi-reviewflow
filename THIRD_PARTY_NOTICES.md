# 第三方软件与许可

此清单依据 2026-09-24 的 PathB Linux 隔离构建核对，并于 2026-11-10 按当前
开发环境实测（`build/requirements-app.txt` 的安装闭包 53 个 Python 包，
逐包读取发行元数据与随包许可文件，见 `docs/DEPS_COMPLIANCE_2.1.5.md`）
更新版本与传递依赖补全。Windows 和 macOS 构建会重新解析
`build/requirements-app.txt`，正式发布前应核对实际打入的版本。
以下许可只适用于对应的第三方项目；本项目自身采用 MIT 许可证（根目录
`LICENSE`）。

| 项目 | 用途 | 许可（发行包元数据/上游） |
| --- | --- | --- |
| CPython 3.12.7 | 捆绑的 Python 运行时 | PSF License 及随运行时提供的历史许可，见运行时 `LICENSE.txt` |
| pip 24.1.2 及其内置组件 | 随嵌入式 Python 保留的安装工具 | pip 为 MIT；内置组件版权和许可见其发行包 |
| FastAPI 0.143.0、Pydantic 2.14.1、pydantic-core 2.50.1、anyio 4.15.1、PyJWT 2.15.1、RapidFuzz 3.14.6、rispy 0.10.0、h11 0.16.0 | API、数据验证、认证、文本匹配与题录解析 | MIT |
| Uvicorn 0.54.0、Starlette 1.8.0、Click 8.5.0、idna 3.20、pypdf 6.21.4 | Web 服务及 PDF 处理 | BSD-3-Clause |
| bcrypt 5.0.0、python-multipart 0.0.32 | 密码散列与上传 | Apache-2.0 |
| passlib 1.7.4 | 密码处理（声明保留，代码直接使用 bcrypt） | BSD |
| pandas 2.3.3 | 数据处理 | BSD-3-Clause；发行包还含其所引用的第三方版权/许可 |
| NumPy 2.4.6 | pandas 的依赖 | BSD-3-Clause、0BSD、MIT、Zlib、CC0-1.0 等（按文件适用） |
| statsmodels 0.14.5 | Meta 分析及加权回归 | BSD-3-Clause；以发行包 `LICENSE.txt` 为准 |
| SciPy 1.17.1 | 分布函数、数值优化与矩阵计算 | BSD-3-Clause；二进制包内 OpenBLAS、LAPACK、GCC 运行时等的许可随包保留 |
| Patsy 1.0.3 | statsmodels 的依赖 | BSD-2-Clause；以发行包许可文件为准 |
| python-docx 1.2.0 | Word 合成报告 | MIT |
| python-pptx 1.0.2 | PowerPoint 合成报告 | MIT |
| lxml 6.1.3 | Word 与 PowerPoint Open XML 处理 | BSD-3-Clause |
| Pillow 13.0.0 | python-pptx 依赖 | MIT-CMU |
| XlsxWriter 3.2.9 | python-pptx 依赖 | BSD-2-Clause |
| python-dateutil 2.9.0.post0 | pandas 的依赖 | BSD-3-Clause 或 Apache-2.0 双许可 |
| six 1.17.0 | python-dateutil 的依赖 | MIT |
| annotated-doc 0.0.5、annotated-types 0.8.0、typing-inspection 0.4.4 | 输入支持 | MIT |
| typing-extensions 4.16.0 | 输入支持 | PSF-2.0 |
| packaging 26.3 | 通用版本/标记解析（statsmodels/Patsy 的依赖） | Apache-2.0 OR BSD-2-Clause |
| joblib 1.6.0、cloudpickle 3.1.2、threadpoolctl 3.7.0 | scikit-learn 的依赖（cloudpickle 经 joblib 引入） | BSD-3-Clause |
| narwhals 2.27.1 | scikit-learn 的依赖 | MIT |
| opentelemetry-api 1.45.1 | FastAPI 的依赖 | Apache-2.0 |
| pytz 2026.5、tzdata 2026.5 | pandas 的时区数据依赖 | MIT（pytz）、Apache-2.0（tzdata） |
| charset-normalizer 3.5.2 | ReportLab 的依赖 | MIT |
| cffi 2.1.1 | cairocffi 的 C FFI 依赖 | MIT-0 |
| cairocffi 1.7.1、cssselect2 0.10.1、tinycss2 1.5.1、webencodings 0.6.1 | CairoSVG/svglib 的解析链 | BSD |
| pycparser 3.11 | cffi 的依赖 | BSD-3-Clause |
| defusedxml 0.7.1 | CairoSVG 的依赖（XML 安全解析） | PSFL |
| apprise（可选，未随包强制安装） | 每日提醒的通知通道引擎；未安装时该通道标记不可用，其余功能不受影响 | BSD-2-Clause（1.9.2 与 2.2.0 发行包元数据及随包 LICENSE 实测一致） |
| PDF.js 4.10.38 | 浏览器内 PDF 阅读 | Apache-2.0；完整文本见 `custom_frontend/pdfjs/LICENSE` |

说明：日历订阅（.ics）由本项目代码直接生成（`coscreen/ics_feed.py`），
不引入第三方 ics 库。

PRISMA 图下方的 16 种来源引文由 [Citation Style Language 官方样式库](https://github.com/citation-style-language/styles)
的对应样式预先排版；样式库许可为 CC BY-SA 3.0。应用只分发
`custom_frontend/prisma_citations.js` 中该篇文献的排版结果，不打包 CSL 样式文件。
题录核对依据为 [BMJ 原文](https://www.bmj.com/content/372/bmj.n71.long)。

各 Python 包的 `*.dist-info` 目录和内附许可文件随嵌入式运行时分发；
CPython 许可文本也随运行时保留。NumPy 与 pandas 含多个第三方组件，
应以发行包内各自的许可文件为准。Windows 安装器可使用 Inno Setup 或
NSIS 3。若使用 NSIS，脚本指定 zlib 压缩；NSIS 核心与 zlib 模块采用
zlib/libpng 许可，允许商业使用。构建工具不决定本项目代码的许可。

上游： [Python 许可](https://docs.python.org/3.12/license.html)、
[PDF.js 许可](https://github.com/mozilla/pdf.js/blob/v4.10.38/LICENSE)、
[NSIS 许可](https://nsis.sourceforge.io/Docs/AppendixI.html)、
[Inno Setup 许可与商业使用说明](https://jrsoftware.org/isorder.php)、
[statsmodels 许可](https://github.com/statsmodels/statsmodels/blob/main/LICENSE.txt)、
[SciPy 许可](https://github.com/scipy/scipy/blob/main/LICENSE.txt)、
[Patsy 许可](https://github.com/pydata/patsy/blob/master/LICENSE.txt)、
[python-docx 许可](https://github.com/python-openxml/python-docx/blob/master/LICENSE)、
[python-pptx 许可](https://github.com/scanny/python-pptx/blob/master/LICENSE)、
[lxml 许可](https://github.com/lxml/lxml/blob/master/LICENSE.txt)、
[Pillow 许可](https://github.com/python-pillow/Pillow/blob/main/LICENSE)、
[XlsxWriter 许可](https://github.com/jmcnamara/XlsxWriter/blob/main/LICENSE.txt)、
[Apprise 许可](https://github.com/caronc/apprise/blob/master/LICENSE)、
[运行时依赖清单](build/requirements-app.txt)。

轨迹图表随程序提供经过字符子集化、重新命名的 Noto Sans CJK 字体（SIL Open Font License 1.1）；完整许可见 `coscreen/assets/trace-chart-license.txt`。
图表拉丁字母及西里尔字母使用 DejaVu Sans 子集，许可见 `coscreen/assets/trace-chart-latin-license.txt`。

## Windows 运行包补充（2026-10-02）

- scikit-learn 1.9.1：SVM/CJK排序与证据图聚类，BSD-3-Clause；NumPy/SciPy及其原生库许可随wheel保留。
- ReportLab 4.5.1、rl-renderPM 4.0.3：Windows图像渲染，BSD类许可，详见发行包LICENSE文件。
- svglib 1.5.1：SVG解析，LGPL-3.0；原始源码随Windows包的third_party_sources保留，使用未修改的可替换Python模块。上游：https://github.com/deeplook/svglib 。
- CairoSVG 2.9.1：非Windows图像渲染，LGPL-3.0-or-later；上游：https://github.com/Kozea/CairoSVG 。原始源码也保存在third_party_sources。

Windows构建的实际版本以runtime-lock.txt及site-packages内发行元数据为准；不应把Linux环境的版本表当作Windows版本锁定文件。
