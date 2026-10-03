# 第三方软件与许可

此清单依据 2026-09-24 的 PathB Linux 隔离构建核对。Windows 和 macOS
构建会重新解析 `build/requirements-app.txt`，正式发布前应核对实际打入的版本。
以下许可只适用于对应的第三方项目；本项目自身尚未选定许可证。

| 项目 | 用途 | 许可（发行包元数据/上游） |
| --- | --- | --- |
| CPython 3.12.7 | 捆绑的 Python 运行时 | PSF License 及随运行时提供的历史许可，见运行时 `LICENSE.txt` |
| pip 24.1.2 及其内置组件 | 随嵌入式 Python 保留的安装工具 | pip 为 MIT；内置组件版权和许可见其发行包 |
| FastAPI 0.141.1、Pydantic 2.13.5、pydantic-core 2.46.5、anyio 4.15.1、PyJWT 2.14.0、RapidFuzz 3.14.6、rispy 0.10.0 | API、数据验证、认证、文本匹配与题录解析 | MIT |
| Uvicorn 0.53.0、Starlette 1.7.0、Click 8.5.0、h11 0.16.0、idna 3.20、pypdf 6.19.0 | Web 服务及 PDF 处理 | BSD-3-Clause |
| bcrypt 5.0.0、python-multipart 0.0.32 | 密码散列与上传 | Apache-2.0 |
| passlib 1.7.4 | 密码处理 | BSD |
| pandas 2.x（本轮新构建将解析实际版本） | 数据处理 | BSD-3-Clause；发行包还含其所引用的第三方版权/许可 |
| NumPy 2.5.3 | pandas 的依赖 | BSD-3-Clause、0BSD、MIT、Zlib、CC0-1.0 等（按文件适用） |
| statsmodels 0.14.5 | Meta 分析及加权回归 | BSD-3-Clause；以发行包 `LICENSE.txt` 为准 |
| SciPy（构建时解析版本） | 分布函数、数值优化与矩阵计算 | BSD-3-Clause；二进制包内 OpenBLAS、LAPACK 等的许可随包保留 |
| Patsy（构建时解析版本） | statsmodels 的依赖 | 主要为 BSD-2-Clause；以发行包 `LICENSE.txt` 为准 |
| python-docx 1.2.0 | Word 合成报告 | MIT |
| python-pptx 1.0.2 | PowerPoint 合成报告 | MIT |
| lxml 6.1.3 | Word 与 PowerPoint Open XML 处理 | BSD-3-Clause |
| Pillow 12.3.0 | python-pptx 依赖 | MIT-CMU |
| XlsxWriter 3.2.9 | python-pptx 依赖 | BSD-2-Clause |
| python-dateutil 2.9.0.post0 | pandas 的依赖 | BSD-3-Clause 或 Apache-2.0 双许可 |
| six 1.17.0 | python-dateutil 的依赖 | MIT |
| annotated-doc 0.0.5、annotated-types 0.8.0、typing-inspection 0.4.4 | 类型支持 | MIT |
| typing-extensions 4.16.0 | 类型支持 | PSF-2.0 |
| PDF.js 4.10.38 | 浏览器内 PDF 阅读 | Apache-2.0；完整文本见 `custom_frontend/pdfjs/LICENSE` |

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
[运行时依赖清单](build/requirements-app.txt)。

轨迹图表随程序提供经过字符子集化、重新命名的 Noto Sans CJK 字体（SIL Open Font License 1.1）；完整许可见 `coscreen/assets/trace-chart-license.txt`。
图表拉丁字母及西里尔字母使用 DejaVu Sans 子集，许可见 `coscreen/assets/trace-chart-latin-license.txt`。

## Windows 运行包补充（2026-10-02）

- scikit-learn：SVM/CJK排序与证据图聚类，BSD-3-Clause；NumPy/SciPy及其原生库许可随wheel保留。
- ReportLab、rl-renderPM：Windows图像渲染，BSD类许可，详见发行包LICENSE文件。
- svglib 1.5.1：SVG解析，LGPL-3.0；原始源码随Windows包的third_party_sources保留，使用未修改的可替换Python模块。上游：https://github.com/deeplook/svglib 。
- CairoSVG：非Windows图像渲染，LGPL-3.0；上游：https://github.com/Kozea/CairoSVG 。原始源码也保存在third_party_sources。

Windows构建的实际版本以runtime-lock.txt及site-packages内发行元数据为准；不应把Linux环境的版本表当作Windows版本锁定文件。
