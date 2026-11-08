"""R9 L7c nails - the static JavaScript gates (b02·F1-65/F1-75, b08·F1-62..F1-67,
b09·F1-78/F1-79/F1-81/F1-82).

Every nail here drives the REAL validator (build/validate_import_review_static.cjs
or build/validate_team_prisma_static.js) against a throwaway checkout assembled
from the live custom_frontend plus that validator and its script_manifest.cjs
helper. Each test's docstring names the pre-fix behaviour it pins, and each pair
has a control that must stay unchanged, so a revert turns it red while a
legitimate tree still passes.

The inventory probe forms these nails reproduce:
  * `model script tag removed: exit 0 {"syntax_files":110} <- fail-open`
  * `临时树删 RFLang.register -> exit 0 {"status":"passed","ten_language_rows":0}`
  * `register([]) -> {"status":"passed","ten_language_rows":0}`
  * `tag0 data-src skippedByGuard=true` / `tag1 attrs=' data-note="a' 截断`
  * `captures: ["ABCDEFGH\\"]` (unmodelled JS escape)
  * `<script type="application/ld+json"> -> new vm.Script SyntaxError`
  * `bad alias row accepted; languageRows=1`
  * `vm.runInNewContext('while(true){}') 5.02s 未返回`
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
NODE = (
    os.environ.get("RF_NODE")
    or shutil.which("node")
    or r"C:\Users\16073\AppData\Local\hermes\node\node.exe"
)
pytestmark = pytest.mark.skipif(not os.path.exists(NODE), reason="node runtime not found")

IMPORT_GATE = "validate_import_review_static.cjs"
TEAM_GATE = "validate_team_prisma_static.js"
HELPER = "script_manifest.cjs"


def _tree(tmp_path: Path, gates: tuple[str, ...]) -> Path:
    """A minimal checkout: the live frontend plus the requested gates verbatim.

    The copy is retried while the live frontend carries another test's
    mutation nail. The older gate nails (test_r7_mm7_relchain.py,
    test_r8_fl8_build.py) append a marker to custom_frontend/*.js and restore it
    in a finally block; a copy taken inside that window would inherit their
    marker and make this file fail for a reason it does not test. The retry is
    bounded and the failure message says so instead of reporting a bogus gate
    result.
    """
    tree = tmp_path / "tree"
    for attempt in range(20):
        if tree.exists():
            shutil.rmtree(tree)
        # 跳过未跟踪的 Mimosa 钩子状态目录（非产品文件；其深层长路径会让 copytree 失败）
        shutil.copytree(REPO / "custom_frontend", tree / "custom_frontend",
                        ignore=shutil.ignore_patterns(".mimosa"))
        (tree / "build").mkdir()
        for name in (*gates, HELPER):
            shutil.copyfile(REPO / "build" / name, tree / "build" / name)
        if not _has_foreign_nail(tree):
            return tree
        shutil.rmtree(tree, ignore_errors=True)
        time.sleep(0.25 * (attempt + 1))
    raise AssertionError("another gate nail is still mutating the live frontend; "
                         "re-run this file when no other gate nail is in flight")


FOREIGN_NAIL_MARKERS = ("/*mm7-02 nail*/", "/*r1 nail*/", "/*mm7-04 nail*/")


def _has_foreign_nail(tree: Path) -> bool:
    for path in (tree / "custom_frontend").rglob("*.js"):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return True
        if any(marker in text for marker in FOREIGN_NAIL_MARKERS):
            return True
    return False


def _run(tree: Path, gate: str, *, timeout_ms: int | None = None, wall: int = 180):
    env = dict(os.environ)
    if timeout_ms is not None:
        env["REVIEWFLOW_VALIDATOR_TIMEOUT_MS"] = str(timeout_ms)
    env.setdefault("TEMP", "E:/rf214tmp/r9/l7c/temp")
    env.setdefault("TMP", env["TEMP"])
    return subprocess.run(
        [NODE, str(tree / "build" / gate)], cwd=tree, capture_output=True,
        text=True, timeout=wall, env=env,
    )


