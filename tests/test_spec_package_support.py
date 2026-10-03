from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from spec_package_support import (
    SECTION_PLACEHOLDER_ITEMS,
    active_package_slugs,
    checklist_passed,
    clarification_gaps,
    count_tasks,
    count_unchecked_checkboxes,
    current_git_branch,
    detect_language,
    extract_evidence_fields,
    extract_integration_branch,
    extract_pending_questions,
    extract_section_bullets,
    extract_structured_section_fields,
    has_command_evidence,
    is_placeholder_value,
    normalize_items,
    remote_unavailable_detected,
    resolve_life_dir,
    resolve_specs_child,
    resolve_specs_root,
    resolve_trae_dir,
    scope_from_changed_paths,
    section_checkboxes,
    section_exists,
    select_active_package,
    slug_verb_advisory,
    verification_scope,
    verification_scope_errors,
)


def _symlinks_available() -> bool:
    """Probe whether the environment can create symlinks.

    Windows without SeCreateSymbolicLinkPrivilege (admin / Developer Mode)
    raises WinError 1314 on symlink creation; such environments should skip
    symlink-based tests rather than report false failures.
    """
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "src"
        source.write_text("", encoding="utf-8")
        link = Path(directory) / "link"
        try:
            link.symlink_to(source)
        except (OSError, NotImplementedError):
            return False
    return True


requires_symlink = pytest.mark.skipif(
    not _symlinks_available(),
    reason="symlinks unavailable (Windows without admin/Developer Mode raises WinError 1314)",
)

# -- integration branch binding --


def _write_selectable_package(root: Path, slug: str, branch: str | None) -> None:
    package = root / ".spec" / "specs" / slug
    package.mkdir(parents=True)
    branch_line = f"- Git integration branch：`{branch}`\n" if branch else ""
    (package / "spec.md").write_text(f"# Package - 项目范围\n{branch_line}", encoding="utf-8")
    (package / "tasks.md").write_text("# Package - 任务拆解\n", encoding="utf-8")
    (package / "checklist.md").write_text("**验收结果**：待修复\n", encoding="utf-8")


def test_current_git_branch_is_empty_outside_git(tmp_path):
    assert current_git_branch(tmp_path) == ""


def test_select_active_package_single_git_package_requires_branch_match(tmp_path):
    slug = "2026-09-05_fix-single"
    _write_selectable_package(tmp_path, slug, f"spec/{slug}")
    specs_root = resolve_specs_root(tmp_path, ".spec")
    detached = select_active_package(
        tmp_path,
        specs_root,
        [slug],
        current_branch="",
        git_available=True,
    )
    assert detached.slug is None
    assert detached.mode == "unbound"
    assert "detached HEAD" in detached.warning
    non_git = select_active_package(
        tmp_path,
        specs_root,
        [slug],
        current_branch="",
        git_available=False,
    )
    assert non_git.slug == slug
    assert non_git.mode == "single-non-git"


def test_select_active_package_duplicate_branch_binding_is_ambiguous(tmp_path):
    first = "2026-09-05_fix-first"
    second = "2026-09-05_fix-second"
    shared = "spec/shared"
    _write_selectable_package(tmp_path, first, shared)
    _write_selectable_package(tmp_path, second, shared)
    specs_root = resolve_specs_root(tmp_path, ".spec")
    selection = select_active_package(
        tmp_path,
        specs_root,
        [first, second],
        current_branch=shared,
        git_available=True,
    )
    assert selection.slug is None
    assert selection.mode == "ambiguous"
    assert first in selection.warning and second in selection.warning

    assert extract_integration_branch("- Git integration branch：`spec/fix-one`；本地模式") == "spec/fix-one"
    assert extract_integration_branch("- Git integration branch: 续用 `spec/fix-two`（same work）") == "spec/fix-two"
    assert extract_integration_branch("正文提到 Git integration branch：`spec/not-metadata`") is None
    assert extract_integration_branch("- Git integration branch：适用外（非 Git）") is None
    assert extract_integration_branch("- Git integration branch：`../bad`") is None


