# Issue tracker: GitHub

Specifications, implementation tasks, and Wayfinding work for this repository live as GitHub Issues in `1012839419a-alt/opencli-Razormind-gjx`.

## Commands

Every GitHub CLI command MUST pass the repository explicitly:

```text
gh issue create --repo 1012839419a-alt/opencli-Razormind-gjx
gh issue view <number> --repo 1012839419a-alt/opencli-Razormind-gjx --comments
gh issue list --repo 1012839419a-alt/opencli-Razormind-gjx
gh issue comment <number> --repo 1012839419a-alt/opencli-Razormind-gjx
gh issue edit <number> --repo 1012839419a-alt/opencli-Razormind-gjx
gh issue close <number> --repo 1012839419a-alt/opencli-Razormind-gjx
gh issue reopen <number> --repo 1012839419a-alt/opencli-Razormind-gjx
gh issue list --repo 1012839419a-alt/opencli-Razormind-gjx --label <label>
```

Use JSON fields appropriate to the operation. Never infer the repository from the current checkout or remote when a command supports `--repo`.

## Wayfinding operations

Wayfinding maps the path from a product frontier to implementation-ready work. Prefer native GitHub Issues and sub-issues when the target repository supports them. When native sub-issue operations are unavailable, use a parent map issue plus a task list in its body, with each child task linked to a separate issue where useful.

- **map**: create or update one parent map issue describing the destination, evidence, decisions, dependencies, frontier, and child work.
- **child**: create native GitHub sub-issues when supported; otherwise create linked child issues and maintain the parent task list.
- **native sub-issue fallback/task list**: if native sub-issue commands or permissions are unavailable, record `- [ ] #<number> ...` links in the parent map issue and preserve dependency order.
- **native dependencies fallback**: if native issue dependency fields are unavailable, record explicit `Blocked by`, `Depends on`, and `Unblocks` issue links in issue bodies/comments; do not treat labels as dependency state.
- **frontier**: identify the next unresolved product/technical edge, its evidence, and the smallest decision needed before creating work.
- **claim**: record an evidence-backed claim with source paths/issue links, confidence, and scope; distinguish observed facts from inference.
- **resolve**: close a Wayfinding question by recording the chosen path, rejected alternatives, acceptance evidence, and resulting child/task links.

Wayfinding reads and writes use only GitHub Issues in `1012839419a-alt/opencli-Razormind-gjx` unless a task explicitly authorizes another surface. Do not create a map issue until the operator requests it.

## Pull requests as a triage surface

PRs as a request surface: no. Pull requests may reference Issues, but they do not enter the Issue triage queue automatically.
