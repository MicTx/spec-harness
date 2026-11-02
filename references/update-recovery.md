# Safe update recovery examples

Use these procedures only under the safety contract in [`stages/update.md`](stages/update.md). They are loaded when an update needs a preimage or historical checkpoint recovery.

### Update rollback-safety contract (ordinary and adopted packages)

The same rules apply when `adoption.json` is present: an adopted package shares the tree with teammates; neither it nor an ordinary update authorizes staging or committing. Do not change the Git index, create commits, reset a branch, or revert a commit range as part of a routine update. An existing commit is not a substitute for a copy of dirty or untracked files. Follow the user's Git authorization separately.

**Trust and metadata prerequisites:** Use these examples only with trusted host executables, interpreter, environment and Git configuration, including the target repository's `.git/config`. Git operations such as `git status` can execute configured commands (for example `core.fsmonitor`); `python3 -I` prevents Python project/PYTHONPATH imports, not Git configuration or environment execution. If that trust cannot be established, stop and arrange manual inspection in a trusted environment.

**Before rewriting:** coordinate exclusive access to every exact file to be changed, including any source-of-truth outside the package; if that cannot be arranged, stop. Inspect `GIT_OPTIONAL_LOCKS=0 git status --short`, `GIT_OPTIONAL_LOCKS=0 git diff --literal-pathspecs -- <path>`, and `GIT_OPTIONAL_LOCKS=0 git diff --cached --literal-pathspecs -- <path>`; the index may already contain unrelated changes or even a different staged version of the same file. Do not stage or unstage them. Make a private, durable, out-of-tree copy of the current working-tree bytes, mode and existence of every planned path. Never put recovery copies in the package or rely on a temporary directory that may be cleaned before recovery. If a path or any parent is a symlink, or the target is non-regular or multiply linked, stop and arrange a safe manual backup instead. Preserve this copy until the update is accepted or recovered.

These examples intentionally support only files with **no ACL/xattr and no setuid, setgid or sticky bits**. First establish that the operating system, filesystem and caller can reliably enumerate **every** relevant ACL and xattr namespace for both project and backup files (including `system.*`, `security.*` and `trusted.*`), and that listing errors are reported. For example, Linux can hide `trusted.*` from an unprivileged caller: an empty list alone does not establish absence. If visibility or filesystem semantics cannot be established, or richer metadata must be preserved, stop and use a capable, independently verified manual backup/recovery procedure. These examples do not preserve inode identity, ctime, birth time, filesystem flags or access time (even reading can update atime); if these or any other unverified metadata matter, stop. Never infer that metadata is absent from a failed or filtered query.

Only after independently establishing the full-visibility and metadata prerequisites, set `recovery_metadata_visibility=verified` in the current trusted shell; leave it unset if any prerequisite is uncertain, and re-establish it after a change of caller, mount or environment. This explicit attestation is required by the helpers, not a way to bypass the prerequisites. Run the following helper definitions in each trusted shell session before any example, including after resuming. They reject visible ACL/xattr, special permission bits, links and non-regular/multiply-linked files, and stop on inspection errors. They compare bytes, full mode, owner, group, nanosecond mtime and link count. They do **not** prove the full-visibility prerequisite above. Recovery is staged beside the target and installed only after verification; all writers must remain stopped throughout, including during the final rename/removal. These checks are not a concurrency lock.

~~~bash
recovery_metadata_safe() {
  if [ "${recovery_metadata_visibility:-}" != verified ]; then
    echo 'Metadata visibility not verified: stop.' >&2
    return 1
  fi
  python3 -I - "$@" <<'PY'
import os, stat, sys
from pathlib import Path

for raw in sys.argv[1:]:
    p = Path(raw)
    if not raw or not p.parts or '..' in p.parts or p in (Path('.'), Path('/')):
        raise SystemExit(f'invalid recovery path: {raw!r}')
    for part in (p, *p.parents):
        if part == Path('.'):
            continue
        try:
            st = part.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise SystemExit(f'cannot inspect {part}: {exc}')
        if stat.S_ISLNK(st.st_mode):
            raise SystemExit(f'symlink is unsafe: {part}')
        if part != p:
            if not stat.S_ISDIR(st.st_mode):
                raise SystemExit(f'not a directory: {part}')
            continue
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
            raise SystemExit(f'not a single-link regular file: {part}')
        if st.st_mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX):
            raise SystemExit(f'special permission bits are unsafe: {part}')
        try:
            attrs = os.listxattr(part, follow_symlinks=False)
        except (AttributeError, NotImplementedError, OSError) as exc:
            raise SystemExit(f'cannot inspect ACL/xattr of {part}: {exc}')
        if attrs:
            raise SystemExit(f'ACL/xattr requires manual recovery: {part}')
