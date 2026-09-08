# Git-tools

Personal collection of small CLI helper scripts built around the GitHub CLI
(`gh`).

## Tools

### `check_my_work.py`

Summarizes outstanding GitHub work for the current user:

1. Problems on your own open pull requests: merge conflicts, failing/pending
   CI checks, unresolved review threads, and outstanding "changes requested"
   review decisions. A "changes requested" review is only reported as
   actionable if a review thread is still unresolved, or a reviewer who
   requested changes hasn't yet been re-requested to look again -- if every
   thread is resolved and a re-review has already been requested, the PR is
   reported clean (the ball is in the reviewer's court, not yours).
2. Open pull requests from other authors that are waiting on your review.

Requires the [GitHub CLI](https://cli.github.com/) (`gh`) to be installed and
authenticated (`gh auth status`).

```sh
# Check the current repository
./check_my_work.py

# Check across every repository you can see
./check_my_work.py --all-repos

# Check a specific repository
./check_my_work.py --repo owner/name

# Emit a machine-readable JSON report instead of text
./check_my_work.py --json
```

The JSON report includes `total_pr_count`, `own_pr_count`, and
`review_request_count` alongside per-PR details, and exits non-zero when
there is anything actionable.

### `generate_delivery_plan.py`

Generates a Markdown delivery plan from GitHub issues, grouped by date.
Issues come from one or more milestones and/or an explicit list of issue
numbers, and are grouped by a target date:

- A milestone's due date, if set on GitHub.
- An explicit override via `--due "Milestone Name=YYYY-MM-DD"`, useful for
  repos (like most) that don't set milestone due dates.
- Otherwise the issue is listed under "Unscheduled".

Requires the [GitHub CLI](https://cli.github.com/) (`gh`) to be installed and
authenticated (`gh auth status`).

```sh
# Plan every milestone in the current repo, using GitHub's due dates
./generate_delivery_plan.py

# Assign due dates to milestones that don't have one on GitHub
./generate_delivery_plan.py \
  --due "Production ready=2026-10-01" \
  --due "Profile Retrieval & Update=2026-09-30"

# Restrict to specific milestones and/or issues
./generate_delivery_plan.py --milestone "Production ready" --issue 350

# Only show what's due in a date range
./generate_delivery_plan.py --since 2026-09-01 --until 2026-09-30

# Include closed issues too, and write the plan to a file
./generate_delivery_plan.py --state all --output delivery-plan.md
```

The summary table shows open/closed/total counts and completion percentage
per date and milestone; each dated section lists issues as a Markdown
checklist (checked when closed), with assignees and labels.