def _edit(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text, f"fixture text not found in {path.name}: {old[:60]}"
    path.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")


MODEL_TAG = '<script src="./import_review_model.js?v=20261028-r7-frontsec1"></script>'


# --------------------------------------------------------------------------- #
# b02·F1-65 - a removed loader tag must not be a silent pass
# --------------------------------------------------------------------------- #
def test_f1_65_clean_tree_passes_and_reports_three_inline_scripts(tmp_path):
    """Control: the live tree is unchanged by the new rule."""
    tree = _tree(tmp_path, (IMPORT_GATE,))
    proc = _run(tree, IMPORT_GATE)
    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout)
    assert report["inline_scripts"] == 3
    assert report["new_panel_translation_coverage"] == "complete"


def test_f1_65_indexof_sentinel_can_no_longer_hide_a_missing_loader_tag(tmp_path):
    """F1-65 probe form: `model script tag removed -> exit 0 {"syntax_files":110}`.

    On THIS tree the observable fail-open is already closed by a different guard:
    the MM9-09 reverse-manifest rule added in r8 rejects the now-dead
    import_review_model.js (probe evidence in the run report), so this nail is
    green at bfd07ae as well. What it pins is that the old
    `html.indexOf(model) < html.indexOf(guide)` sentinel comparison - which is
    trivially true when indexOf returns -1 - is no longer the thing deciding
    whether the model is loaded: the decision now reads the parsed manifest and
    names the module."""
    tree = _tree(tmp_path, (IMPORT_GATE,))
    model = tree / "custom_frontend" / "import_review_model.js"
    _edit(tree / "custom_frontend/index.html", MODEL_TAG, "")
    # The file still exists, so only the loader-tag removal is under test.
    assert model.is_file()
    proc = _run(tree, IMPORT_GATE)
    assert proc.returncode != 0, proc.stdout
    combined = proc.stdout + proc.stderr
    assert "import_review_model.js" in combined
    assert "dead file" in combined or "must load" in combined


def test_f1_65_sentinel_comparison_is_gone_from_the_gate():
    """Structural pin for the same defect: the -1 sentinel comparison must not
    survive anywhere in the gate, because it is fail-open by construction. Both
    required modules are now looked up by name in the parsed load list."""
    source = (REPO / "build" / IMPORT_GATE).read_text(encoding="utf-8")
    assert "indexOf('import_review_model.js')<html.indexOf" not in source
    assert "indexOf('import_review_model.js') < html.indexOf" not in source
    assert "manifest.includes(required)" in source
    assert "modelPosition>=0" in source and "guidePosition>=0" in source


def test_f1_65_model_tag_before_guide_tag_order_is_still_enforced(tmp_path):
    """The real ordering constraint must still bite once both tags exist."""
    tree = _tree(tmp_path, (IMPORT_GATE,))
    html = tree / "custom_frontend/index.html"
    guide = '<script src="./import_guide.js?v=2.1.2.2"></script>'
    text = html.read_text(encoding="utf-8")
    html.write_text(text.replace(MODEL_TAG, "").replace(guide, guide + "\n" + MODEL_TAG, 1),
                    encoding="utf-8", newline="\n")
    proc = _run(tree, IMPORT_GATE)
    assert proc.returncode != 0
    assert "must load before" in proc.stderr


# --------------------------------------------------------------------------- #
# b08·F1-62 / b09·F1-81 - script-tag scanning must not be a lossy regex
# --------------------------------------------------------------------------- #
def test_f1_62_attribute_containing_gt_is_not_truncated(tmp_path):
    """RED at baseline: /<script\\b([^>]*)>/ cut the tag at the '>' inside
    data-note="a>b", so the body's first characters were re-parsed as attributes
    and the rest of the script was syntax-checked as a fragment."""
    tree = _tree(tmp_path, (IMPORT_GATE,))
    _edit(tree / "custom_frontend/index.html", MODEL_TAG,
          '<script data-note="a>b">var attributeGtSurvived = 1;</script>\n' + MODEL_TAG)
    proc = _run(tree, IMPORT_GATE)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["inline_scripts"] == 4


