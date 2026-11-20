"""2.1.6 REL-01/REL-02/REL-02b 发布守卫（行为钉）：触发面、node24 钉与资产完整性。

核心方法——不拿文本镜像当行为证据：工作流的 shell 步骤（"Verify release
asset completeness"、"Generate checksums"、"Verify checksums"、"Point release
tag to this build (P1-1 tag guards)"）从**解析后的** .github/workflows/release.yml
提取 ``run:`` 脚本，写入临时 .sh，用真实 bash 在受控临时环境（临时裸仓库 /
临时 release-assets 目录）执行。工作流文本漂移即测试漂移，不存在陈旧拷贝
造成的假绿；shell 行为（标签不移动、缺件拒绝、checksum 拦截篡改、同提交
幂等）由进程退出码 + ls-remote 实测断言。

REL-01a（触发面，主控裁定 2026-11-14）：publish 仅 push 标签事件运行——
    release/** 分支推送与 workflow_dispatch（即使手动选择标签 ref）只构建。
    2.1.5 实证 branch push 可发正式版（run 43541772593），job 级 ``if:`` 收口。
REL-01b（node24 钉）：GitHub runner 已移除 node20（2026-09），八处 ``uses:``
    钉到官方 action.yml ``runs.using: node24`` 的最小主版本。一手证据
    （raw action.yml @ tag + commits API 主标签对照，2026-11-14 实测）：
      actions/checkout@v5          = v5.1.0 (fbc6f399) using: node24
      actions/setup-python@v6      = v6.3.0 (ece7cb06) using: node24
      actions/upload-artifact@v6   = v6.0.0 (b7c566a7) using: node24
      actions/download-artifact@v7 = v7.0.0 (37930b1c) using: node24
      softprops/action-gh-release@v3 = v3.0.4 (8a0cd651) using: node24
    注意 upload@v5、download@v5/v6 实测 runs.using 仍 node20（其发布说明自称
    "supports Node 24" 但未翻面）——不得回退到这些主版本。
REL-02（资产完整性）：download-artifact 的 merge-multiple 只交付"存在的"
    产物——缺件不报错，不完整资产集会一路走到发版。六件资产（三平台安装包
    + verify_release_payload.py 写出的三份 build-manifest）必须齐全，缺一即败。
REL-02b（产物汇集前移，Task8 主控裁定 2026-11-14）：新增只读 assemble 作业
    （needs build、无 checkout、contents:read）承担 download 汇集→六载荷检查
    →SHA 生成（显式六载荷清单，永不包含 SHA256SUMS.txt 自身）→checksum 实际
    校验→上传单一 desktop-release-assets 资产包；publish 改 needs assemble、
    按 name 取包，在任何 tag/push/release 动作前 ``sha256sum -c`` 再验一次。
    分支推送与 dispatch 演练因此全程复现产物传输与校验（原 REL-01 验收缺口）。

运行环境：bash + git（Git for Windows / CI runner 自带）；pyyaml 由
build/requirements-test.txt 提供（CI 安装行已接 -r 该文件）。
本地：E:/rf216-scratch/validation-venv/Scripts/python.exe -m pytest
build/tests/test_2_1_6_release_workflow.py -q（逐文件跑，勿并入整目录联跑）。

纪律：本文件不写进程级 os.environ（子进程 env 仅作参数传递）；所有 git
沙箱（裸仓库/克隆/标签/提交）都在 pytest tmp_path 下，绝不触碰仓库远端。
"""
import os
import shutil
import subprocess
from collections import Counter
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
WORKFLOW = REPO / ".github" / "workflows" / "release.yml"

#: REL-01b：八处 uses 钉的精确多重集（checkout 在 build/publish 各一；
#: upload 在 build 与 assemble 各一；download 在 assemble 与 publish 各一）。
NODE24_PIN_MULTISET = Counter({
    "actions/checkout@v5": 2,
    "actions/setup-python@v6": 1,
    "actions/upload-artifact@v6": 2,
    "actions/download-artifact@v7": 2,
    "softprops/action-gh-release@v3": 1,
})