PY
}

recovery_same() {
  recovery_metadata_safe "$1" "$2" && cmp -s -- "$1" "$2" &&
  python3 -I - "$1" "$2" <<'PY'
import os, stat, sys

def metadata(path):
    s = os.stat(path, follow_symlinks=False)
    return stat.S_IMODE(s.st_mode), s.st_uid, s.st_gid, s.st_mtime_ns, s.st_nlink

if metadata(sys.argv[1]) != metadata(sys.argv[2]):
    raise SystemExit('metadata differs')
PY
}

recovery_postimage_same() {
  recovery_metadata_safe "$path" "$step/path" "$step/present" "$step/absent" &&
  printf '%s\0' "$path" | cmp -s - "$step/path" || return 1
  if [ -f "$step/present" ] && [ ! -e "$step/absent" ]; then
    recovery_same "$path" "$step/present"
  elif [ -f "$step/absent" ] && [ ! -e "$step/present" ]; then
    [ ! -e "$path" ] && [ ! -L "$path" ]
  else
    return 1
  fi
}

recovery_binding_ok() {
  # Fail-closed: the postimage step must live inside the current backup, and
  # the backup must be bound to this project's worktree root. This refuses a
  # same-relative-path backup or postimage captured in a different checkout,
  # including across sessions.
  local root
  [ -n "$backup" ] && [ -n "$step" ] || return 1
  case "$step/" in "$backup/"*) ;; *) return 1;; esac
  [ -f "$backup/root" ] || return 1
  root=$(GIT_OPTIONAL_LOCKS=0 git rev-parse --show-toplevel) 2>/dev/null || return 1
  root=$(cd "$root" && pwd -P) 2>/dev/null || return 1
  [ "$(cat -- "$backup/root")" = "$root" ] || return 1
}

recovery_install_preimage() {
  local source=$1 target=$2 stage temp
  recovery_metadata_safe "$source" "$target" || return 1
  stage=$(umask 077; mktemp -d -- "${target}.recovery.XXXXXXXX") || return 1
  temp="$stage/preimage"
  if cp -p -- "$source" "$temp" && recovery_same "$source" "$temp" &&
     recovery_postimage_same && mv -fT -- "$temp" "$target"; then
    rmdir -- "$stage"
    return 0
  fi
  rm -f -- "$temp"; rmdir -- "$stage"
  return 1
}

recovery_install_legacy() {
  local legacy=$1 target=$2 stage temp
  recovery_metadata_safe "$legacy" && recovery_postimage_same || return 1
  stage=$(umask 077; mktemp -d -- "${target}.recovery.XXXXXXXX") || return 1
  temp="$stage/preimage"
  if cp -p -- "$step/present" "$temp" && recovery_same "$step/present" "$temp" &&
     cat -- "$legacy" > "$temp" && recovery_metadata_safe "$temp" &&
     cmp -s -- "$legacy" "$temp" &&
     python3 -I - "$step/present" "$temp" <<'PY'
import os, stat, sys

def permissions(path):
    s = os.stat(path, follow_symlinks=False)
    return stat.S_IMODE(s.st_mode), s.st_uid, s.st_gid

if permissions(sys.argv[1]) != permissions(sys.argv[2]):
    raise SystemExit('legacy target permissions differ')
PY
  then
    if recovery_postimage_same && mv -fT -- "$temp" "$target"; then
      rmdir -- "$stage"
      return 0
    fi
  fi
  rm -f -- "$temp"; rmdir -- "$stage"
  return 1
}
~~~

The following Bash/GNU-tools example captures the usual triad. Run from the project root after assigning `pkg` to the exact repository-relative package path, and append any other planned paths to `paths` **before** running it. Set `TMPDIR` to a private durable location outside the repository if the default `/tmp` may be cleared. On any failure do not rewrite. An absent checklist is recorded as absent, not silently treated as an empty file.