def test_f1_62_data_src_is_not_a_loader_and_its_body_is_checked(tmp_path):
    """RED at baseline: /\\bsrc=/ matched `data-src=`, so an inline block that the
    browser DOES execute (data-src is not a loader - only `src` is) was skipped
    and never syntax-checked. The data-src value here names a real module, so the
    loader manifest stays valid and only the inline-skip decision is under test.
    (script_manifest.cjs's own `\\bsrc` boundary is an out-of-lane follow-up.)"""
    tree = _tree(tmp_path, (IMPORT_GATE,))
    _edit(tree / "custom_frontend/index.html", MODEL_TAG,
          '<script data-src="./import_review_model.js">var broken = ;</script>\n' + MODEL_TAG)
    proc = _run(tree, IMPORT_GATE)
    assert proc.returncode != 0, "an inline block with a syntax error must fail"
    assert "SyntaxError" in proc.stderr, proc.stderr


def test_f1_62_non_javascript_script_types_are_data_not_code(tmp_path):
    """ld+json / importmap / uppercase JSON blocks are data. The old
    case-sensitive `application/json` test crashed on all three; they must be
    skipped, and genuine JS inline scripts must still be checked."""
    tree = _tree(tmp_path, (IMPORT_GATE,))
    _edit(tree / "custom_frontend/index.html", MODEL_TAG,
          '<script type="application/ld+json">{"@context":"https://schema.org"}</script>\n'
          '<script type="importmap">{"imports":{"x":"./import_review_model.js"}}</script>\n'
          '<script type="application/JSON">{"a":1}</script>\n' + MODEL_TAG)
    proc = _run(tree, IMPORT_GATE)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["inline_scripts"] == 3


# --------------------------------------------------------------------------- #
# b08·F1-66 - JavaScript escapes must be modelled
# --------------------------------------------------------------------------- #
def test_f1_66_escaped_apostrophe_is_compared_as_text(tmp_path):
    """RED at baseline: `'([^'\\n]{8,})'` stopped at the escaped quote, so the
    phantom key `Don\\` was compared and the real string was never looked up.
    A REGISTERED string containing an apostrophe must therefore pass."""
    tree = _tree(tmp_path, (IMPORT_GATE,))
    workspace = tree / "custom_frontend" / "unified_import_workspace.js"
    _edit(workspace, "I.remember=report=>{",
          "I.remember=report=>{const apostropheNail=T('Don\\'t leave this unregistered');")
    locale = tree / "custom_frontend" / "import_review_locale.js"
    locale.write_text(
        locale.read_text(encoding="utf-8")
        + "\nRFLang.register([['x','Don\\'t leave this unregistered','a','b','c','d','e','f','g','h','i']]);\n",
        encoding="utf-8", newline="\n")
    proc = _run(tree, IMPORT_GATE)
    # Two-sided: with escapes modelled the registered string is found...
    assert proc.returncode == 0, proc.stderr
    # ...and an UNregistered escaped string is still reported verbatim.
    _edit(workspace, "Don\\'t leave this unregistered", "Don\\'t leave this MISSING")
    proc = _run(tree, IMPORT_GATE)
    assert proc.returncode != 0
    assert "Don't leave this MISSING" in proc.stderr
    assert "Don\\" not in proc.stderr, "the trailing-backslash phantom key is back"


# --------------------------------------------------------------------------- #
# b08·F1-63 - coverage rule must see short display strings, not 8+ chars only
# --------------------------------------------------------------------------- #
def test_f1_63_short_unregistered_label_is_caught(tmp_path):
    """RED at baseline: the rule required 8+ characters, so 'DOI', 'Authors',
    'Cancel' and the 271 short workspace literals could never fail the gate."""
    tree = _tree(tmp_path, (IMPORT_GATE,))
    # 7 characters: exactly what the old `{8,}` quantifier could never see.
    _edit(tree / "custom_frontend/unified_import_workspace.js",
          "labels={duplicate:'Duplicate checks'",
          "labels={duplicate:'Dup Chk'")
    proc = _run(tree, IMPORT_GATE)
    assert proc.returncode != 0
    assert "Dup Chk" in proc.stderr


