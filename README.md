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