~~~bash
paths=("$pkg/spec.md" "$pkg/tasks.md" "$pkg/checklist.md")
# Example: paths+=("references/actual-source-of-truth.md")
if backup=$(
  set -eu
  umask 077
  root=$(git rev-parse --show-toplevel) || exit 1
  root=$(cd "$root" && pwd -P) || exit 1
  dest=$(mktemp -d "${TMPDIR:-/tmp}/spec-update.XXXXXXXX") || exit 1
  dest=$(cd "$dest" && pwd -P) || exit 1
  if [ "$dest" = "$root" ] || [ "${dest#"$root/"}" != "$dest" ]; then
    echo 'backup must be outside the repository' >&2
    exit 1
  fi
  printf '%s\n' "$root" > "$dest/root" || exit 1
  for path in "${paths[@]}"; do
    # Refuse traversal, links and non-regular/multiply-linked targets or parents.
    python3 -I - "$path" <<'PY' || exit 1
import stat, sys
from pathlib import Path
p = Path(sys.argv[1])
if p.is_absolute() or '..' in p.parts or not p.parts:
    raise SystemExit('invalid recovery path')
for part in (p, *p.parents):
    if part == Path('.'):
        continue
    try:
        st = part.lstat()
    except FileNotFoundError:
        continue
    if stat.S_ISLNK(st.st_mode):
        raise SystemExit(f'symlink is unsafe: {part}')
    if part == p:
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
            raise SystemExit(f'not an unlinked regular file: {part}')
    elif not stat.S_ISDIR(st.st_mode):
        raise SystemExit(f'not a directory: {part}')
PY
    recovery_metadata_safe "$path" || exit 1
    dir=$(dirname -- "$path") || exit 1
    if [ -f "$path" ]; then
      mkdir -p -- "$dest/before/$dir" || exit 1
      cp -p -- "$path" "$dest/before/$path" || exit 1
      recovery_same "$path" "$dest/before/$path" || exit 1
    else
      mkdir -p -- "$dest/absent/$dir" || exit 1
      : > "$dest/absent/$path" || exit 1
      recovery_metadata_safe "$dest/absent/$path" || exit 1
    fi
  done
  [ -f "$dest/before/$pkg/spec.md" ] && [ -f "$dest/before/$pkg/tasks.md" ] || exit 1
  printf '%s\n' "$dest"
); then
  printf 'Private recovery directory: %s\n' "$backup"
else
  backup=
  echo 'Recovery copy failed: do not rewrite.' >&2
  false
fi
~~~

**Capture the expected result after each rewrite** while exclusive access is still held, using a fresh step directory for that one exact path. The path must still satisfy the same no-symlink/regular-file/single-link conditions; if it does not, stop. Save the step path and the recovery directory somewhere the resuming session can find them without exposing their contents. This example handles either an existing file or a deletion:

~~~bash
path="$pkg/tasks.md"                 # the one path just rewritten
step=
if [ -n "$backup" ] && step=$(umask 077; mktemp -d "$backup/step.XXXXXXXX"); then
  if ! python3 -I - "$path" <<'PY'
import stat, sys
from pathlib import Path
p = Path(sys.argv[1])
if p.is_absolute() or '..' in p.parts or not p.parts:
    raise SystemExit('invalid recovery path')
for part in (p, *p.parents):
    if part == Path('.'):
        continue
    try:
        st = part.lstat()
    except FileNotFoundError:
        continue
    if stat.S_ISLNK(st.st_mode):
        raise SystemExit(f'symlink is unsafe: {part}')
    if part == p:
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
            raise SystemExit(f'not a single-link regular file: {part}')
    elif not stat.S_ISDIR(st.st_mode):
        raise SystemExit(f'not a directory: {part}')
PY
  then
    step=
  elif ! recovery_metadata_safe "$path"; then
    step=
  elif ! printf '%s\0' "$path" > "$step/path" || ! recovery_metadata_safe "$step/path"; then
    step=
  elif [ -f "$path" ] && [ ! -L "$path" ]; then
    cp -p -- "$path" "$step/present" && recovery_same "$path" "$step/present" || step=
  elif [ ! -e "$path" ] && [ ! -L "$path" ]; then
    : > "$step/absent" && recovery_metadata_safe "$step/absent" || step=
  else
    step=
  fi