def test_f1_63_short_literal_in_a_field_table_is_caught(tmp_path):
    """The metadata-field table is exactly the shape the inventory named
    ('Authors/Year/Journal/DOI'): short capital-initial literals."""
    tree = _tree(tmp_path, (IMPORT_GATE,))
    _edit(tree / "custom_frontend/unified_import_workspace.js",
          "['authors','Authors'],['year','Year']",
          "['authors','Authors'],['year','Pub Yr']")
    proc = _run(tree, IMPORT_GATE)
    assert proc.returncode != 0
    assert "Pub Yr" in proc.stderr


def test_f1_63_display_helper_strings_of_any_length_are_checked(tmp_path):
    tree = _tree(tmp_path, (IMPORT_GATE,))
    _edit(tree / "custom_frontend/unified_import_workspace.js",
          "I.remember=report=>{", "I.remember=report=>{const shortNail=T('Ready?');")
    proc = _run(tree, IMPORT_GATE)
    assert proc.returncode != 0
    assert "Ready?" in proc.stderr


# --------------------------------------------------------------------------- #
# b08·F1-64 - no unbounded child processes
# --------------------------------------------------------------------------- #
def test_f1_64_top_level_infinite_loop_cannot_hang_the_gate(tmp_path):
    """RED at baseline: vm.runInNewContext had no timeout, so a checked locale
    file with a top-level `while(true){}` hung the gate forever (the inventory
    probe measured the same hang at 5.02s and beyond)."""
    tree = _tree(tmp_path, (IMPORT_GATE,))
    locale = tree / "custom_frontend" / "import_review_locale.js"
    locale.write_text("while(true){}\n" + locale.read_text(encoding="utf-8"),
                      encoding="utf-8", newline="\n")
    proc = _run(tree, IMPORT_GATE, timeout_ms=2000, wall=90)
    assert proc.returncode != 0
    assert "timed out" in (proc.stdout + proc.stderr).lower()


def test_f1_64_every_child_process_call_carries_a_timeout():
    """The execFileSync --check loop was equally unbounded; a deadline on the vm
    alone would leave the gate hanging on a pathological file."""
    for gate in (IMPORT_GATE, TEAM_GATE):
        source = (REPO / "build" / gate).read_text(encoding="utf-8")
        call_sites = [line for line in source.splitlines() if "execFileSync(" in line]
        assert call_sites, gate
        missing = [line for line in call_sites if "timeout:TIMEOUT_MS" not in line]
        assert not missing, (gate, missing)


# --------------------------------------------------------------------------- #
# b08·F1-67 - finally-block assertions were tautologies and hid real errors
# --------------------------------------------------------------------------- #
def test_f1_67_containment_checks_run_before_the_body_not_in_finally():
    """RED at baseline: `finally{assert.equal(dirname(temp),tmpdir);
    assert.match(basename(temp),/^reviewflow-inline-/);fs.rmSync(temp)}` could
    never fail on the pass path (the path is fixed two lines above) and, when
    rmSync raised EPERM, replaced the real syntax error with its own."""
    source = (REPO / "build" / IMPORT_GATE).read_text(encoding="utf-8")
    try_block = source.index("try{\n  for(const block of scriptBlocks(html))")
    finally_block = source.index("finally{")
    for guard in ("inline temp dir must live in os.tmpdir()",
                  "inline temp dir must use the reviewflow-inline- prefix"):
        position = source.index(guard)
        assert position < try_block, f"{guard} must be setup, not a finally assertion"
        assert position < finally_block
    finally_body = source[finally_block:source.index("if(failure)throw failure;")]
    assert "assert." not in finally_body, "a cleanup failure must not replace the body's error"


def test_f1_67_syntax_error_is_reported_as_a_syntax_error(tmp_path):
    tree = _tree(tmp_path, (IMPORT_GATE,))
    _edit(tree / "custom_frontend/index.html", MODEL_TAG,
          "<script>var inlineBroken = ;</script>\n" + MODEL_TAG)
    proc = _run(tree, IMPORT_GATE)
    assert proc.returncode != 0
    assert "SyntaxError" in proc.stderr, proc.stderr
    # The body's own failure must reach the caller, not be replaced by a
    # cleanup/containment failure from the finally block.
    assert "cleanup failed" not in proc.stderr
    assert "inline temp dir" not in proc.stderr