def test_select_active_package_prefers_exact_current_branch(tmp_path):
    first = "2026-09-05_fix-first"
    second = "2026-09-05_fix-second"
    _write_selectable_package(tmp_path, first, f"spec/{first}")
    _write_selectable_package(tmp_path, second, f"spec/{second}")
    specs_root = resolve_specs_root(tmp_path, ".spec")
    selection = select_active_package(
        tmp_path,
        specs_root,
        active_package_slugs(specs_root / "specs"),
        current_branch=f"spec/{second}",
    )
    assert selection.slug == second
    assert selection.mode == "branch"
    assert selection.integration_branch == f"spec/{second}"


def test_select_active_package_fails_closed_when_multi_package_is_unbound(tmp_path):
    first = "2026-09-05_fix-first"
    second = "2026-09-05_fix-second"
    _write_selectable_package(tmp_path, first, f"spec/{first}")
    _write_selectable_package(tmp_path, second, f"spec/{second}")
    specs_root = resolve_specs_root(tmp_path, ".spec")
    selection = select_active_package(
        tmp_path,
        specs_root,
        active_package_slugs(specs_root / "specs"),
        current_branch="feature/unrelated",
    )
    assert selection.slug is None
    assert selection.mode == "unbound"
    assert "已隐藏跨包数据" in selection.warning


def test_select_active_package_supports_legacy_convention_and_explicit_override(tmp_path):
    legacy = "2026-09-05_fix-legacy"
    other = "2026-09-05_fix-other"
    _write_selectable_package(tmp_path, legacy, None)
    _write_selectable_package(tmp_path, other, f"spec/{other}")
    specs_root = resolve_specs_root(tmp_path, ".spec")
    available = active_package_slugs(specs_root / "specs")
    conventional = select_active_package(tmp_path, specs_root, available, current_branch=f"spec/{legacy}")
    assert conventional.slug == legacy
    assert conventional.mode == "branch-convention"
    explicit = select_active_package(tmp_path, specs_root, available, explicit=other, current_branch=f"spec/{legacy}")
    assert explicit.slug == other
    assert explicit.mode == "explicit"


FRESH_SPEC = """\
# Test - 项目范围

## 1. 问题定义
- **项目目标**：xxx
- **目标用户**：xxx
- **核心价值**：xxx

## 2. 假设与待确认
### 2.1 已确认事实
- 事实 A
### 2.2 关键假设
- 假设 A
### 2.3 待确认问题
- 问题 A
## 3. 功能范围
### 3.3 不在范围内
- 明确列出本轮不做的内容
## 4. 最小实现路径
- 最小路径条目
"""


def test_clarification_gaps_fresh():
    gaps = clarification_gaps(FRESH_SPEC)
    assert any("缺少有效" in g for g in gaps)


def test_clarification_gaps_complete():
    spec = """\
# Test - 项目范围

## 1. 问题定义
- **项目目标**：实际目标
- **目标用户**：实际用户
- **核心价值**：实际价值

## 2. 假设与待确认
### 2.1 已确认事实
- 已确认的事实
### 2.2 关键假设
- 实际假设
### 2.3 待确认问题
- 无
## 3. 功能范围
### 3.3 不在范围内
- 不做什么
## 4. 最小实现路径
- 路径1
- 路径2
- 路径3
"""
    gaps = clarification_gaps(spec)
    assert gaps == []


def test_clarification_gaps_missing_section():
    spec = """\
# Test - 项目范围

## 1. 问题定义
- **项目目标**：实际目标
- **目标用户**：实际用户
- **核心价值**：实际价值

## 2. 假设与待确认
### 2.1 已确认事实
- 已确认
### 2.2 关键假设
- 假设
### 2.3 待确认问题
- 无
### 3.3 不在范围内
- 不做什么

## 4. 最小实现路径
- 路径1
- 路径2
- 路径3
"""
    gaps = clarification_gaps(spec)
    assert len(gaps) == 0


