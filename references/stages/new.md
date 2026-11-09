# `/spec:new`

Goal: turn a clarified goal into a package with scope, tasks and acceptance.

1. Investigate the request, code and data first; ask only for permission, irreversible choices, genuine preferences or inaccessible external facts.
2. Name the slug `YYYY-MM-DD_<verb>-<object>` (verbs: [`../naming-and-commits.md`](../naming-and-commits.md)).
3. Run:

   ```bash
   python3 scripts/init_spec_package.py --root <project> --slug YYYY-MM-DD_<slug> --title "<title>" [--workflow full]
   ```

   In Git it requires a clean tree (other active packages' own documents excepted), refuses protected or already-bound branches, fast-forwards `main` and creates `spec/<slug>`; follow its error when it stops. `--skip-git-branch` records why the branch step is out of scope. `--adopt --confirm-adopt` binds work already in flight to the current branch ([`../handoff.md`](../handoff.md)).
4. Fill every placeholder ([`../templates.md`](../templates.md)). Each task gets a `boundary` and a real `verify` command; each §6 criterion names its task.
5. Continue into `run`.

Use `--workflow full` when the user asks for it or the work touches auth, billing, secrets, data migration or destructive operations.
