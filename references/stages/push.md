# `/spec:push`

Goal: after `done`, merge into main, push, and delete the merged branch. Only with explicit user authorization.

```bash
python3 scripts/push_spec_package.py --root <git-repo> --branch <working-branch> [--main-branch main] [--remote origin] [--slug <slug>]
```

- Scope: by default it gates the packages the branch touches; every non-merge commit needs a `Spec: <slug>` footer, or `Spec: none (<reason>)` for unpackaged trivial work. Unarchived packages in scope fail.
- Order: clean tree → merge → push main → confirm remote containment → delete remote branch → delete local branch. Protected branches are never deleted; only `spec/` branches are merged.
- On failure it aborts an in-progress merge and returns to the original branch when the tree is clean. A failed push of main leaves the local merge in place; report the actual Git state instead of assuming a rollback. Auth failures, conflicts and broken remote hooks stop without deleting anything.
- A dirty tree is packed first: commit package changes, put unrelated changes into their own package or commit.

Report the actual merge target, remote state and branch cleanup after they happen.