# -- count_tasks --


def test_count_tasks_mixed():
    content = """\
## 阶段一
- [x] Done task
- [ ] Pending task
  - boundary: only x
  - verify: runs
- [x] Another done
"""
    total, completed = count_tasks(content)
    assert total == 3
    assert completed == 2


def test_count_tasks_empty():
    total, completed = count_tasks("")
    assert total == 0
    assert completed == 0


# -- count_unchecked_checkboxes --


def test_count_unchecked():
    assert count_unchecked_checkboxes("- [ ] a\n- [x] b\n- [ ] c") == 2


@pytest.mark.parametrize("marker", ["!", ">", "?", "-"])
def test_count_unchecked_treats_every_non_x_marker_as_incomplete(marker):
    assert count_unchecked_checkboxes(f"- [{marker}] unresolved\n- [X] done") == 1


# -- checklist_passed --


def test_checklist_passed():
    assert checklist_passed("**验收结果**：通过")


def test_checklist_not_passed():
    assert not checklist_passed("**验收结果**：待修复")


# -- extract_evidence_fields --


def test_extract_evidence_fields():
    content = """\
## 验收证据
- 外部对标：Anthropic 官方
- 脚本验证：py_compile 退出 0
- 不相关项：xxx

## 其他段
"""
    fields = extract_evidence_fields(content)
    assert fields["外部对标"] == "Anthropic 官方"
    assert "脚本验证" in fields


# -- extract_section_bullets --


def test_extract_section_bullets():
    content = """\
## Section A
- bullet 1
- bullet 2

## Section B
- other
"""
    bullets = extract_section_bullets(content, "## Section A")
    assert bullets == ["bullet 1", "bullet 2"]


def test_extract_section_bullets_empty():
    assert extract_section_bullets("## Other\n- x", "## Missing") == []


# -- extract_structured_section_fields --


def test_extract_structured_fields():
    content = """\
### 5.4 编排策略
- route: local
- ownership: main thread
- waiting strategy: none

## Next section
"""
    fields = extract_structured_section_fields(content, "### 5.4 编排策略")
    assert fields["route"] == "local"
    assert fields["ownership"] == "main thread"


def test_extract_structured_fields_ignore_nested_bullets():
    content = """\
### 5.4 编排策略
- route: nonsense
- example:
  - route: local
"""
    fields = extract_structured_section_fields(content, "### 5.4 编排策略")
    # Nested bullets belong to the `example` bullet; only top-level fields count.
    assert fields["route"] == "nonsense"


def test_extract_structured_fields_nested_value_never_becomes_key():
    content = """\
### 5.4 编排策略
- route: local
- ownership:
  - route: nonsense
"""
    fields = extract_structured_section_fields(content, "### 5.4 编排策略")
    assert fields["route"] == "local"


def test_extract_structured_fields_keeps_first_top_level_route():
    content = """\
### 5.4 编排策略
- route: local
- route: review
"""
    fields = extract_structured_section_fields(content, "### 5.4 编排策略")
    assert fields["route"] == "local"


# -- extract_pending_questions --


def test_extract_pending_questions_allows_explicit_no_pending_declaration():
    spec = """\
### 2.3 待确认问题
- 无（已澄清：本轮没有开放问题）
"""
    assert extract_pending_questions(spec) == []


def test_extract_pending_questions_keeps_real_questions_starting_with_no():
    spec = """\
### 2.3 待确认问题
- 无权限时是否降级为只读模式？
- 无数据时是否展示空状态？
"""
    assert extract_pending_questions(spec) == [
        "无权限时是否降级为只读模式？",
        "无数据时是否展示空状态？",
    ]


