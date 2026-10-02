#!/usr/bin/env bash
# SPDX-License-Identifier: LicenseRef-Spec-NonCommercial
set -euo pipefail

REPO_URL="${REPO_URL:-}"
REPO_REF="${REPO_REF:-}"
INSTALL_HOSTS="${INSTALL_HOSTS:-all}"
CLAUDE_SKILLS_DIR="${CLAUDE_SKILLS_DIR:-${INSTALL_DIR:-$HOME/.claude/skills}}"
CLAUDE_COMMANDS_DIR="${CLAUDE_COMMANDS_DIR:-$HOME/.claude/commands}"
CLAUDE_AGENTS_DIR="${CLAUDE_AGENTS_DIR:-$HOME/.claude/agents}"
CODEX_SKILLS_DIR="${CODEX_SKILLS_DIR:-$HOME/.codex/skills}"
ZCODE_SKILLS_DIR="${ZCODE_SKILLS_DIR:-$HOME/.zcode/skills}"
# Each host skills directory below follows that agent's own standard location,
# so the installed skill is picked up directly by the host CLI.
CLAUDE_DESKTOP_SKILLS_DIR="${CLAUDE_DESKTOP_SKILLS_DIR:-$HOME/.claude-desktop/skills}"
GEMINI_SKILLS_DIR="${GEMINI_SKILLS_DIR:-$HOME/.gemini/skills}"
GROK_SKILLS_DIR="${GROK_SKILLS_DIR:-$HOME/.grok/skills}"
OPENCODE_SKILLS_DIR="${OPENCODE_SKILLS_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/opencode/skills}"
OPENCLAW_SKILLS_DIR="${OPENCLAW_SKILLS_DIR:-$HOME/.openclaw/skills}"
PI_SKILLS_DIR="${PI_SKILLS_DIR:-${PI_DIR:-$HOME/.pi}/agent/skills}"
SKILL_NAME="${SKILL_NAME:-spec}"
BACKUP_ROOT="${BACKUP_ROOT:-$HOME/.spec-skill-backups}"
KEEP_TEMP="${KEEP_TEMP:-0}"
FORCE="${FORCE:-0}"
PYTHON_BIN="${PYTHON_BIN:-}"

# Hermes home resolution follows the Hermes agent's own lookup order:
# HERMES_HOME wins, then Windows %LOCALAPPDATA%\hermes, then ~/.hermes.
default_hermes_home() {
  if [ -n "${HERMES_HOME:-}" ]; then
    printf '%s\n' "$HERMES_HOME"
  elif [ -n "${LOCALAPPDATA:-}" ]; then
    printf '%s/hermes\n' "$LOCALAPPDATA"
  else
    printf '%s\n' "$HOME/.hermes"
  fi
}
HERMES_SKILLS_DIR="${HERMES_SKILLS_DIR:-$(default_hermes_home)/skills}"

CLAUDE_ALIASES=()
STALE_CLAUDE_ALIASES=(route tasks)
# Retired stages that once shipped as commands/spec/<stage>.md in the current
# subdirectory layout; the swarm stages went away with the multi-session
# worker machinery and must be swept on upgrade like any retired artifact.
STALE_CLAUDE_SUBDIR_STAGES=(assemble combat marshal)
PREPARED_SOURCE_DIR=""
INSTALL_TEMP_DIR=""
SOURCE_CLONE_DIR=""
LAST_BACKUP_PATH=""

log() {
  printf '%s\n' "$*"
}

fail() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

target_exists() {
  [ -e "$1" ] || [ -L "$1" ]
}

reject_symlink() {
  [ ! -L "$1" ] || fail "refusing to use symlink path: $1"
}

ensure_private_dir() {
  local dir="$1"
  reject_symlink "$dir"
  # Create without -m: on Windows NTFS `mkdir -m` errors out, and the chmod
  # below already enforces 0700 on every platform.
  mkdir -p "$dir"
  reject_symlink "$dir"
  chmod 700 "$dir"
}

write_file_from_stdin() {
  local target="$1"
  local parent tmp
  parent="$(dirname "$target")"
  ensure_private_dir "$parent"
  reject_symlink "$target"
  tmp="$(mktemp "$parent/.spec-skill-write.XXXXXX")"
  cat > "$tmp"
  mv -f "$tmp" "$target"
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || fail "missing required command: $1"
}

# Pick a working Python interpreter. `python3` is preferred, but on Windows the
# default `python3` is often a broken Microsoft Store stub that exits non-zero
# without running anything, so fall back to `python`. Returns the command name
# on stdout, or non-zero if neither can import sys.
resolve_python() {
  if command -v python3 >/dev/null 2>&1 && python3 -c 'import sys' >/dev/null 2>&1; then
    printf '%s\n' python3
  elif command -v python >/dev/null 2>&1 && python -c 'import sys' >/dev/null 2>&1; then
    printf '%s\n' python
  else
    return 1
  fi
}