#: REL-02b：assemble 汇总上传的唯一资产包名（刻意避开 reviewflow-* 前缀）。
BUNDLE_ARTIFACT = "desktop-release-assets"

#: REL-01a：主控裁定的 publish 触发条件（精确文本；放宽/改写即红）。
PUBLISH_IF = "github.event_name == 'push' && startsWith(github.ref, 'refs/tags/')"

#: REL-02：publish 必须拿齐的六件资产（matrix 三个安装包 +
#: verify_release_payload.py 以 matrix.command 写出的三份清单）。
REQUIRED_ASSETS = (
    "ReviewFlow-Setup-x64.exe",
    "ReviewFlow.dmg",
    "ReviewFlow-x86_64.AppImage",
    "build-manifest-windows.json",
    "build-manifest-macos.json",
    "build-manifest-linux.json",
)

ASSET_CHECK_STEP = "Verify release asset completeness"
TAG_GUARD_STEP = "Point release tag to this build (P1-1 tag guards)"


def _load_workflow():
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _job_steps(doc, job):
    return doc["jobs"][job].get("steps", [])


def _step_by_name(doc, job, name):
    for step in _job_steps(doc, job):
        if step.get("name") == name:
            return step
    pytest.fail(f"release.yml publish 作业缺少步骤 {name!r}——步骤被改名/删除即红")


def _write_bash_script(tmp_path, body, filename):
    """落盘为 LF 脚本（CRLF 会污染 bash 变量比较），返回 POSIX 风格路径。"""
    script = tmp_path / filename
    script.write_bytes(body.replace("\r\n", "\n").encode("utf-8"))
    return script.as_posix()


def _resolve_bash():
    """从 git 安装树推导同树 bash——不裸信 PATH 首位。

    本机仓库根残留一个旧 cygwin bash.exe（连同旧 msys-2.0.dll，历史事故
    遗留的未跟踪文件），会劫持 Windows PATH 首位的 ``bash`` 解析：其运行时
    与 git 树的 msys 共享节冲突（NtCreateDirectoryObject 0xC0000022），
    且会让 git 把本地远端 URL 误按 scp 语法解析（host "C"）。同树 bash 与
    git/sh 共享一致的 msys 运行时，链路（bash→git→sh→upload-pack）实测通。
    """
    candidates = []
    git_exe = shutil.which("git")
    if git_exe:
        for ancestor in Path(git_exe).resolve().parents:
            candidates.append(ancestor / "usr" / "bin" / "bash.exe")
            candidates.append(ancestor / "bin" / "bash")
    candidates.append(shutil.which("bash"))
    for cand in candidates:
        if cand and Path(cand).is_file():
            return str(cand)
    pytest.fail("找不到可执行 bash：行为守卫需要 bash 运行提取的工作流脚本")


def _run_bash(script_posix, cwd, env_overrides):
    """在 cwd 执行脚本；env 只作为子进程参数，永不写回本进程环境。"""
    env = dict(os.environ)
    env.update(env_overrides)
    return subprocess.run(
        [_resolve_bash(), script_posix], cwd=str(cwd), env=env,
        capture_output=True, text=True, timeout=120,
    )