fi
[ -n "$step" ] || { echo 'Postimage capture failed: stop.' >&2; false; }
~~~

**Recover only owned changes:** keep writers stopped, inspect the current file and both staged and unstaged diffs, and compare its bytes and mode/metadata with the recorded expected result. If a postimage was never recorded (including interruption mid-write), if it differs, if the path or a parent became a link, or ownership is uncertain, stop: preserve both versions and manually reconcile the exact update hunks with the other writer/user. Never blindly copy a whole package. The following guarded example restores the one `path` captured in `step`; it compares bytes, mode, owner, group, mtime and link count. Recheck the no-symlink-parents condition immediately before running it and keep writers stopped through the copy/removal; this is not an atomic concurrency lock. The index is not touched, even for a partially staged target.

~~~bash
recover_one_path() {
if [ -z "$backup" ] || [ -z "$step" ]; then
  echo 'Missing recovery copy or postimage: stop.' >&2; return 1
elif ! recovery_binding_ok; then
  echo 'Recovery binding mismatch: stop and reconcile.' >&2; return 1
elif ! recovery_postimage_same; then
  echo 'Postimage changed: stop and reconcile.' >&2; return 1
elif ! recovery_metadata_safe "$backup/before/$path" "$backup/absent/$path"; then
  echo 'Preimage metadata is unsafe: stop.' >&2; return 1
elif [ -f "$backup/before/$path" ] && [ ! -e "$backup/absent/$path" ]; then
  recovery_install_preimage "$backup/before/$path" "$path" || {
    echo 'Could not safely restore preimage: stop.' >&2; return 1;
  }
elif [ -f "$backup/absent/$path" ] && [ ! -e "$backup/before/$path" ]; then
  if [ -f "$step/present" ]; then
    recovery_postimage_same && rm -- "$path" || {
      echo 'Postimage changed: stop and reconcile.' >&2; return 1;
    }
  fi
else
  echo 'No unique preimage: stop.' >&2; return 1
fi
}
recover_one_path
~~~

If update changes have already been committed, do not reset the branch or revert a range: identify and inspect the exact affected paths/hunks and intervening commits, then reconcile only the owned update with exclusive access. Treat creating a corrective commit as a separate Git action requiring authorization. Keep-new likewise requires review; only then dispose of the private copies under the user's retention policy.

### Historical `update-checkpoint.json` (retired mechanism)

- **Route advises, gates agree**: a package holding a historical checkpoint (valid or corrupted) gets a prominent route advisory while routing normally; `check`, `done`, and `check_all` refuse. Archived read-only review keeps the file as history and does not fail on it.
- **Valid snapshot**: the JSON contains the actual pre-update text, including work that may never have been committed — Git history alone cannot recover that. First make the out-of-tree recovery copy above of the current affected files and checkpoint (add `paths+=("$pkg/update-checkpoint.json")` immediately after the `paths` assignment). The read-only inspector verifies schema, checksum, and the recorded package basename only; neither that basename nor the unkeyed checksum proves which project the checkpoint came from. Independently establish its project provenance and ownership before using it; if uncertain, stop for manual reconciliation. This example extracts snapshot members to a *new* private directory without touching the package or index. If extraction fails, stop and do not use any partial output. The completion marker is a defense against mistakenly continuing after such a failure, not proof of provenance or a substitute for stopping. Run from the project root with a trusted host-provided Python interpreter. Beforehand set `skill_scripts` to the **absolute scripts directory of the trusted installed Spec skill**, resolved from the host skill installation, not from the target project or its environment. Isolated mode prevents project files and `PYTHONPATH` from supplying the inspector or its dependencies:

~~~bash
: "${skill_scripts:?set skill_scripts to the trusted installed skill scripts directory}"
[ -n "$backup" ] || { echo 'Missing recovery directory: stop.' >&2; exit 1; }
case "$backup" in /*) ;; *) echo 'Recovery directory must be absolute: stop.' >&2; exit 1;; esac
root=$(GIT_OPTIONAL_LOCKS=0 git rev-parse --show-toplevel) 2>/dev/null || { echo 'Cannot resolve project root: stop.' >&2; exit 1; }
root=$(cd "$root" && pwd -P) 2>/dev/null || exit 1
case "$backup/" in "$root/"*) echo 'Recovery directory must be outside the repository: stop.' >&2; exit 1;; esac
python3 -I - "$skill_scripts" "$pkg" "$backup" <<'PY'
import sys
from pathlib import Path
trusted = Path(sys.argv[1])
if not trusted.is_absolute() or not trusted.is_dir():
    raise SystemExit('skill_scripts must be an absolute trusted directory')
sys.path.insert(0, str(trusted))
from update_checkpoint_support import inspect_update_checkpoint
checkpoint = inspect_update_checkpoint(Path(sys.argv[2]))
out = Path(sys.argv[3]) / 'legacy-snapshot'
out.mkdir(mode=0o700)
for name, text in checkpoint.snapshot.items():
    with (out / name).open('x', encoding='utf-8', newline='') as handle:
        handle.write(text)
# Only a fully successful extraction may be used by the example below.
(out / '.complete').touch(exist_ok=False)
print(f'Validated snapshot copies: {out}')
PY
~~~

  Compare these files to the current triad and, if useful, inspect individual historical versions with `git log --oneline --literal-pathspecs -- <path>` and `git show <verified-commit>:<path>`. The legacy snapshot contains text, not file modes or index state; a missing optional checklist is not authorization to delete a current file. With exclusive access and ownership established, reconcile only the selected files/hunks, leaving the index untouched; if it is unclear which edits belong to the update, stop for manual reconciliation.

  For an existing regular `tasks.md` whose **entire** current content has been reviewed as safely replaceable by the validated legacy snapshot, first use the postimage capture block above to record the *current* file in `step`. Then, with writers still stopped and parents rechecked for symlinks, this example copies the **legacy** content, not the half-applied pre-recovery backup. It stages the legacy text and preserves the current target mode, owner and group because the old snapshot did not record them; it requires the completed-extraction marker and refuses unsafe or uninspectable metadata. For a missing file, a partial-hunk choice, or uncertain ownership, use manual reconciliation instead.

~~~bash
path="$pkg/tasks.md"
legacy="$backup/legacy-snapshot/tasks.md"
if [ -n "$backup" ] && [ -n "$step" ] &&
   printf '%s\0' "$path" | cmp -s - "$step/path"; then
  if recovery_metadata_safe "$legacy" "$backup/legacy-snapshot/.complete" &&
     [ -f "$legacy" ] && [ -f "$backup/legacy-snapshot/.complete" ] &&
     [ -f "$step/present" ] && recovery_postimage_same &&
     recovery_install_legacy "$legacy" "$path"; then
    : # installed only after validation
  else
    echo 'Legacy snapshot unavailable or current file changed: stop and reconcile.' >&2
    false
  fi
else
  echo 'Missing recovery copy or matching postimage: stop.' >&2
  false
fi
~~~

  For **corrupt or identity-mismatched** checkpoints, do not trust/extract the payload: retain it and use independently verified backups, current diffs, or explicitly inspected Git history; if those cannot establish a safe state, leave the gate blocked.
- **Resolution**: only after explicitly reviewing keep-new or completing a safe recovery, and after backing up and checking the exact checkpoint for concurrent edits, remove that one working-tree checkpoint and rerun the refused gate. Check the staged version separately: removal from the working tree does not alter the index, and a staged checkpoint could reappear; change the index only if separately authorized and path-scoped. Never automatically clear a corrupt or unresolved checkpoint. With writers still stopped and parent paths rechecked for symlinks, this removes only an unchanged checkpoint that was included in the pre-recovery backup:

~~~bash
path="$pkg/update-checkpoint.json"
if [ -n "$backup" ] && [ -f "$backup/before/$path" ] &&
   recovery_same "$path" "$backup/before/$path"; then
  rm -- "$path"
else
  echo 'Checkpoint changed or not backed up: stop and reconcile.' >&2
  false
fi
~~~

- **Recognition CLI** (`scripts/update_checkpoint.py`, read-only; `begin`/`rollback`/`complete` are retired and refuse with the recovery guidance above). Run it with the **absolute trusted installed skill scripts directory** you set as `skill_scripts` above, never a project-relative path (which could resolve to a project-local script of the same name):

~~~bash
python3 "$skill_scripts/update_checkpoint.py" --root <project> status [--slug YYYY-MM-DD_<slug>]   # 0=none 3=active 4=corrupted
~~~