# User-facing stages for Claude command files. Single source: the stage list
# documented in SKILL.md and references/commands.md.
USER_STAGES=(new goal run check done push update status doctor organize)

format_stage_aliases() {
  local prefix="$1"
  local separator="$2"
  local aliases=""
  local stage
  for stage in "${CLAUDE_ALIASES[@]}"; do
    [ -z "$aliases" ] || aliases="$aliases$separator"
    aliases="${aliases}${prefix}${stage}"
  done
  printf '%s' "$aliases"
}

cleanup_temp() {
  if [ "$KEEP_TEMP" = "1" ]; then
    return 0
  fi
  if [ -n "$INSTALL_TEMP_DIR" ]; then
    rm -rf -- "$INSTALL_TEMP_DIR"
  fi
  if [ -n "$SOURCE_CLONE_DIR" ]; then
    rm -rf -- "$SOURCE_CLONE_DIR"
  fi
}

abs_path() {
  local path="$1"
  if [ -d "$path" ]; then
    (cd "$path" && pwd)
  else
    (cd "$(dirname "$path")" && printf '%s/%s\n' "$(pwd)" "$(basename "$path")")
  fi
}

# Skill-directory-only hosts: the runtime skill lands in the host's skills
# directory and no host-specific command files are written.
SKILL_ONLY_HOSTS=(claude-desktop codex gemini grok opencode openclaw hermes pi zcode)

skill_dir_for() {
  case "$1" in
    claude-desktop) printf '%s\n' "$CLAUDE_DESKTOP_SKILLS_DIR" ;;
    codex) printf '%s\n' "$CODEX_SKILLS_DIR" ;;
    gemini) printf '%s\n' "$GEMINI_SKILLS_DIR" ;;
    grok) printf '%s\n' "$GROK_SKILLS_DIR" ;;
    opencode) printf '%s\n' "$OPENCODE_SKILLS_DIR" ;;
    openclaw) printf '%s\n' "$OPENCLAW_SKILLS_DIR" ;;
    hermes) printf '%s\n' "$HERMES_SKILLS_DIR" ;;
    pi) printf '%s\n' "$PI_SKILLS_DIR" ;;
    zcode) printf '%s\n' "$ZCODE_SKILLS_DIR" ;;
    *) return 1 ;;
  esac
}

host_label_for() {
  case "$1" in
    claude-desktop) printf '%s\n' "Claude Desktop" ;;
    codex) printf '%s\n' "Codex" ;;
    gemini) printf '%s\n' "Gemini CLI" ;;
    grok) printf '%s\n' "Grok Build" ;;
    opencode) printf '%s\n' "OpenCode" ;;
    openclaw) printf '%s\n' "OpenClaw" ;;
    hermes) printf '%s\n' "Hermes" ;;
    pi) printf '%s\n' "Pi" ;;
    zcode) printf '%s\n' "ZCode" ;;
    *) printf '%s\n' "$1" ;;
  esac
}

default_skill_dir_for() {
  case "$1" in
    claude-desktop) printf '%s\n' "$HOME/.claude-desktop/skills" ;;
    codex) printf '%s\n' "$HOME/.codex/skills" ;;
    gemini) printf '%s\n' "$HOME/.gemini/skills" ;;
    grok) printf '%s\n' "$HOME/.grok/skills" ;;
    opencode) printf '%s\n' "${XDG_CONFIG_HOME:-$HOME/.config}/opencode/skills" ;;
    openclaw) printf '%s\n' "$HOME/.openclaw/skills" ;;
    hermes) printf '%s\n' "$(default_hermes_home)/skills" ;;
    pi) printf '%s\n' "$HOME/.pi/agent/skills" ;;
    zcode) printf '%s\n' "$HOME/.zcode/skills" ;;
    *) return 1 ;;
  esac
}

# Per-host invocation hint for the post-install summary.
host_trigger_hint() {
  case "$1" in
    codex|zcode|pi) printf '%s\n' 'trigger $spec' ;;
    *) printf '%s\n' 'trigger the spec skill' ;;
  esac
}

host_enabled() {
  local host="$1" tokens token
  case ",$INSTALL_HOSTS," in
    *,all,*) return 0 ;;
  esac
  case "$host" in
    # Claude Code writes the skill plus the /spec:<stage> command files.
    claude) tokens="claude desktop rpi" ;;
    codex) tokens="codex codex-desktop desktop rpi" ;;
    # grokbuild is an accepted alias token for Grok Build.
    grok) tokens="grok grokbuild" ;;
    claude-desktop|gemini|opencode|openclaw|hermes|pi|zcode) tokens="$host" ;;
    *) return 1 ;;
  esac
  local IFS=' '
  for token in $tokens; do
    case ",$INSTALL_HOSTS," in
      *,"$token",*) return 0 ;;
    esac
  done
  return 1
}