# G1: full-line 无（...） declarations outside the legacy vocabulary are
# declarations, not pending questions (live case: 均可合理假设 was blocked).
@pytest.mark.parametrize(
    "declaration",
    [
        "无（均已解决）",
        "无（可合理假设）",
        "无（均可合理假设，不阻塞执行）。",
        "无（无需用户决策：两项关键假设均可合理假定，见 2.2/2.4）。",
        "无（无阻塞）",
        "无(已用半角括号声明)",
        "无（已澄清：本轮没有开放问题）",
        "无",
        "无：本轮为审查修复型任务，无改变项目形态的决策点",
        "无: 本轮为审查修复型任务，发现的问题按能修则修处理",
    ],
)
def test_extract_pending_questions_accepts_full_line_no_pending_declarations(declaration):
    spec = f"### 2.3 待确认问题\n- {declaration}\n"
    assert extract_pending_questions(spec) == []


# G1 guard: anything containing a question mark stays a pending question,
# including questions that open with 无（ or merely start with 无.
@pytest.mark.parametrize(
    "question",
    [
        "无权限时是否降级为只读模式？",
        "无数据时是否展示空状态？",
        "无（除了 X 该怎么办？）",
        "无（详见 2.4，尚有一个开放点）？",
        "无法确定的边界条件有哪些?",
        "无（已澄清大部分，剩余见下）？",
        "无：是否需要降级为只读？",
    ],
)
def test_extract_pending_questions_keeps_questions_with_question_marks(question):
    spec = f"### 2.3 待确认问题\n- {question}\n"
    assert extract_pending_questions(spec) == [question]


def test_clarification_gaps_accepts_full_line_no_pending_declaration():
    spec = """\
# Test - 项目范围

## 1. 问题定义
- **项目目标**：实际目标
- **目标用户**：实际用户
- **核心价值**：实际价值

## 2. 假设与待确认
### 2.1 已确认事实
- 已确认的事实
### 2.2 关键假设
- 实际假设
### 2.3 待确认问题
- 无（均已解决）。
## 3. 功能范围
### 3.3 不在范围内
- 不做什么
## 4. 最小实现路径
- 路径1
- 路径2
- 路径3
"""
    assert clarification_gaps(spec) == []


# -- is_placeholder_value --


@pytest.mark.parametrize("value", ["待补充", "待完善", "n/a", "N/A", "NA", ""])
def test_placeholder_values(value):
    assert is_placeholder_value(value)


@pytest.mark.parametrize("value", ["实际内容", "Python 3.10", "已完成"])
def test_non_placeholder_values(value):
    assert not is_placeholder_value(value)


# -- normalize_items --


def test_normalize_filters_placeholders():
    result = normalize_items(["实际内容", "待补充", "n/a", "xxx"])
    assert result == ["实际内容"]


# G4: bare-token equality only — content that merely mentions "xxx" survives.
def test_normalize_items_drops_bare_xxx_token_only():
    result = normalize_items(
        [
            "实际内容",
            "xxx",
            "`xxx`",
            " xxx ",
            "xxx.py 的编码问题是否需要处理？",
            "待补充",
            "n/a",
        ]
    )
    assert result == ["实际内容", "xxx.py 的编码问题是否需要处理？"]


def test_extract_pending_questions_keeps_questions_mentioning_xxx():
    spec = "### 2.3 待确认问题\n- xxx.py 的编码问题是否需要处理？\n"
    assert extract_pending_questions(spec) == ["xxx.py 的编码问题是否需要处理？"]


# G4: the minimal-path template slots (references/templates.md ## 4 block) are
# enumerated per heading, so a fresh template package still shows the gap and
# real items that mention xxx still count.
def test_clarification_gaps_flags_template_only_minimal_path():
    spec = """\
# Test - 项目范围

## 1. 问题定义
- **项目目标**：实际目标
- **目标用户**：实际用户
- **核心价值**：实际价值

## 2. 假设与待确认
### 2.1 已确认事实
- 已确认的事实
### 2.2 关键假设
- 实际假设
### 2.3 待确认问题
- 无
## 3. 功能范围
### 3.3 不在范围内
- 不做什么
## 4. 最小实现路径
- 最简单可行方案：xxx
- 暂不引入：xxx
- 不做的抽象/配置化：xxx
"""
    gaps = clarification_gaps(spec)
    assert "`spec.md` 缺少有效区块：## 4. 最小实现路径" in gaps