def _git(cwd, *args):
    """临时沙箱内跑 git；身份用 -c 注入，不读也不写全局配置。"""
    proc = subprocess.run(
        ["git", "-c", "user.name=rel-guard-test", "-c", "user.email=rel@example.com",
         *args],
        cwd=str(cwd), capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, f"git {args} 失败:\n{proc.stderr}"
    return proc


def _git_sandbox(tmp_path, reject_push=False):
    """临时裸仓库 + 克隆两提交（A→B，HEAD=B）。

    返回 (bare, work, sha_a, sha_b)。沙箱全部位于 pytest tmp_path，测试中的
    提交/标签只作用于这个一次性远端，与仓库真实远端零交集。
    """
    bare = tmp_path / "remote.git"
    _git(tmp_path, "init", "--bare", "-b", "main", str(bare))
    work = tmp_path / "work"
    _git(tmp_path, "clone", str(bare), str(work))
    (work / "VERSION").write_text("1.2.3\n", encoding="utf-8")
    _git(work, "add", "-A")
    _git(work, "commit", "-m", "A")
    _git(work, "push", "origin", "main")
    (work / "VERSION").write_text("1.2.4\n", encoding="utf-8")
    _git(work, "add", "-A")
    _git(work, "commit", "-m", "B")
    sha_b = _git(work, "rev-parse", "HEAD").stdout.strip()
    sha_a = _git(work, "rev-parse", "HEAD~1").stdout.strip()
    if reject_push:
        hook = bare / "hooks" / "pre-receive"
        hook.write_bytes(b"#!/bin/sh\necho 'sandbox: rejecting push' >&2\nexit 1\n")
        hook.chmod(0o755)
    return bare, work, sha_a, sha_b


def _remote_tag(bare, name):
    """ls-remote 单标签：返回 {raw_sha, peeled_sha}（无则两者皆 None）。"""
    proc = _git(bare, "ls-remote", str(bare), f"refs/tags/{name}", f"refs/tags/{name}^{{}}")
    raw = peeled = None
    for line in proc.stdout.splitlines():
        sha, ref = line.split("\t")
        if ref.endswith("^{}"):
            peeled = sha
        else:
            raw = sha
    return {"raw": raw, "peeled": peeled}


def _run_tag_guard(tmp_path, work, *, ref_type, ref, sha, version):
    """从解析后的 release.yml 提取 P1-1 守卫脚本并在克隆里执行（CI 语义 env）。"""
    doc = _load_workflow()
    body = _step_by_name(doc, "publish", TAG_GUARD_STEP)["run"]
    script = _write_bash_script(tmp_path, body, "tag-guard.sh")
    return _run_bash(script, work, {
        "GITHUB_REF_TYPE": ref_type,
        "GITHUB_REF": ref,
        "GITHUB_SHA": sha,
        "APP_VERSION": version,
    })


class TestReleaseWorkflowConfigNails:
    """少量配置钉（数据面）：钉版本多重集、作业图、触发条件与关键步骤顺序。

    行为证据在下方三个行为类；此处只钉"配置即数据"的部分——
    钉文本就是钉参数，不冒充行为。
    """

    def test_uses_pins_are_the_node24_native_majors(self):
        doc = _load_workflow()
        uses = Counter(
            step["uses"]
            for job in ("build", "assemble", "publish")
            for step in _job_steps(doc, job)
            if "uses" in step
        )
        assert uses == NODE24_PIN_MULTISET, (
            "全部八处 uses 钉必须是 node24 原生最小主版本集合"
            f"（{dict(NODE24_PIN_MULTISET)}）；upload@v5、download@v5/v6 实测"
            "仍 node20，不允许出现"
        )

    def test_job_graph_build_feeds_assemble_feeds_publish(self):
        doc = _load_workflow()
        jobs = doc["jobs"]
        needs = jobs["publish"]["needs"]
        assert needs in ("assemble", ["assemble"]), (
            "publish 必须依赖 assemble——按名取走已验证的完整资产包"
        )
        assert jobs["assemble"]["needs"] in ("build", ["build"])
        assert "needs" not in jobs["build"]
        # 分支推送/手动演练必须走到 assemble（无事件门）——产物汇集与校验
        # 前移后，演练链路 build→assemble 全程可复现（REL-02b 验收目标）。
        assert "if" not in jobs["assemble"], (
            "assemble 不得加事件门——分支推送与 dispatch 演练必须经过产物汇集与校验"
        )

    def test_assemble_is_readonly_no_checkout_and_read_only_permissions(self):
        doc = _load_workflow()
        job = doc["jobs"]["assemble"]
        assert job["permissions"] == {"contents": "read"}, (
            "assemble 是只读产物准备：不得持有仓库写权限"
        )
        uses = [s.get("uses", "") for s in _job_steps(doc, "assemble")]
        assert not any(u.startswith("actions/checkout") for u in uses), (
            "assemble 不检出源码——没有 checkout 就没有凭据持久化面"
        )
        build_checkout = next(
            s for s in _job_steps(doc, "build")
            if s.get("uses", "").startswith("actions/checkout")
        )
        assert build_checkout["with"]["persist-credentials"] is False, (
            "build 的 checkout 必须继续不持久化凭据（L-03）"
        )

    def test_publish_if_gates_to_push_tag_events_only(self):
        assert _load_workflow()["jobs"]["publish"].get("if") == PUBLISH_IF, (
            "publish 触发条件必须精确等于主控裁定式：仅 push 标签事件进入；"
            "branch push 与 workflow_dispatch（含手动选标签 ref）只构建"
        )

    def test_build_job_has_no_event_gate(self):
        assert "if" not in _load_workflow()["jobs"]["build"], (
            "build 作业不得加事件门——release 分支与 dispatch 演练必须仍能构建"
        )

    def test_publish_gates_assets_before_checksums_tag_and_release(self):
        doc = _load_workflow()
        names = [
            step.get("name") or step.get("uses", "")
            for step in _job_steps(doc, "publish")
        ]
        i_assets = names.index(ASSET_CHECK_STEP)
        i_sums = names.index("Verify checksums")
        i_tag = names.index(TAG_GUARD_STEP)
        i_release = names.index("Create versioned release and upload installers")
        assert i_assets < i_sums < i_tag < i_release, (
            "publish 步骤顺序必须是：资产完整性 → checksum 校验 → P1-1 标签"
            "守卫 → 发版——checksum 校验失败必须发生在任何打标签/推标签/"
            "建发行版动作之前"
        )

    def test_artifact_transfer_wiring(self):
        doc = _load_workflow()
        # build：每平台一个 reviewflow-<platform> 产物，上传缺件即错
        build_upload = [
            s for s in _job_steps(doc, "build")
            if s.get("uses", "").startswith("actions/upload-artifact")
        ]
        assert len(build_upload) == 1, "build 作业必须有且只有一个产物上传步"
        with_block = build_upload[0]["with"]
        assert with_block["name"] == "reviewflow-${{ matrix.platform }}"
        assert with_block["path"] == "release-assets/*"
        assert with_block["if-no-files-found"] == "error", (
            "上传侧缺文件必须报错（if-no-files-found: error）"
        )
        # assemble：pattern 合并三平台产物 → 汇集上传单一 desktop-release-assets
        asm_download = [
            s for s in _job_steps(doc, "assemble")
            if s.get("uses", "").startswith("actions/download-artifact")
        ]
        assert len(asm_download) == 1, "assemble 作业必须有且只有一个产物下载步"
        dl_with = asm_download[0]["with"]
        assert dl_with["pattern"] == "reviewflow-*"
        assert dl_with["merge-multiple"] is True, (
            "三平台产物按 pattern 合并进同一 release-assets 目录"
        )
        asm_upload = [
            s for s in _job_steps(doc, "assemble")
            if s.get("uses", "").startswith("actions/upload-artifact")
        ]
        assert len(asm_upload) == 1, "assemble 作业必须有且只有一个资产包上传步"
        up_with = asm_upload[0]["with"]
        assert up_with["name"] == BUNDLE_ARTIFACT, (
            "资产包名必须固定且避开 reviewflow-* 前缀（防被自己的 pattern 捕获）"
        )
        assert up_with["path"] == "release-assets/*"
        assert up_with["if-no-files-found"] == "error"
        # publish：按名取走已验证的完整资产包（不再 pattern 合并平台产物）
        pub_download = [
            s for s in _job_steps(doc, "publish")
            if s.get("uses", "").startswith("actions/download-artifact")
        ]
        assert len(pub_download) == 1, "publish 作业必须有且只有一个资产包下载步"
        assert pub_download[0]["with"]["name"] == BUNDLE_ARTIFACT, (
            "publish 必须按 name 精确下载 assemble 的完整资产包"
        )
        assert "pattern" not in pub_download[0]["with"], (
            "publish 不得再用 pattern 合并——必须消费 assemble 已验证的那一份"
        )


class TestReleaseAssetCompletenessBehavior:
    """REL-02 行为：提取工作流资产完整性脚本，对真实目录执行。

    assemble 与 publish 各持一份同型脚本（双保险），两份都必须独立通过
    行为验证——任何一份漂移（改弱/改删）即红。
    """

    def _run(self, tmp_path, job, present, extras=()):
        assets = tmp_path / "release-assets"
        assets.mkdir()
        for name in present:
            (assets / name).write_bytes(f"asset-bytes:{name}".encode("utf-8"))
        for name in extras:
            (assets / name).write_bytes(b"extra")
        doc = _load_workflow()
        body = _step_by_name(doc, job, ASSET_CHECK_STEP)["run"]
        script = _write_bash_script(tmp_path, body, f"asset-check-{job}.sh")
        return _run_bash(script, tmp_path, {})

    @pytest.mark.parametrize("job", ["assemble", "publish"])
    def test_complete_set_passes(self, tmp_path, job):
        proc = self._run(tmp_path, job, REQUIRED_ASSETS)
        assert proc.returncode == 0, proc.stderr

    @pytest.mark.parametrize("job", ["assemble", "publish"])
    def test_missing_installer_is_rejected_and_only_the_missing_one(self, tmp_path, job):
        present = [f for f in REQUIRED_ASSETS if f != "ReviewFlow-Setup-x64.exe"]
        proc = self._run(tmp_path, job, present)
        assert proc.returncode != 0, "缺 Windows 安装包必须失败"
        assert "::error::missing release asset: ReviewFlow-Setup-x64.exe" in proc.stderr
        assert proc.stderr.count("missing release asset") == 1, (
            "只报缺失的那一件——齐全资产不得误报"
        )

    @pytest.mark.parametrize("job", ["assemble", "publish"])
    def test_missing_manifest_is_rejected(self, tmp_path, job):
        present = [f for f in REQUIRED_ASSETS if f != "build-manifest-linux.json"]
        proc = self._run(tmp_path, job, present)
        assert proc.returncode != 0
        assert "::error::missing release asset: build-manifest-linux.json" in proc.stderr

    @pytest.mark.parametrize("job", ["assemble", "publish"])
    def test_empty_directory_is_rejected(self, tmp_path, job):
        proc = self._run(tmp_path, job, ())
        assert proc.returncode != 0
        assert proc.stderr.count("missing release asset") == len(REQUIRED_ASSETS)

    @pytest.mark.parametrize("job", ["assemble", "publish"])
    def test_extra_files_do_not_block(self, tmp_path, job):
        proc = self._run(tmp_path, job, REQUIRED_ASSETS, extras=("SHA256SUMS.txt", "stray.log"))
        assert proc.returncode == 0, "多出的文件必须放行（缺一才拒，不做白名单）"


class TestChecksumAssemblyBehavior:
    """REL-02b 行为：提取 assemble 的 SHA 生成脚本与两作业的校验脚本实测。

    生成必须恰好覆盖六载荷（不含 SHA256SUMS.txt 自身、不吸收多余文件）；
    assemble 与 publish 的校验对完整资产包通过、任一已记录载荷被篡改即
    失败——发布侧的失败发生在一切远端写动作之前（顺序钉见配置类）。
    """

    def _assets_dir(self, tmp_path, *, extra=("stray.log",)):
        assets = tmp_path / "release-assets"
        assets.mkdir()
        for name in REQUIRED_ASSETS:
            (assets / name).write_bytes(f"asset-bytes:{name}".encode("utf-8"))
        for name in extra:
            (assets / name).write_bytes(b"extra")
        return assets

    def _step_script(self, tmp_path, job, step_name):
        doc = _load_workflow()
        body = _step_by_name(doc, job, step_name)["run"]
        filename = f"{step_name.lower().replace(' ', '-')}-{job}.sh"
        return _write_bash_script(tmp_path, body, filename)

    def _generate(self, tmp_path):
        proc = _run_bash(self._step_script(tmp_path, "assemble", "Generate checksums"),
                         tmp_path, {})
        assert proc.returncode == 0, proc.stderr

    def test_generation_covers_exactly_the_six_payloads(self, tmp_path):
        assets = self._assets_dir(tmp_path)
        self._generate(tmp_path)
        sums = assets / "SHA256SUMS.txt"
        assert sums.is_file(), "生成步必须落盘 SHA256SUMS.txt"
        recorded = set()
        for line in sums.read_text(encoding="utf-8").splitlines():
            # sha256sum 记录行 "hash␣␣name"（Linux 文本模式）或 "hash␣*name"
            # （msys 二进制模式）——两种分隔都能解析，跨平台一致。
            _digest, name = line.split(maxsplit=1)
            recorded.add(name.lstrip("*"))
        assert recorded == set(REQUIRED_ASSETS), (
            "SHA 清单必须恰好等于六载荷——不含 SHA256SUMS.txt 自身，"
            "也不吸收目录内多余文件（stray.log）"
        )

    @pytest.mark.parametrize("job", ["assemble", "publish"])
    def test_verification_passes_on_the_intact_bundle(self, tmp_path, job):
        self._assets_dir(tmp_path, extra=())
        self._generate(tmp_path)
        proc = _run_bash(self._step_script(tmp_path, job, "Verify checksums"),
                         tmp_path, {})
        assert proc.returncode == 0, proc.stdout + proc.stderr

    @pytest.mark.parametrize("job", ["assemble", "publish"])
    def test_verification_fails_when_a_recorded_payload_is_tampered(self, tmp_path, job):
        assets = self._assets_dir(tmp_path, extra=())
        self._generate(tmp_path)
        (assets / "ReviewFlow.dmg").write_bytes(b"tampered-bytes")
        proc = _run_bash(self._step_script(tmp_path, job, "Verify checksums"),
                         tmp_path, {})
        assert proc.returncode != 0, "已记录载荷被篡改必须失败"
        assert "FAILED" in (proc.stdout + proc.stderr), (
            "失败必须点名被篡改的文件（sha256sum -c 的 FAILED 行）"
        )


class TestTagGuardBareRepoBehavior:
    """P1-1 标签守卫行为：提取工作流脚本，在临时裸仓库上实测。

    三件关键行为的自动化收口（原仅 12 组手工复现）：
    标签不移动 / 同提交幂等放行 / 非强制推送；外加触发标签不匹配拒发。
    """

    def test_trigger_tag_mismatching_version_refuses_without_creating_any_tag(self, tmp_path):
        bare, work, _, sha_b = _git_sandbox(tmp_path)
        proc = _run_tag_guard(
            tmp_path, work,
            ref_type="tag", ref="refs/tags/v9.9.9", sha=sha_b, version="1.2.3",
        )
        assert proc.returncode != 0, "触发标签与 VERSION 不一致必须拒绝"
        assert "does not match VERSION" in proc.stderr
        assert _remote_tag(bare, "v1.2.3")["raw"] is None, (
            "拒绝路径不得在远端留下任何标签"
        )

    def test_existing_tag_at_a_different_commit_refuses_to_move(self, tmp_path):
        bare, work, sha_a, sha_b = _git_sandbox(tmp_path)
        _git(work, "tag", "v1.2.3", sha_a)
        _git(work, "push", "origin", "v1.2.3")
        proc = _run_tag_guard(
            tmp_path, work,
            ref_type="tag", ref="refs/tags/v1.2.3", sha=sha_b, version="1.2.3",
        )
        assert proc.returncode != 0, "远端标签指向别的提交时必须拒绝移动"
        assert "refusing to move" in proc.stderr
        assert _remote_tag(bare, "v1.2.3")["raw"] == sha_a, "既有标签必须原位不动"

    def test_existing_annotated_tag_at_same_commit_is_idempotent(self, tmp_path):
        # 附注标签：ls-remote 有 tag 对象 + peeled 两行，守卫必须取 peeled 行
        bare, work, _, sha_b = _git_sandbox(tmp_path)
        _git(work, "tag", "-a", "v1.2.3", "-m", "release", sha_b)
        _git(work, "push", "origin", "v1.2.3")
        before = _remote_tag(bare, "v1.2.3")
        proc = _run_tag_guard(
            tmp_path, work,
            ref_type="tag", ref="refs/tags/v1.2.3", sha=sha_b, version="1.2.3",
        )
        assert proc.returncode == 0, proc.stderr
        assert _remote_tag(bare, "v1.2.3") == before, "幂等重跑不得改写远端标签"

    def test_existing_lightweight_tag_at_same_commit_is_idempotent(self, tmp_path):
        bare, work, _, sha_b = _git_sandbox(tmp_path)
        _git(work, "tag", "v1.2.3", sha_b)
        _git(work, "push", "origin", "v1.2.3")
        proc = _run_tag_guard(
            tmp_path, work,
            ref_type="tag", ref="refs/tags/v1.2.3", sha=sha_b, version="1.2.3",
        )
        assert proc.returncode == 0, proc.stderr
        assert _remote_tag(bare, "v1.2.3")["raw"] == sha_b

    def test_missing_remote_tag_is_created_by_a_non_forced_push(self, tmp_path):
        bare, work, _, sha_b = _git_sandbox(tmp_path)
        proc = _run_tag_guard(
            tmp_path, work,
            ref_type="tag", ref="refs/tags/v1.2.3", sha=sha_b, version="1.2.3",
        )
        assert proc.returncode == 0, proc.stderr
        assert _remote_tag(bare, "v1.2.3")["raw"] == sha_b, (
            "无既有标签时按当前提交创建并推送"
        )

    def test_concurrent_conflicting_tag_fails_visibly_because_push_is_not_forced(self, tmp_path):
        # pre-receive 拒收模拟并发冲突：脚本不含 --force，推送失败必须可见
        bare, work, _, sha_b = _git_sandbox(tmp_path, reject_push=True)
        proc = _run_tag_guard(
            tmp_path, work,
            ref_type="tag", ref="refs/tags/v1.2.3", sha=sha_b, version="1.2.3",
        )
        assert proc.returncode != 0, "非强制推送被拒时步骤必须失败（不许静默覆盖）"
        assert "sandbox: rejecting push" in proc.stderr, (
            "失败必须来自 pre-receive 拒收（证明推送真的到达了远端钩子，"
            "而非其他环境性失败）"
        )
        assert _remote_tag(bare, "v1.2.3")["raw"] is None

    def test_branch_context_would_tag_the_branch_head_guards_alone_do_not_gate(self, tmp_path):
        # 记录性断言：守卫脚本本身在 branch 语境下会从分支头建标签发版——
        # 这正是 2.1.5 branch-push 发版的路径。真正的触发面控制点是
        # job 级 if:（见 ReleaseWorkflowConfigNails），本用例钉死该认知。
        bare, work, _, sha_b = _git_sandbox(tmp_path)
        proc = _run_tag_guard(
            tmp_path, work,
            ref_type="branch", ref="refs/heads/main", sha=sha_b, version="1.2.3",
        )
        assert proc.returncode == 0, proc.stderr
        assert _remote_tag(bare, "v1.2.3")["raw"] == sha_b, (
            "P1-1 守卫不拦 branch 语境——publish 的 job 级 if: 才是 branch/"
            "dispatch 不发版的控制点"
        )