validate_hosts() {
  local IFS=','
  local host enabled=0
  for host in $INSTALL_HOSTS; do
    case "$host" in
      all|claude|claude-desktop|codex|codex-desktop|desktop|gemini|grok|grokbuild|opencode|openclaw|hermes|pi|rpi|zcode) enabled=1 ;;
      "") ;;
      *) fail "unknown INSTALL_HOSTS entry: $host" ;;
    esac
  done
  [ "$enabled" = "1" ] || fail "INSTALL_HOSTS must include a supported host: all, claude, claude-desktop, codex, codex-desktop, desktop, gemini, grok, grokbuild, opencode, openclaw, hermes, pi, rpi, or zcode"
}

validate_inputs() {
  [ "$SKILL_NAME" = "spec" ] || fail "SKILL_NAME must be spec; custom skill names would break /spec:xx aliases"
  validate_hosts
  validate_backup_root
}

validate_repo_url() {
  [ -n "$REPO_URL" ] || fail "REPO_URL is required when install.sh is not run from a local spec checkout or exported package"
  case "$REPO_REF" in
    [0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F]) ;;
    *) fail "REPO_REF must be a full 40-character commit SHA for remote installs" ;;
  esac
  case "$REPO_URL" in
    https://*) ;;
    -*) fail "REPO_URL must not start with '-'" ;;
    *) fail "REPO_URL must use https:// for remote installs" ;;
  esac
}

validate_backup_root() {
  if [ -L "$BACKUP_ROOT" ]; then
    fail "BACKUP_ROOT must not be a symlink: $BACKUP_ROOT"
  fi
}

require_allowed_root() {
  local host="$1"
  local root="$2"
  local default_root="$3"
  if [ "$root" = "$default_root" ] || [ "$FORCE" = "1" ]; then
    return 0
  fi
  fail "$host install root is outside the default path; set FORCE=1 to use: $root"
}

prepare_source() {
  local script_dir source_dir
  if [ -n "${SOURCE_DIR:-}" ]; then
    PREPARED_SOURCE_DIR="$(abs_path "$SOURCE_DIR")"
    return 0
  fi
  script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  source_dir="$script_dir"

  if [ -f "$source_dir/SKILL.md" ] && [ -d "$source_dir/scripts" ]; then
    PREPARED_SOURCE_DIR="$source_dir"
    return 0
  fi

  if [ -f "$source_dir/../SKILL.md" ] && [ -d "$source_dir/../scripts" ]; then
    PREPARED_SOURCE_DIR="$(abs_path "$source_dir/..")"
    return 0
  fi

  validate_repo_url
  need_cmd git
  SOURCE_CLONE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/spec-skill-source.XXXXXX")"
  git clone --depth 1 -- "$REPO_URL" "$SOURCE_CLONE_DIR" >/dev/null
  git -C "$SOURCE_CLONE_DIR" fetch --depth 1 origin "$REPO_REF" >/dev/null
  git -C "$SOURCE_CLONE_DIR" checkout --detach FETCH_HEAD >/dev/null
  rm -rf -- "$SOURCE_CLONE_DIR/.git"
  PREPARED_SOURCE_DIR="$SOURCE_CLONE_DIR"
}

export_runtime() {
  local source_dir="$1"
  local output_dir="$2"
  if [ -n "${PYTHON_BIN:-}" ] && [ -f "$source_dir/scripts/export_skill_package.py" ]; then
    if [ "${SPEC_SKIP_SIGNING:-}" = "1" ]; then
      "$PYTHON_BIN" "$source_dir/scripts/export_skill_package.py" --root "$source_dir" --output "$output_dir" --force --skip-signing >/dev/null
    else
      "$PYTHON_BIN" "$source_dir/scripts/export_skill_package.py" --root "$source_dir" --output "$output_dir" --force >/dev/null
    fi
    return 0
  fi

  # 分发包出厂即带发布签名；签名缺失且本地无签发凭据时拒绝，避免装出来源不明的副本。
  # 存在性判断不走 grep：签名行超长，行截断会误判为缺失。
  if ! "$PYTHON_BIN" -c 'import pathlib,sys; sys.exit(0 if "\u2063" in pathlib.Path(sys.argv[1]).read_text(encoding="utf-8") else 1)' "$source_dir/SKILL.md"; then
    if [ -z "${SPEC_SIGNING_IDENTITY:-}" ] || [ -z "${SPEC_SIGNING_KEY:-}" ] || [ -z "${PYTHON_BIN:-}" ]; then
      fail "exported skill package has no distribution signature and no local signing credentials are available"
    fi
  fi
  mkdir -p "$output_dir"
  cp -R "$source_dir/." "$output_dir/"
}

