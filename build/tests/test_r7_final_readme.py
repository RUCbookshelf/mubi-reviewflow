# -*- coding: utf-8 -*-
"""r7 finalfix：README 版本锚点与 VERSION 一致（破坏即红）。

缺陷（r7-finalfix 唯一一条）：VERSION 已升至 2.1.4、RELEASE_NOTES.md 由
lunafix 泳道负责，而 README.md 顶部下载锚点仍指向
releases/tag/v2.1.3、"使用引导"行仍标注 2.1.3——读者按 README 会下到旧版。

修复口径：
- README:9 下载锚点 releases/tag/v2.1.3 → releases/tag/v2.1.4；
- README:11 "2.1.3 使用引导" → "2.1.4 使用引导"；
- 历史版本段落（### 2.1.2 / 2.1.1 / 2.1.0、2.0.5 升级注记）属 changelog
  性质，保留不动。

钉子断言（撤修复必红）：
1. README 含指向当前 VERSION 的 v{version} 下载链接；
2. 顶部横幅块（首个 "## " 语言标题之前）出现的唯一版本号就是 VERSION，
   不得残留任何"当前版本语境"下的旧版本号（如 2.1.3）；
3. 全文所有 releases/tag/v* 固定锚点必须指向当前版本；
4. 上述期望值全部取自 VERSION 文件，README 与 VERSION 强一致。
"""
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

_TAG_RE = re.compile(r"releases/tag/v(\d+(?:\.\d+){1,3})")
_SEMVER_RE = re.compile(r"\d+\.\d+\.\d+(?:\.\d+)?")


def _read_version() -> str:
    raw = (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:\.\d+)?", raw):
        raise AssertionError(f"VERSION 文件内容不是纯版本号: {raw!r}")
    return raw


def _banner_block(readme: str) -> str:
    """首个 "## " 语言标题之前的顶部区块 = 当前版本语境（锚点 + 使用引导）。"""
    lines = readme.splitlines()
    for idx, line in enumerate(lines):
        if line.startswith("## "):
            return "\n".join(lines[:idx])
    return readme


class R7FinalReadmeVersionAnchorTests(unittest.TestCase):
    """README 下载锚点 / 使用引导必须与 VERSION 同步（r7-finalfix）。"""

    def test_readme_download_anchor_points_to_current_version(self):
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        version = _read_version()
        self.assertIn(
            f"https://github.com/RUCbookshelf/mubi-reviewflow/releases/tag/v{version}",
            readme,
            f"README 缺少当前版本 v{version} 的下载锚点（修前指向 v2.1.3）",
        )

    def test_readme_banner_labels_match_version(self):
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        version = _read_version()
        self.assertIn(f"**{version}:**", readme, "README 下载行版本标注与 VERSION 不一致")
        self.assertIn(f"{version} 使用引导", readme, "README 使用引导版本标注与 VERSION 不一致")

    def test_readme_banner_has_no_stale_current_version_string(self):
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        version = _read_version()
        banner = _banner_block(readme)
        found = set(_SEMVER_RE.findall(banner))
        self.assertEqual(
            found, {version},
            f"顶部横幅（当前版本语境）只允许出现 {version}，实际出现 {sorted(found)}；"
            "历史版本号只能存在于语种章节的 changelog 段落",
        )

    def test_readme_pinned_tag_links_all_point_to_current_version(self):
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        version = _read_version()
        tags = set(_TAG_RE.findall(readme))
        self.assertTrue(tags, "README 应至少存在一个 releases/tag/v* 固定下载锚点")
        self.assertEqual(
            tags, {version},
            f"README 固定锚点 {sorted(tags)} 必须全部指向当前版本 {version}",
        )


if __name__ == "__main__":
    unittest.main()