def test_minimal_path_template_slots_filtered_but_real_xxx_items_count():
    spec = """\
## 4. 最小实现路径
- 最简单可行方案：xxx
- 暂不引入：xxx
- 不做的抽象/配置化：xxx
- 修复 xxx.py 的编码问题 -> verify: pytest tests/test_encoding.py 通过
"""
    items = normalize_items(
        extract_section_bullets(spec, "## 4. 最小实现路径"),
        ignored=SECTION_PLACEHOLDER_ITEMS["## 4. 最小实现路径"],
    )
    assert items == ["修复 xxx.py 的编码问题 -> verify: pytest tests/test_encoding.py 通过"]


def test_clarification_gaps_accepts_real_minimal_path_item_mentioning_xxx():
    spec = """\
# Test - 项目范围

## 1. 问题定义
- **项目目标**：实际目标
- **目标用户**：实际用户
- **核心价值**：实际价值

## 2. 假设与待确认
### 2.1 已确认事实
- 已确认的事实
### 2.2 关键假设
- 实际假设
### 2.3 待确认问题
- 无
## 3. 功能范围
### 3.3 不在范围内
- 不做什么
## 4. 最小实现路径
- 最简单可行方案：修复 xxx.py 的编码问题并补回归测试
- 暂不引入：新配置项
- 不做的抽象/配置化：无
"""
    gaps = clarification_gaps(spec)
    assert not any("最小实现路径" in gap for gap in gaps)


# -- resolve_life_dir / resolve_trae_dir --


def test_resolve_life_dir_default():
    root = Path("/tmp/project")
    assert resolve_life_dir(root) == root.resolve(strict=False) / ".spec"


def test_resolve_life_dir_custom():
    root = Path("/tmp/project")
    assert resolve_life_dir(root, ".specs") == root.resolve(strict=False) / ".specs"


def test_resolve_trae_dir_keeps_backward_compatibility():
    root = Path("/tmp/project")
    assert resolve_trae_dir(root) == resolve_life_dir(root)


def test_resolve_specs_root_rejects_absolute_specs_dir(tmp_path):
    root = tmp_path / "project"
    absolute_outside = str((tmp_path / "outside").resolve())
    with pytest.raises(ValueError, match="relative directory"):
        resolve_specs_root(root, absolute_outside)


def test_resolve_specs_root_rejects_parent_traversal():
    root = Path("/tmp/project")
    with pytest.raises(ValueError, match="must not contain"):
        resolve_specs_root(root, "../outside")


def test_resolve_specs_root_rejects_current_directory():
    root = Path("/tmp/project")
    with pytest.raises(ValueError, match="relative directory"):
        resolve_specs_root(root, ".")


def test_resolve_specs_root_rejects_empty_string():
    root = Path("/tmp/project")
    with pytest.raises(ValueError, match="relative directory"):
        resolve_specs_root(root, "")


def test_resolve_specs_root_allows_nested_relative_directory():
    root = Path("/tmp/project")
    assert resolve_specs_root(root, ".spec/local") == root.resolve(strict=False) / ".spec/local"


@pytest.mark.parametrize(
    "name",
    ["node_modules/spec-state", ".claude/spec-state", "build/spec-state", "vendor/spec-state"],
)
def test_resolve_specs_root_rejects_reserved_runtime_directories(name):
    root = Path("/tmp/project")
    with pytest.raises(ValueError, match="reserved runtime directory"):
        resolve_specs_root(root, name)


@requires_symlink
def test_resolve_specs_root_rejects_symlink_escape(tmp_path):
    root = tmp_path / "project"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / ".spec").symlink_to(outside)

    with pytest.raises(ValueError, match="resolve under root"):
        resolve_specs_root(root, ".spec")