# --------------------------------------------------------------------------- #
# b02·F1-75 / b09·F1-82 - the locale pack must actually register rows
# --------------------------------------------------------------------------- #
def test_f1_75_removed_register_call_fails_the_gate(tmp_path):
    """RED at baseline: deleting RFLang.register from team_prisma_locale.js left
    the stub un-run, printed {"status":"passed","ten_language_rows":0} and
    exited 0."""
    tree = _tree(tmp_path, (TEAM_GATE,))
    _edit(tree / "custom_frontend/team_prisma_locale.js",
          "  RFLang.register(rows);", "  /* register removed by the nail */")
    proc = _run(tree, TEAM_GATE)
    assert proc.returncode != 0, proc.stdout
    assert "registered no ten-language rows" in proc.stderr


def test_f1_82_register_of_an_empty_list_fails_the_gate(tmp_path):
    """RED at baseline: `register([])` - zero rows validated - printed
    status:'passed' with ten_language_rows 0."""
    tree = _tree(tmp_path, (TEAM_GATE,))
    _edit(tree / "custom_frontend/team_prisma_locale.js",
          "  RFLang.register(rows);", "  RFLang.register([]);")
    proc = _run(tree, TEAM_GATE)
    assert proc.returncode != 0
    assert "ten-language rows" in proc.stderr


def test_f1_82_clean_tree_still_reports_every_row(tmp_path):
    """Control: the fail-closed rule must not shrink the validated set. The live
    locale pack registers 101 rows and the gate must still count all of them."""
    tree = _tree(tmp_path, (TEAM_GATE,))
    proc = _run(tree, TEAM_GATE)
    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout)
    assert report["status"] == "passed"
    assert report["ten_language_rows"] == 101
    assert report["language_pack_files"] == 19


# --------------------------------------------------------------------------- #
# b08·F1-62 (L7c 登记) - script_manifest.cjs 的 src 判定
# --------------------------------------------------------------------------- #
def test_manifest_data_src_is_not_a_loader(tmp_path):
    """RED at baseline: script_manifest.cjs 用 /<script\\b[^>]*\\bsrc\\s*=/ 扫
    载入列表，`data-src="./ghost_module.js"` 被当成了 loader——manifest 因此
    要求一个浏览器根本不加载的文件，整道门禁因一个非载入属性而失败
    （探针：`manifest module is missing on disk: ghost_module.js`）。修后只有
    精确的 `src` 属性算载入，data-src 整块被忽略。"""
    tree = _tree(tmp_path, (IMPORT_GATE,))
    _edit(tree / "custom_frontend/index.html", MODEL_TAG,
          '<script data-src="./ghost_module.js"></script>\n' + MODEL_TAG)
    assert not (tree / "custom_frontend" / "ghost_module.js").exists()
    proc = _run(tree, IMPORT_GATE)
    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout)
    assert report["syntax_files"] == 116  # ghost_module.js 未混进载入列表；r11 ERIC 入口 +1


def test_manifest_data_src_cannot_mask_a_dead_file(tmp_path):
    """同族的反向后果：data-src 曾被当成载入，于是 MM9-09 的"死文件"规则
    被一个假载入欺骗（探针：同一死文件，加 data-src 前 fail、加之后 pass）。"""
    tree = _tree(tmp_path, (IMPORT_GATE,))
    (tree / "custom_frontend" / "zz_dead_probe.js").write_text("window.ZZ=1;\n", encoding="utf-8")
    _edit(tree / "custom_frontend/index.html", MODEL_TAG,
          '<script data-src="./zz_dead_probe.js"></script>\n' + MODEL_TAG)
    proc = _run(tree, IMPORT_GATE)
    assert proc.returncode != 0, "假载入不得让死文件蒙混过关"
    assert "dead file" in proc.stderr, proc.stderr