backup_namespace() {
  local target="$1"
  local parent
  parent="$(dirname "$target")"

  case "$parent" in
    "$HOME/.claude/skills") printf '%s\n' ".claude-skills" ;;
    "$HOME/.claude/commands") printf '%s\n' ".claude-commands" ;;
    "$HOME/.claude/agents") printf '%s\n' ".claude-agents" ;;
    "$HOME/.codex/skills") printf '%s\n' ".codex-skills" ;;
    "$HOME/.zcode/skills") printf '%s\n' ".zcode-skills" ;;
    "$HOME/.claude-desktop/skills") printf '%s\n' ".claude-desktop-skills" ;;
    "$HOME/.gemini/skills") printf '%s\n' ".gemini-skills" ;;
    "$HOME/.grok/skills") printf '%s\n' ".grok-skills" ;;
    "$HOME/.config/opencode/skills") printf '%s\n' ".config-opencode-skills" ;;
    "$HOME/.openclaw/skills") printf '%s\n' ".openclaw-skills" ;;
    "$HOME/.hermes/skills") printf '%s\n' ".hermes-skills" ;;
    "$HOME/.pi/agent/skills") printf '%s\n' ".pi-agent-skills" ;;
    "$HOME/.local/bin") printf '%s\n' ".local-bin" ;;
    "$HOME/.claude") printf '%s\n' ".claude-config" ;;
    *)
      abs_path "$parent" | sed 's#[^A-Za-z0-9._-]#_#g'
      ;;
  esac
}

is_installer_owned() {
  local target="$1"
  local marker="$target/.spec-skill-install"
  if [ -f "$target" ] && [ ! -L "$target" ]; then
    # A leftover sidecar is itself the ownership stamp. Do not look for
    # `$target.spec-skill-install.spec-skill-install`, or an orphan marker
    # after the payload was deleted is treated as a user-owned file and
    # blocks reinstall.
    case "$target" in
      *.spec-skill-install)
        grep -qx 'installed_by=spec/install.sh' "$target"
        return
        ;;
    esac
    marker="$target.spec-skill-install"
  fi
  target_exists "$target" || return 0
  [ -f "$marker" ] || return 1
  [ ! -L "$marker" ] || return 1
  grep -qx 'installed_by=spec/install.sh' "$marker"
}

# A foreign skill is a same-name skill from a different source: it has its own
# SKILL.md but no marker written by this installer. Two skills named "spec"
# cannot coexist in one host, so this is a real collision, not a routine update.
is_foreign_skill() {
  local target="$1"
  target_exists "$target" || return 1
  [ -f "$target/SKILL.md" ] || return 1
  ! is_installer_owned "$target"
}

ensure_replaceable() {
  local target="$1"
  reject_symlink "$target"
  target_exists "$target" || return 0
  if is_installer_owned "$target" || [ "$FORCE" = "1" ]; then
    return 0
  fi
  fail "refusing to replace user-owned target without valid marker: $target (set FORCE=1 to back it up and replace it)"
}

backup_existing() {
  local target="$1"
  LAST_BACKUP_PATH=""
  target_exists "$target" || return 0
  ensure_replaceable "$target"

  local timestamp backup_dir backup_path base counter marker marker_backup
  timestamp="$(date +%Y%m%d-%H%M%S)"
  ensure_private_dir "$BACKUP_ROOT"
  backup_dir="$BACKUP_ROOT/$(backup_namespace "$target")"
  ensure_private_dir "$backup_dir"
  # Sanitize ':' out of the backup basename only (the target keeps its real
  # name). NTFS forbids ':' in filenames, so backing up a colon-named command
  # file like spec:check.md would otherwise land a broken-named artifact under
  # the backup root on Windows. Normal targets have no ':' so this is a no-op
  # for them.
  base="$(basename "$target")-$timestamp-$$"
  base="${base//:/_}"
  backup_path="$backup_dir/$base"
  counter=0
  while [ -e "$backup_path" ]; do
    counter=$((counter + 1))
    backup_path="$backup_dir/$base-$counter"
  done
  mv "$target" "$backup_path"
  LAST_BACKUP_PATH="$backup_path"
  marker="$target.spec-skill-install"
  if [ -f "$marker" ] && [ ! -L "$marker" ]; then
    marker_backup="$backup_path.spec-skill-install"
    mv "$marker" "$marker_backup"
  fi
  log "Backed up existing $(basename "$target") to $backup_path"
}