@requires_symlink
def test_resolve_specs_child_rejects_nested_symlink_escape(tmp_path):
    root = tmp_path / "project"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    specs_root = resolve_specs_root(root, ".spec")
    specs_root.mkdir()
    (specs_root / "specs").symlink_to(outside)

    with pytest.raises(ValueError, match="resolve under specs root"):
        resolve_specs_child(specs_root, "specs")


def test_resolve_specs_child_allows_normal_nested_path(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    specs_root = resolve_specs_root(root, ".spec")

    assert resolve_specs_child(specs_root, "specs", "2026-06-12_demo") == (specs_root / "specs" / "2026-06-12_demo")


def test_scope_from_changed_paths_rejects_invalid_slug_in_trusted_root():
    with pytest.raises(ValueError, match="invalid Development Record slug"):
        scope_from_changed_paths([".spec/specs/not-a-development-record/spec.md"])


def test_scope_from_changed_paths_prefers_nested_custom_root():
    slugs = scope_from_changed_paths(
        [".spec/local/specs/2026-07-13_fix-nested/spec.md"],
        [".spec/local"],
    )
    assert slugs == ["2026-07-13_fix-nested"]


# -- detect_language --


def test_detect_language_chinese_title():
    assert detect_language("计费系统重构") == "zh"


def test_detect_language_english_title():
    assert detect_language("Billing System Rewrite") == "en"


def test_detect_language_mixed_mostly_cjk():
    assert detect_language("推送阶段 push stage") == "zh"


def test_detect_language_empty_falls_back_to_zh():
    assert detect_language("") == "zh"


# -- slug_verb_advisory --


@pytest.mark.parametrize("slug", ["2026-06-12_add-push-stage", "2026-06-12_fix-push-safety", "2026-06-12_docs-readme"])
def test_slug_verb_advisory_none_for_verb_object(slug):
    assert slug_verb_advisory(slug) is None


@pytest.mark.parametrize("slug", ["2026-06-12_spec-push-stage", "2026-06-12_billing-system"])
def test_slug_verb_advisory_present_for_noun_phrase(slug):
    advisory = slug_verb_advisory(slug)
    assert advisory is not None
    assert "verb-object" in advisory
    assert "naming-and-commits.md" in advisory


def test_slug_verb_advisory_none_for_non_development_record():
    assert slug_verb_advisory("legacy-slug") is None


def test_extract_evidence_fields_nested_bullets():
    content = """\
## 验收证据
- 脚本验证：
  - pytest -q
  - python3 scripts/smoke_test_spec_skill.py
- 旧新对比：
  - 旧：弱门禁
  - 新：强制脚本证据

## 其他段
"""
    fields = extract_evidence_fields(content)
    assert "pytest -q" in fields["脚本验证"]
    assert "smoke_test_spec_skill.py" in fields["脚本验证"]
    assert "旧" not in fields  # must not leak nested keys
    assert "旧：弱门禁" in fields["旧新对比"] or "弱门禁" in fields["旧新对比"]
    assert has_command_evidence(content)


def test_section_exists_requires_exact_heading():
    content = "## 行为成效回填\n- [x] a\n## 行为成效\n- [ ] b\n"
    assert section_exists(content, "## 行为成效")
    assert section_exists(content, "## 行为成效回填")
    # prefix-only collision content
    only_prefix = "## 行为成效回填\n- [x] a\n- [x] b\n"
    assert not section_exists(only_prefix, "## 行为成效")
    assert section_checkboxes(only_prefix, "## 行为成效") == (0, 0)


def test_remote_unavailable_detected_transport_failures():
    assert remote_unavailable_detected(
        "fatal: unable to access 'https://example.test/repo.git/': The requested URL returned error: 502"
    )
    assert remote_unavailable_detected("fatal: Couldn't connect to server")
    assert remote_unavailable_detected("fatal: does not appear to be a git repository")


def test_remote_unavailable_detected_rejects_auth_and_missing_repo():
    assert not remote_unavailable_detected("ERROR: repository not found\nfatal: Could not read from remote repository.")
    assert not remote_unavailable_detected("fatal: Authentication failed for 'https://example.test/repo.git/'")
    assert not remote_unavailable_detected(
        "remote: Permission denied\nfatal: unable to access 'https://example.test/repo.git/'"
    )


@pytest.mark.parametrize("suffix", ["（来自实测）", " (measured)", "（第一性纪律）"])
def test_heading_annotations_preserve_section_validation(suffix):
    from spec_package_support import heading_line_matches

    heading = "### 2.3 待确认问题"
    assert heading_line_matches(heading + suffix, heading)
    assert extract_pending_questions(heading + suffix + "\n- 需要用户选择数据库？\n") == ["需要用户选择数据库？"]
    assert extract_section_bullets(heading + suffix + "\n## Next\n- not here\n", heading) == []
    assert (
        normalize_items(extract_section_bullets(heading + suffix + "\n- 问题 A\n", heading), ignored={"问题 A"}) == []
    )


@pytest.mark.parametrize("suffix", ["回填", "（未闭合", "(broken）", "（ok）junk", "()", "（a(b)）", "（ ）"])
def test_heading_annotation_does_not_accept_prefix_or_malformed_suffix(suffix):
    from spec_package_support import heading_line_matches

    assert not heading_line_matches("## 行为成效" + suffix, "## 行为成效")


# -- evidence freshness anchor parsing (证据锚点：HEAD <sha> @ <iso>) --


def _checklist_with_anchor_line(value: str) -> str:
    return f"## 验收证据\n- 脚本验证：pytest -q\n- 证据锚点：{value}\n**验收结果**：通过\n"


def test_extract_evidence_anchor_parses_full_sha_and_timestamp():
    from spec_package_support import extract_evidence_anchor

    content = _checklist_with_anchor_line("HEAD f3b69a49367f90e97750e248f8994081ebcf9e38 @ 2026-09-25T10:00:00+08:00")
    assert extract_evidence_anchor(content) == ("f3b69a49367f90e97750e248f8994081ebcf9e38", "2026-09-25T10:00:00+08:00")


def test_extract_evidence_anchor_accepts_short_sha_and_uppercase_hex():
    from spec_package_support import extract_evidence_anchor

    content = _checklist_with_anchor_line("HEAD F3B69A4 @ 2026-09-25T02:00:00Z")
    assert extract_evidence_anchor(content) == ("f3b69a4", "2026-09-25T02:00:00Z")


def test_extract_evidence_anchor_reads_nested_value_block():
    from spec_package_support import extract_evidence_anchor

    content = "## 验收证据\n- 证据锚点：\n  - HEAD abc1234 @ 2026-09-25T00:00:00Z\n"
    assert extract_evidence_anchor(content) == ("abc1234", "2026-09-25T00:00:00Z")


@pytest.mark.parametrize(
    "value",
    [
        "待补充",
        "HEAD",
        "HEAD not-a-sha @ 2026-09-25T00:00:00Z",
        "HEAD 12345 @ 2026-09-25T00:00:00Z",  # 5 hex chars: below 7-char floor
        "head abc1234 @ 2026-09-25T00:00:00Z",  # keyword is case-sensitive
        "current HEAD abc1234",
    ],
)
def test_extract_evidence_anchor_absent_or_malformed_never_activates(value):
    from spec_package_support import extract_evidence_anchor

    assert extract_evidence_anchor(_checklist_with_anchor_line(value)) is None


def test_extract_evidence_anchor_absent_without_field():
    from spec_package_support import extract_evidence_anchor

    assert extract_evidence_anchor("## 验收证据\n- 脚本验证：pytest -q\n") is None


def test_git_head_sha_matches_rev_parse(tmp_path):
    import subprocess

    from spec_package_support import git_head_sha

    repo = tmp_path / "proj"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@example.com"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "T"], check=True)
    # No commits yet: HEAD is unresolvable and must read as empty.
    assert git_head_sha(repo) == ""
    (repo / "file.txt").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "base"], check=True)
    expected = (
        subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True)
        .stdout.strip()
        .lower()
    )
    assert git_head_sha(repo) == expected


