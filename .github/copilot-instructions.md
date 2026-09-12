# Copilot instructions for Git-tools

Personal collection of small, standalone CLI helper scripts built around the
GitHub CLI (`gh`). There is no shared library, package, build system, test
suite, or linter — each script in the repo root is self-contained and can be
read/modified independently.

## Running the tools

All Python scripts are executable and use `#!/usr/bin/env python3` (no
dependencies beyond the standard library). Run them directly, e.g.:

```sh
./check_my_work.py --repo owner/name
./generate_delivery_plan.py --milestone "Production ready"
./list_python_on_path.py --broken-only
```

Every `.py` and `.sh` script requires the [GitHub CLI](https://cli.github.com/)
(`gh`) installed and authenticated (`gh auth status`) except
`list_python_on_path.py` and `prune-local-branches.sh`, which only use local
`git`/filesystem state.

There are no automated tests or linters configured. Validate changes by
running the affected script against a real repo (or `--json` output) and
checking the exit code / printed report by hand.

## Conventions shared across the Python scripts

- Every `gh` invocation goes through a shared `run_gh()` / `run_gh_json()`
  pair (duplicated per-script, not a shared module) that runs `gh` via
  `subprocess.run` with a static argument list, raises `SystemExit` with
  `gh`'s stderr on failure, and JSON-decodes stdout when needed. Follow this
  pattern for any new `gh` calls instead of shelling out ad hoc.
- Scripts default to operating on the current repo (via
  `gh repo view --json owner,name`) but accept `--repo owner/name` and, where
  it makes sense, `--all-repos`/`--state` to broaden scope.
- Every script supports both a human-readable text report (default) and a
  `--json` machine-readable report, and uses the JSON report's derived
  boolean(s) (e.g. `clean`, `has_own_issues`) to set the process exit code:
  0 when there's nothing actionable, 1 otherwise. Keep this contract when
  adding new reports.
- Domain data is modeled with small `@dataclass` types (e.g. `PrIssues`,
  `Issue`, `Milestone`) with a `to_json()`/`has_issues`-style helper property
  rather than passing raw dicts around.
- GraphQL is used instead of REST when data isn't otherwise available via
  `gh`'s JSON flags (see `review_thread_and_request_status` in
  `check_my_work.py` for review-thread/re-review state, and the REST
  `issues` endpoint in `generate_delivery_plan.py` for `sub_issues_summary`,
  which `gh issue list --json` doesn't expose).
- Module docstrings are the primary documentation for each script's behavior
  and flags; keep them in sync with `argparse` definitions and mirror
  relevant changes into `README.md`.

## Architecture notes

- `check_my_work.py`: reports (1) problems on the user's own open PRs
  (conflicts, failing/pending checks, unresolved review threads, actionable
  "changes requested" reviews) and (2) PRs awaiting the user's review. The
  "changes requested" logic specifically distinguishes reviewers who haven't
  been re-requested yet (actionable) from those already re-requested (ball's
  in their court) — see `review_thread_and_request_status`.
- `generate_delivery_plan.py`: builds a Markdown plan from issues grouped by
  milestone due date (or `--due` overrides, or "Unscheduled"). Issue ordering
  within a milestone is dependency-aware: `order_by_dependencies` does a
  DFS/topological sort using `Issue.depends_on`, which combines GitHub's
  native sub-issues (an umbrella issue depends on its sub-issues) with
  "depends on #N" / "blocked by #N" / "requires #N" phrases parsed from the
  issue body via `DEPENDENCY_PATTERN`.
- `list_python_on_path.py`: scans `$PATH` for Python scripts/symlinks
  (matched by `.py` suffix or shebang) and flags broken symlinks; this is how
  the other scripts in this repo are meant to be exposed globally (symlinked
  from e.g. `~/.local/bin`).
- `prune-local-branches.sh` / `prune-remote-branches.sh`: interactive Bash
  scripts (`set -euo pipefail`) that delete local branches with no matching
  remote branch, or remote branches with no local counterpart and no
  associated PR, respectively. Both prompt for confirmation before deleting
  and take an optional remote name as `$1` (default `origin`).