copy_runtime() {
  local source_dir="$1"
  local target_dir="$2"
  local parent staging
  parent="$(dirname "$target_dir")"
  ensure_private_dir "$parent"
  ensure_replaceable "$target_dir"
  # Copy and mark a complete sibling before replacing the installed tree.
  # Replacing (not overlaying) also removes retired launcher files on upgrade.
  staging="$(mktemp -d "$parent/.spec-stage.XXXXXX")"
  if ! cp -R "$source_dir/." "$staging/"; then
    rm -rf -- "$staging"
    fail "could not stage runtime; previous installation untouched"
  fi
  runtime_version="$("$PYTHON_BIN" "$source_dir/scripts/read_version.py" "$source_dir/pyproject.toml" 2>/dev/null || printf unknown)"
  write_file_from_stdin "$staging/.spec-skill-install" <<EOF_MARKER
installed_by=spec/install.sh
source=${PREPARED_SOURCE_DIR:-$source_dir}
version=${runtime_version}
EOF_MARKER
  backup_existing "$target_dir"
  if ! mv "$staging" "$target_dir"; then
    [ -z "$LAST_BACKUP_PATH" ] || mv "$LAST_BACKUP_PATH" "$target_dir"
    rm -rf -- "$staging"
    fail "runtime publish failed; previous installation restored"
  fi
}

stage_description() {
  case "$1" in
    route) printf '%s\n' "Spec auto-routing" ;;
    new) printf '%s\n' "Spec: create a task package" ;;
    goal) printf '%s\n' "Spec: one-shot goal chain" ;;
    run) printf '%s\n' "Spec: execute or resume" ;;
    check) printf '%s\n' "Spec: collaborative review" ;;
    done) printf '%s\n' "Spec: archive and record" ;;
    push) printf '%s\n' "Spec: merge and push" ;;
    update) printf '%s\n' "Spec: adjust tasks" ;;
    status) printf '%s\n' "Spec: package overview" ;;
    doctor) printf '%s\n' "Spec: environment self-check and repair" ;;
    organize) printf '%s\n' "Spec: structure audit and tidy-up" ;;
    *) printf '%s\n' "Spec workflow stage" ;;
  esac
}

command_body() {
  local command_name="$1"
  local stage="$2"
  case "$stage" in
    route)
      cat <<EOF_BODY
# /$command_name

Internal route first, then continue with the right spec stage. Do not present route as a user-facing stage unless debugging the workflow.
EOF_BODY
      ;;
    goal)
      cat <<EOF_BODY
# /$command_name

Run the one-shot goal workflow: plan the task package, execute every task in this session, independently verify each delivery, archive and commit scoped changes after gates pass, then use the safe spec:push flow. Stop at any failed gate.
EOF_BODY
      ;;
    run)
      cat <<EOF_BODY
# /$command_name

Execute the active spec to a concrete result in one shot when possible. If prior work was interrupted, recover state from the task package and continue from the next incomplete item.

The main session is the orchestrator: work ready tasks in dependency order, run each task's verify before checking it off, and keep going until the package converges. Bounded sidecar work follows the orchestration contract in references/orchestration.md after a route_decision.py call; loop-shaped work follows a managed slot protocol. Stop at any failed gate or blocker and report it.
EOF_BODY
      ;;
    check)
      cat <<EOF_BODY
# /$command_name

Run an extra human-in-the-loop review pass: inspect implementation, evidence, gaps, and write required fixes back into the spec package. The machine gate is check_spec_package.py: tasks must be complete, checklist must be fully checked with a passing result, every task needs boundary/verify, and checklist evidence must include non-placeholder script/test/build proof plus any enabled consistency or structure gates.
EOF_BODY
      ;;
    done)
      cat <<EOF_BODY
# /$command_name

Close the task: verify completion, extract reusable knowledge, archive the task package, and create the local Git commit. Do not merge, push, or delete branches in this stage; use spec:push for the post-commit Git handoff.
EOF_BODY
      ;;
    push)
      cat <<EOF_BODY
# /$command_name

After spec:done and a committed working branch, run the Git handoff flow: safety precheck, merge the branch into main, push main, and delete the merged local and remote branch.
EOF_BODY
      ;;
    update)
      cat <<EOF_BODY
# /$command_name

Adjust the current spec during execution: add, remove, or reorder tasks and update scope or acceptance criteria when needed.
EOF_BODY
      ;;
    status)
      cat <<EOF_BODY
# /$command_name

Show the user a concise overview across active task packages, including each package's phase, progress, blockers, and next action.
EOF_BODY
      ;;
    doctor)
      cat <<EOF_BODY
# /$command_name