def test_git_head_sha_empty_outside_git(tmp_path):
    from spec_package_support import git_head_sha

    plain = tmp_path / "plain"
    plain.mkdir()
    assert git_head_sha(plain) == ""


def _verification_scope(level: str = "package", full_project: str = "适用外：发布门禁") -> str:
    return f"""### 5.1 验证策略
- 范围级别：{level}
- 变更对象：changed module
- 快速检查：pytest tests/test_changed.py
- 集成检查：适用外：无直接受影响链路
- 全项目检查：{full_project}
- 升级触发：共享基础设施或跨模块契约变化
"""


def test_verification_scope_accepts_package_default():
    assert verification_scope(_verification_scope()) == "package"
    assert verification_scope_errors(_verification_scope()) == []


def test_verification_scope_accepts_parenthetical_heading_suffix():
    text = _verification_scope().replace("### 5.1 验证策略", "### 5.1 验证策略（说明）")
    assert verification_scope(text) == "package"
    assert verification_scope_errors(text) == []


def test_verification_scope_ignores_code_fenced_examples():
    example = """```markdown
### 5.1 验证策略
- 范围级别：project
```\n"""
    text = example + _verification_scope()
    assert verification_scope(text) == "package"
    assert verification_scope_errors(text) == []


@pytest.mark.parametrize("level", ["unknown", "package integration", ""])
def test_verification_scope_rejects_unknown_or_missing_level(level):
    errors = verification_scope_errors(_verification_scope(level))
    assert errors
    if level:
        assert "范围级别必须是单 token" in errors[0]
    else:
        assert any("缺少字段：范围级别" in error for error in errors)