def test_manifest_quoted_gt_does_not_hide_a_real_loader(tmp_path):
    """RED at baseline: `[^>]*` 在引号内的 '>' 处截断，同一标签后面的真 `src`
    看不到——import_review_model.js 从载入列表消失，MM9-09 反手把它判成死文件
    （探针：model present: False）。修后属性扫描读到真正的标签结尾。"""
    tree = _tree(tmp_path, (IMPORT_GATE,))
    _edit(tree / "custom_frontend/index.html", MODEL_TAG,
          MODEL_TAG.replace('<script src=', '<script data-note="a>b" src='))
    proc = _run(tree, IMPORT_GATE)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["syntax_files"] == 116


def test_manifest_helper_only_accepts_the_exact_src_attribute(tmp_path):
    """控制（helper 级）：只有精确的 `src` 取到值，data-src / lazy-src 一律不算；
    正常带 src 的标签照旧全部解析（不把真 loader 一起丢掉）。"""
    tree = _tree(tmp_path, (IMPORT_GATE,))
    html = tree / "custom_frontend" / "index.html"
    text = html.read_text(encoding="utf-8")
    html.write_text(
        text.replace(MODEL_TAG,
                     '<script data-src="./ghost_one.js" lazy-src="./ghost_two.js"></script>\n'
                     + MODEL_TAG),
        encoding="utf-8", newline="\n")
    driver = tmp_path / "probe.cjs"
    driver.write_text(
        "const {loadScriptManifest}=require(%r);\n"
        "const list=loadScriptManifest(%r);\n"
        "console.log(JSON.stringify({count:list.length,"
        "model:list.includes('import_review_model.js'),"
        "ghost:list.filter(f=>f.startsWith('ghost_'))}));\n"
        % (str(tree / "build" / "script_manifest.cjs").replace("\\", "/"),
           str(tree).replace("\\", "/")),
        encoding="utf-8")
    proc = subprocess.run([NODE, str(driver)], capture_output=True, text=True,
                          timeout=120, cwd=str(tree))
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["ghost"] == [], f"data-src/lazy-src 混进了载入列表：{out['ghost']}"
    assert out["model"] is True, "真 loader 不得因同标签的其他属性被漏掉"
    assert out["count"] == 116


# --------------------------------------------------------------------------- #
# b09·F1-79 - alias groups were never validated
# --------------------------------------------------------------------------- #
def test_f1_79_alias_placeholder_mismatch_fails_the_gate(tmp_path):
    """RED at baseline: the stub only inspected row[0..9]; `bad alias row
    accepted; languageRows=1`. Aliases are what historical cached controls
    still resolve through, so a mismatched placeholder silently breaks them."""
    tree = _tree(tmp_path, (TEAM_GATE,))
    # Every primary string carries {0}, so the pre-existing primary-row
    # placeholder check passes and only the alias rule can fail this row.
    row = ("  {const row=Array.from({length:10},(_,i)=>'Alias Nail '+i+' {0}');"
           "row.aliases=Array.from({length:10},(_,i)=>'Alias Nail '+i+' {1}');"
           "rows.push(row);}\n  RFLang.register(rows);")
    _edit(tree / "custom_frontend/team_prisma_locale.js", "  RFLang.register(rows);", row)
    proc = _run(tree, TEAM_GATE)
    assert proc.returncode != 0
    assert "Mismatched alias placeholders" in proc.stderr


def test_f1_79_alias_group_that_is_not_ten_wide_fails_the_gate(tmp_path):
    tree = _tree(tmp_path, (TEAM_GATE,))
    # The 'needs' row keeps a single-quoted ten-language alias group; dropping
    # two entries leaves an eight-wide group that can no longer be aligned with
    # the ten primary strings.
    _edit(tree / "custom_frontend/team_prisma_locale.js",
          "needs.aliases=['待定','Maybe','Peut-être',",
          "needs.aliases=['待定',")
    proc = _run(tree, TEAM_GATE)
    assert proc.returncode != 0