Run the spec environment doctor: check Python/git foundations, the installed skill package, every host installation, Claude stage command files and legacy layout leftovers, the target project's .spec skeleton, and rendered Git hook drift. With --fix apply the safe repairs (installer-owned paths only, backed up first). Report from the script output; do not restate the check list by hand.
EOF_BODY
      ;;
    organize)
      cat <<EOF_BODY
# /$command_name

Run the spec structure organize audit from \`scripts/organize_project_structure.py\`: top-level inventory, the external reference graph (live vs historical), deprecated-structure candidates, and declared-architecture consistency. Default: create a new task package in the target project before any move or doc fix (default new package). Do not append findings to an unrelated active package. Audit-only is an explicit opt-out, not the default. never delete; archive confirmed orphans under archive/retired, and disposition every member of a confirmed class. Report facts and actual archive moves; do not restate the audit report by hand.
EOF_BODY
      ;;
    new)
      cat <<EOF_BODY
# /$command_name

Create a new spec task package with scope, tasks, and acceptance criteria before implementation.
EOF_BODY
      ;;
    *)
      cat <<EOF_BODY
# /$command_name

Use the installed spec skill for this workflow stage.
EOF_BODY
      ;;
  esac
}

write_claude_command() {
  local command_name="$1"
  local target_file="$2"
  local stage="$3"
  local description skill_ref
  description="$(stage_description "$stage")"
  skill_ref="spec"
  ensure_private_dir "$(dirname "$target_file")"
  write_file_from_stdin "$target_file" <<EOF_COMMAND
---
description: $description
---

$(command_body "$command_name" "$stage")

Use the installed skill \`$skill_ref\` for this request. Forward user arguments unchanged.
EOF_COMMAND
  write_file_from_stdin "$target_file.spec-skill-install" <<EOF_MARKER
installed_by=spec/install.sh
target=spec
stage=$stage
EOF_MARKER
}

write_claude_alias() {
  local alias_name="$1"
  local target_dir="$2"
  local stage="$3"
  local description
  description="$(stage_description "$stage")"
  ensure_private_dir "$target_dir"
  write_file_from_stdin "$target_dir/SKILL.md" <<EOF_ALIAS
---
name: $alias_name
description: $description
---

# /$alias_name

This is a Claude Code slash-command alias for the installed spec skill.

Use ../spec/SKILL.md as the canonical workflow definition, then follow the /$alias_name section in ../spec/references/commands.md and ../spec/references/output-contracts.md.

Do not create a separate workflow. Treat this alias as stage '$stage' of the same spec new/goal/run/check/done/push/update/status task-package workflow.
EOF_ALIAS
  write_file_from_stdin "$target_dir/.spec-skill-install" <<EOF_MARKER
installed_by=spec/install.sh
target=spec
stage=$stage
EOF_MARKER
}

remove_installer_owned() {
  local target="$1"
  target_exists "$target" || return 0
  if is_installer_owned "$target"; then
    backup_existing "$target"
  fi
}

preflight_claude_targets() {
  local target="$CLAUDE_SKILLS_DIR/$SKILL_NAME"
  local stage alias_command
  ensure_replaceable "$target"
  for stage in "${CLAUDE_ALIASES[@]}"; do
    alias_command="$CLAUDE_COMMANDS_DIR/$SKILL_NAME/$stage.md"
    ensure_replaceable "$alias_command"
  done
}

preflight_claude_agent_targets() {
  local name target
  for name in orchestrator planner reviewer confirmer; do
    target="$CLAUDE_AGENTS_DIR/$name.md"
    preflight_native_target "$target"
  done
}

install_claude_agents() {
  local runtime_dir="$1"
  local name source target
  ensure_private_dir "$CLAUDE_AGENTS_DIR"
  for name in orchestrator planner reviewer confirmer; do
    source="$runtime_dir/agents/$name.md"
    target="$CLAUDE_AGENTS_DIR/$name.md"
    [ -f "$source" ] || fail "sidecar agent contract missing from runtime package: $source"
    backup_orphan_native_marker "$target"
    backup_existing "$target"
    cp "$source" "$target"
    chmod 600 "$target"
    write_file_from_stdin "$target.spec-skill-install" <<EOF_MARKER
installed_by=spec/install.sh
source=$source
EOF_MARKER
    log "Installing Claude Code native agent $name to $target"
  done
}

# Detect same-name skills from a different source at the install targets and
# warn prominently. Refuse unless FORCE=1, which backs the foreign skill up
# before replacing it. This keeps a foreign "spec" skill (for example a router
# variant) from being silently overwritten.
check_name_collisions() {
  local host target
  local collisions=()
  if host_enabled claude; then
    target="$CLAUDE_SKILLS_DIR/$SKILL_NAME"
    if is_foreign_skill "$target"; then
      collisions+=("$target")
    fi
  fi
  for host in "${SKILL_ONLY_HOSTS[@]}"; do
    host_enabled "$host" || continue
    if [ "$host" = zcode ] && zcode_link_current; then
      continue
    fi
    target="$(skill_dir_for "$host")/$SKILL_NAME"
    if is_foreign_skill "$target"; then
      collisions+=("$target")
    fi
  done
  [ "${#collisions[@]}" -gt 0 ] || return 0

  log ""
  log "WARNING: a different '$SKILL_NAME' skill is already installed at:"
  for target in "${collisions[@]}"; do
    log "  - $target"
  done
  log ""
  log "Two skills named '$SKILL_NAME' cannot coexist in the same host; installing"
  log "this one would replace the other. The existing target was not installed by"
  log "this installer, so it will not be touched automatically."
  log ""
  if [ "$FORCE" = "1" ]; then
    log "FORCE=1 is set: each target above will be backed up under $BACKUP_ROOT"
    log "before being replaced."
    return 0
  fi
  fail "Refusing to overwrite a different '$SKILL_NAME' skill. Re-run with FORCE=1 to back it up and replace it, or remove/rename the existing skill first."
}

preflight_native_target() {
  local target="$1"
  local marker="$1.spec-skill-install"
  local parent
  parent="$(dirname "$target")"
  # Match the directory checks performed by publication before writing any
  # launcher/skill/extension, not only when this particular file is reached.
  reject_symlink "$parent"
  if target_exists "$parent"; then
    [ -d "$parent" ] || fail "native install parent is not a directory: $parent"
  fi
  ensure_replaceable "$target"
  reject_symlink "$marker"
  if target_exists "$marker"; then
    [ -f "$marker" ] || fail "native install marker is not a regular file: $marker"
    if ! target_exists "$target"; then
      ensure_replaceable "$marker"
    fi
  fi
}

backup_orphan_native_marker() {
  local target="$1"
  if ! target_exists "$target" && target_exists "$target.spec-skill-install"; then
    backup_existing "$target.spec-skill-install"
  fi
}

preflight_targets() {
  local host dir name
  if host_enabled claude; then
    require_allowed_root "Claude Code" "$CLAUDE_SKILLS_DIR" "$HOME/.claude/skills"
    require_allowed_root "Claude Code commands" "$CLAUDE_COMMANDS_DIR" "$HOME/.claude/commands"
    require_allowed_root "Claude Code agents" "$CLAUDE_AGENTS_DIR" "$HOME/.claude/agents"
    preflight_claude_targets
    preflight_claude_agent_targets
  fi
  for host in "${SKILL_ONLY_HOSTS[@]}"; do
    host_enabled "$host" || continue
    if [ "$host" = zcode ] && zcode_link_current; then
      continue
    fi
    dir="$(skill_dir_for "$host")"
    require_allowed_root "$(host_label_for "$host")" "$dir" "$(default_skill_dir_for "$host")"
    ensure_replaceable "$dir/$SKILL_NAME"
  done
}

install_claude() {
  local runtime_dir="$1"
  local target="$CLAUDE_SKILLS_DIR/$SKILL_NAME"
  log "Installing Claude Code skill to $target"
  copy_runtime "$runtime_dir" "$target"

  local stage alias_dir alias_command command_target
  command_target="$CLAUDE_COMMANDS_DIR/$SKILL_NAME.md"

  # Clean up older installer layouts so they don't linger as duplicate slash
  # commands. The skill itself provides /spec (no base command file). Stage
  # commands now live under commands/spec/<stage>.md, which Claude Code maps
  # to /spec:<stage>. Anything this installer previously created is moved to
  # backups first. A pre-existing /spec command file must be handled even when
  # it is not installer-owned: leaving it in place would create a duplicate
  # legacy command next to the skill-provided /spec entry. FORCE=1 backs it up;
  # without FORCE, backup_existing refuses user-owned files.
  backup_existing "$command_target"

  # Sweep the previous per-stage alias skill dirs (a very old layout).
  for stage in "${CLAUDE_ALIASES[@]}" "${STALE_CLAUDE_ALIASES[@]}"; do
    alias_dir="$CLAUDE_SKILLS_DIR/$SKILL_NAME:$stage"
    remove_installer_owned "$alias_dir"
  done

  # Remove the previous colon-named command files (spec:<stage>.md) for every
  # shipped and dropped stage. The new layout stores these as spec/<stage>.md
  # because Windows NTFS forbids ':' in filenames: the Node.js (Win32) API that
  # Claude Code uses read spec:check.md as speccheck.md, so /spec:check was
  # never registered and surfaced as "Did you mean speccheck?". The colon name
  # is still reachable from this bash installer, so removal works here.
  for stage in "${CLAUDE_ALIASES[@]}" "${STALE_CLAUDE_ALIASES[@]}"; do
    alias_command="$CLAUDE_COMMANDS_DIR/$SKILL_NAME:$stage.md"
    remove_installer_owned "$alias_command"
  done

  # Sweep retired stages that shipped in the current subdirectory layout
  # (commands/spec/<stage>.md). The swarm stages are gone from USER_STAGES,
  # but nothing removed their files on upgrade — leaving them in place would
  # keep dead /spec:<stage> commands registered next to the live stages.
  for stage in "${STALE_CLAUDE_SUBDIR_STAGES[@]}"; do
    subdir_command="$CLAUDE_COMMANDS_DIR/$SKILL_NAME/$stage.md"
    remove_installer_owned "$subdir_command"
  done

  # Write the stage commands under the subdirectory layout: spec/<stage>.md,
  # which Claude Code resolves as /spec:<stage> on every platform.
  for stage in "${CLAUDE_ALIASES[@]}"; do
    alias_command="$CLAUDE_COMMANDS_DIR/$SKILL_NAME/$stage.md"
    backup_existing "$alias_command"
    write_claude_command "$SKILL_NAME:$stage" "$alias_command" "$stage"
  done

  install_claude_agents "$runtime_dir"
}

install_skill_only_host() {
  local host="$1"
  local runtime_dir="$2"
  local target
  target="$(skill_dir_for "$host")/$SKILL_NAME"
  log "Installing $(host_label_for "$host") skill to $target"
  copy_runtime "$runtime_dir" "$target"
}

# ZCode users often symlink ~/.zcode/skills/spec to another host's install.
# When the symlink points at a target this installer keeps current, it already
# delivers the latest skill and no separate copy is needed.
zcode_link_current() {
  local link="$ZCODE_SKILLS_DIR/$SKILL_NAME"
  local target
  [ -L "$link" ] || return 1
  target="$(readlink "$link")"
  if host_enabled claude && [ "$target" = "$CLAUDE_SKILLS_DIR/$SKILL_NAME" ]; then
    return 0
  fi
  if host_enabled codex && [ "$target" = "$CODEX_SKILLS_DIR/$SKILL_NAME" ]; then
    return 0
  fi
  return 1
}

main() {
  need_cmd mktemp
  PYTHON_BIN="$(resolve_python)" || fail "missing a working python3 or python on PATH (Windows: the python3 stub may be broken; install Python or ensure python is on PATH)"
  validate_inputs
  trap cleanup_temp EXIT

  local runtime_dir
  prepare_source
  INSTALL_TEMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/spec-skill-install.XXXXXX")"
  runtime_dir="$INSTALL_TEMP_DIR/runtime"

  export_runtime "$PREPARED_SOURCE_DIR" "$runtime_dir"
  CLAUDE_ALIASES=("${USER_STAGES[@]}")
  check_name_collisions
  preflight_targets

  if host_enabled claude; then
    install_claude "$runtime_dir"
  fi
  local host
  for host in "${SKILL_ONLY_HOSTS[@]}"; do
    host_enabled "$host" || continue
    if [ "$host" = zcode ] && zcode_link_current; then
      log "ZCode skill already current via symlink: $ZCODE_SKILLS_DIR/$SKILL_NAME -> $(readlink "$ZCODE_SKILLS_DIR/$SKILL_NAME")"
      continue
    fi
    install_skill_only_host "$host" "$runtime_dir"
  done

  log ""
  log "Installed spec skill. Verify with:"
  if host_enabled claude; then
    log "  $PYTHON_BIN $CLAUDE_SKILLS_DIR/$SKILL_NAME/scripts/smoke_test_spec_skill.py"
    log "  Claude Code commands: /spec, $(format_stage_aliases '/spec:' ', ')"
  fi
  for host in "${SKILL_ONLY_HOSTS[@]}"; do
    host_enabled "$host" || continue
    log "  $PYTHON_BIN $(skill_dir_for "$host")/$SKILL_NAME/scripts/smoke_test_spec_skill.py"
    log "  $(host_label_for "$host"): $(host_trigger_hint "$host"), then use $(format_stage_aliases 'spec:' ' / ')"
  done

  # AGENTS.md 兼读提示：AGENTS.md 是 Codex/OpenCode/Gemini CLI 等 agent 的
  # 指令入口，与 SKILL.md 同层生效。仅提示，不改安装逻辑。
  if [ -f "$PWD/AGENTS.md" ]; then
    log ""
    log "Note: AGENTS.md detected in $PWD. Hosts that read AGENTS.md (Codex,"
    log "OpenCode, Gemini CLI, ...) load it at the same instruction layer as this"
    log "skill's SKILL.md; keep workflow rules aligned across both, and mention the"
    log "spec skill in AGENTS.md if the project relies on it."
  fi
}

main "$@"