def test_verification_scope_rejects_placeholder_and_unjustified_full_project_check():
    text = _verification_scope(full_project="pytest -q")
    errors = verification_scope_errors(text.replace("pytest tests/test_changed.py", "<command>"))
    assert any("快速检查" in error for error in errors)
    assert any("升级条件或适用外理由" in error for error in errors)


def test_verification_scope_is_optional_for_legacy_spec():
    assert verification_scope("## 5. 技术决策\n- 技术栈：legacy\n") is None
    assert verification_scope_errors("## 5. 技术决策\n- 技术栈：legacy\n") == []


def test_verification_scope_rejects_duplicate_top_level_fields():
    text = _verification_scope() + "- 范围级别：project\n"
    errors = verification_scope_errors(text)
    assert any("范围级别" in error and "必须唯一" in error for error in errors)


def test_verification_scope_rejects_duplicate_sections():
    errors = verification_scope_errors(_verification_scope() + "\n" + _verification_scope())
    assert any("出现 2 次" in error and "必须唯一" in error for error in errors)


@pytest.mark.parametrize(
    ("level", "field"),
    [("package", "快速检查"), ("integration", "集成检查"), ("project", "集成检查"), ("project", "全项目检查")],
)
def test_verification_scope_requires_real_checks_for_declared_level(level, field):
    text = _verification_scope(level).replace(
        f"- {field}：", f"- {field}：适用外：本轮未覆盖"
    )
    errors = verification_scope_errors(text)
    assert any(field in error and "必须填写真实" in error for error in errors)


def test_verification_scope_rejects_conditional_required_check():
    text = _verification_scope("integration").replace(
        "- 集成检查：适用外：无直接受影响链路",
        "- 集成检查：仅当 CI 触发时运行",
    )
    errors = verification_scope_errors(text)
    assert any("集成检查" in error and "必须填写真实" in error for error in errors)


@pytest.mark.parametrize("marker", ["未执行", "跳过", "不执行"])
def test_verification_scope_rejects_non_run_required_check(marker):
    text = _verification_scope("project").replace("- 全项目检查：适用外：发布门禁", f"- 全项目检查：{marker}")
    errors = verification_scope_errors(text)
    assert any("全项目检查" in error and "必须填写真实" in error for error in errors)