def test_f1_79_live_alias_groups_pass_the_new_rule(tmp_path):
    """Control: the 94 real alias groups (18 of them 20-wide: primary + alias
    per language) are valid under the slot-indexed rule - no false positive."""
    tree = _tree(tmp_path, (TEAM_GATE,))
    proc = _run(tree, TEAM_GATE)
    assert proc.returncode == 0, proc.stderr


# --------------------------------------------------------------------------- #
# b09·F1-78 - non-JS <script> blocks must not reach the parser
# --------------------------------------------------------------------------- #
def test_f1_78_ld_json_and_importmap_blocks_are_skipped(tmp_path):
    """RED at baseline: the team gate skipped only \\bsrc=/type='application/json',
    so an ld+json or importmap block crashed with `Unexpected token ':'`."""
    tree = _tree(tmp_path, (TEAM_GATE,))
    _edit(tree / "custom_frontend/index.html", MODEL_TAG,
          '<script type="application/ld+json">{"@context":"https://schema.org"}</script>\n'
          '<script type="importmap">{"imports":{"x":"./import_review_model.js"}}</script>\n'
          + MODEL_TAG)
    proc = _run(tree, TEAM_GATE)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["inline_scripts"] == 3


def test_f1_78_and_f1_81_uppercase_json_type_and_attribute_gt(tmp_path):
    """F1-81: the type test was case-sensitive and a '>' inside a quoted
    attribute truncated the tag; both broke the skip decision."""
    tree = _tree(tmp_path, (TEAM_GATE,))
    _edit(tree / "custom_frontend/index.html", MODEL_TAG,
          '<script type="application/JSON">{"a":1}</script>\n'
          '<script data-note="a>b">var okAttribute = 1;</script>\n' + MODEL_TAG)
    proc = _run(tree, TEAM_GATE)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["inline_scripts"] == 4


# --------------------------------------------------------------------------- #
# b08·F1-77 - a deleted tracked file must not crash the gate
# --------------------------------------------------------------------------- #
def _git_tree(tmp_path: Path) -> Path:
    tree = _tree(tmp_path, (TEAM_GATE,))
    git = shutil.which("git")
    assert git, "git is required for this nail"
    blank = dict(os.environ, GIT_AUTHOR_NAME="n", GIT_AUTHOR_EMAIL="n@example.test",
                 GIT_COMMITTER_NAME="n", GIT_COMMITTER_EMAIL="n@example.test")
    for command in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "init"]):
        subprocess.run([git, *command], cwd=tree, check=True, capture_output=True, env=blank)
    return tree


def test_f1_77_deleted_tracked_file_does_not_crash_the_gate(tmp_path):
    """F1-77 probe form: `临时仓库删 workflow_controls.js -> exit 1 Error: ENOENT
    ... validate_team_prisma_static.js:18`; restoring the file gave exit 0.

    On THIS tree the reported trigger is already gone for two independent
    reasons, both verified on the bfd07ae copies during this run:
      * workflow_controls.js is in the MM9-09 `required` list, so deleting it
        now fails with "required validator input is missing" (control test
        below) - it can never reach the read;
      * an ordinary tracked .js deletion exits 0 with no ENOENT because
        `changed.filter(existing)` intersects the deletion away (probe: deleting
        title_duplicate_review.js on the bfd07ae gate -> exit 0, status passed).
    Nothing in the gate is left to harden, so this test pins the surviving
    contract instead of inventing a red: an ordinary deleted module must not
    crash the gate, and the required-file contract must still fire."""
    tree = _git_tree(tmp_path)
    (tree / "custom_frontend" / "title_duplicate_review.js").unlink()
    proc = _run(tree, TEAM_GATE)
    assert proc.returncode == 0, proc.stderr
    assert "ENOENT" not in proc.stderr
    assert json.loads(proc.stdout)["status"] == "passed"


def test_f1_77_a_required_input_being_deleted_still_fails(tmp_path):
    """Control: the fix must not swallow the MM9-09 required-file contract."""
    tree = _git_tree(tmp_path)
    (tree / "custom_frontend" / "workflow_controls.js").unlink()
    proc = _run(tree, TEAM_GATE)
    assert proc.returncode != 0
    assert "required validator input is missing" in proc.stderr
