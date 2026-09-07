#!/usr/bin/env python3
"""Summarize outstanding GitHub work for the current user.

Reports two things using the authenticated `gh` CLI:

1. Problems on the user's own open pull requests: merge conflicts, failing/
   pending CI checks, unresolved review threads, and outstanding
   "changes requested" review decisions. A "changes requested" review is
   only reported as actionable if a review thread is still unresolved, or
   a reviewer who requested changes hasn't yet been re-requested to look
   again -- if every thread is resolved and a re-review has already been
   requested, the PR is reported clean (the ball is in the reviewer's
   court, not yours).
2. Open pull requests from other authors that are waiting on the user's
   review.

Requires the GitHub CLI (`gh`) to be installed and authenticated
(`gh auth status`). By default this only looks at the current repository;
pass --all-repos to search across every repository the user can see.
Pass --json to emit a machine-readable JSON report instead of the default
human-readable text.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field

GH_EXECUTABLE = shutil.which("gh") or "gh"


def run_gh(args: list[str]) -> str:
    """Run a `gh` command and return its raw stdout, or exit on failure."""
    # Trusted CLI tool (gh) invoked with a static, code-controlled argument
    # list; no untrusted/user-supplied input reaches the shell.
    result = subprocess.run(  # noqa: S603
        [GH_EXECUTABLE, *args],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SystemExit(
            f"`gh {' '.join(args)}` failed:\n{result.stderr.strip()}"
        )
    return result.stdout


def run_gh_json(args: list[str]) -> object:
    """Run a `gh` command and parse its JSON stdout, or exit on failure."""
    return json.loads(run_gh(args) or "null")


def current_user() -> str:
    return run_gh(["api", "user", "-q", ".login"]).strip()


def current_repo() -> str:
    return run_gh(
        ["repo", "view", "--json", "owner,name", "-q", '.owner.login + "/" + .name']
    ).strip()


@dataclass
class PrIssues:
    number: int
    title: str
    url: str
    repo: str = ""
    conflicting: bool = False
    changes_requested: bool = False
    awaiting_rereview_from: list[str] = field(default_factory=list)
    rereview_pending_from: list[str] = field(default_factory=list)
    failing_checks: list[str] = field(default_factory=list)
    pending_checks: list[str] = field(default_factory=list)
    unresolved_threads: int = 0

    @property
    def has_issues(self) -> bool:
        return bool(
            self.conflicting
            or self.changes_requested
            or self.failing_checks
            or self.pending_checks
            or self.unresolved_threads
        )

    def to_json(self) -> dict:
        data = asdict(self)
        data["has_issues"] = self.has_issues
        return data


def own_open_prs(repo: str | None, all_repos: bool) -> list[dict]:
    args = [
        "search",
        "prs",
        "--author=@me",
        "--state=open",
        "--json",
        "number,title,url,repository",
    ]
    if not all_repos:
        args += ["--repo", repo] if repo else []
    return run_gh_json(args)  # type: ignore[return-value]


def prs_needing_review(repo: str | None, all_repos: bool) -> list[dict]:
    args = [
        "search",
        "prs",
        "--review-requested=@me",
        "--state=open",
        "--json",
        "number,title,url,repository,author",
    ]
    if not all_repos:
        args += ["--repo", repo] if repo else []
    return run_gh_json(args)  # type: ignore[return-value]


def review_thread_and_request_status(
    owner: str, name: str, number: int
) -> tuple[int, list[str], list[str]]:
    """Return (unresolved_thread_count, not_yet_rerequested, already_rerequested).

    The latter two partition the reviewers whose most recent review
    requested changes: those with no pending re-review request from them
    (still actionable) versus those GitHub already shows as pending a fresh
    look (the ball is in their court).
    """
    query = """
    query($owner: String!, $repo: String!, $num: Int!) {
      repository(owner: $owner, name: $repo) {
        pullRequest(number: $num) {
          reviewThreads(first: 100) {
            nodes { isResolved }
          }
          reviews(first: 100) {
            nodes { state author { login } submittedAt }
          }
          reviewRequests(first: 100) {
            nodes { requestedReviewer { ... on User { login } } }
          }
        }
      }
    }
    """
    data = run_gh_json(
        [
            "api",
            "graphql",
            "-f",
            f"query={query}",
            "-f",
            f"owner={owner}",
            "-f",
            f"repo={name}",
            "-F",
            f"num={number}",
        ]
    )
    pr = data["data"]["repository"]["pullRequest"]  # type: ignore[index]
    unresolved = sum(1 for node in pr["reviewThreads"]["nodes"] if not node["isResolved"])

    # `reviews` is returned in submission order, so the last entry per author
    # overwrites earlier ones, leaving each author's most recent review state.
    latest_state_by_author: dict[str, str] = {}
    for review in pr["reviews"]["nodes"]:
        login = review.get("author", {}).get("login")
        if login:
            latest_state_by_author[login] = review["state"]

    pending_rereview_logins = {
        node["requestedReviewer"]["login"]
        for node in pr["reviewRequests"]["nodes"]
        if node.get("requestedReviewer") and "login" in node["requestedReviewer"]
    }

    changes_requested_authors = [
        login for login, state in latest_state_by_author.items() if state == "CHANGES_REQUESTED"
    ]
    not_yet_rerequested = [
        login for login in changes_requested_authors if login not in pending_rereview_logins
    ]
    already_rerequested = [
        login for login in changes_requested_authors if login in pending_rereview_logins
    ]
    return unresolved, not_yet_rerequested, already_rerequested


def inspect_own_pr(repo: str, pr: dict) -> PrIssues:
    owner, name = repo.split("/", 1)
    number = pr["number"]
    issues = PrIssues(number=number, title=pr["title"], url=pr["url"], repo=repo)

    details = run_gh_json(
        [
            "pr",
            "view",
            str(number),
            "--repo",
            repo,
            "--json",
            "mergeable,reviewDecision,statusCheckRollup",
        ]
    )
    issues.conflicting = details["mergeable"] == "CONFLICTING"  # type: ignore[index]

    for check in details["statusCheckRollup"] or []:  # type: ignore[index]
        conclusion = check.get("conclusion")
        status = check.get("status")
        check_name = check.get("name", "unknown check")
        if conclusion in {"FAILURE", "ERROR", "CANCELLED", "TIMED_OUT"}:
            issues.failing_checks.append(check_name)
        elif status not in {"COMPLETED", None} or conclusion in {None, "NEUTRAL"}:
            if status == "IN_PROGRESS" or conclusion is None:
                issues.pending_checks.append(check_name)

    unresolved, not_yet_rerequested, already_rerequested = review_thread_and_request_status(
        owner, name, number
    )
    issues.unresolved_threads = unresolved

    # "Changes requested" is only actionable by you if a thread is still
    # unresolved, or a reviewer who asked for changes hasn't been
    # re-requested to look again. If every thread is resolved and a
    # re-review has already been requested from every such reviewer, the
    # ball is in their court, not yours -- surface it as informational only.
    if details["reviewDecision"] == "CHANGES_REQUESTED":  # type: ignore[index]
        if unresolved > 0 or not_yet_rerequested:
            issues.changes_requested = True
            issues.awaiting_rereview_from = not_yet_rerequested
        else:
            issues.rereview_pending_from = already_rerequested
    return issues


def gather_own_pr_issues(repo: str | None, all_repos: bool) -> list[PrIssues]:
    """Fetch the user's open PRs and inspect each for outstanding issues."""
    prs = own_open_prs(repo, all_repos)
    return [
        inspect_own_pr(pr.get("repository", {}).get("nameWithOwner") or repo, pr)
        for pr in prs
    ]


def print_own_pr_report(pr_issues: list[PrIssues], all_repos: bool) -> bool:
    print("=== Your open pull requests ===")
    if not pr_issues:
        print("  (none)")
        return False

    any_issue = False
    for issues in pr_issues:
        label = (
            f"#{issues.number} {issues.title} ({issues.repo})"
            if all_repos
            else f"#{issues.number} {issues.title}"
        )
        if not issues.has_issues:
            note = ""
            if issues.rereview_pending_from:
                reviewers = ", ".join(f"@{login}" for login in issues.rereview_pending_from)
                note = f" -- all comments resolved, re-review already requested from {reviewers}"
            print(f"  \u2713 {label}{note or ' -- clean'} ({issues.url})")
            continue

        any_issue = True
        print(f"  \u2717 {label} ({issues.url})")
        if issues.conflicting:
            print("      - merge conflict: needs rebase/resolve")
        if issues.changes_requested:
            if issues.awaiting_rereview_from:
                reviewers = ", ".join(f"@{login}" for login in issues.awaiting_rereview_from)
                print(f"      - changes requested, not yet re-requested from: {reviewers}")
            else:
                print("      - changes requested, and an unresolved review comment remains")
        if issues.failing_checks:
            print(f"      - failing checks: {', '.join(issues.failing_checks)}")
        if issues.pending_checks:
            print(f"      - pending checks: {', '.join(issues.pending_checks)}")
        if issues.unresolved_threads:
            print(f"      - unresolved review comments: {issues.unresolved_threads}")
    return any_issue


def is_release_bump_pr(pr: dict) -> bool:
    """Detect the automated weekly version-bump PR (see .github/workflows/version-bump.yml).

    These are safe to defer: a maintainer only needs to merge/act on them
    once a week, so they shouldn't count as actionable review work.
    """
    return pr.get("title", "").lower().startswith("chore(release): bump version")


def print_review_requests(prs: list[dict], repo: str | None) -> bool:
    print("\n=== Open PRs waiting on your review ===")
    if not prs:
        print("  (none)")
        return False

    any_actionable = False
    for pr in prs:
        pr_repo = pr.get("repository", {}).get("nameWithOwner", repo)
        author = pr.get("author", {}).get("login", "unknown")
        note = ""
        if is_release_bump_pr(pr):
            note = " (weekly release bump -- safe to defer)"
        else:
            any_actionable = True
        print(f"  - #{pr['number']} {pr['title']} by @{author} ({pr_repo}) {pr['url']}{note}")
    return any_actionable


def build_json_report(
    user: str,
    repo: str | None,
    pr_issues: list[PrIssues],
    review_requests: list[dict],
) -> dict:
    has_own_issues = any(issues.has_issues for issues in pr_issues)
    review_request_entries = [
        {
            "number": pr["number"],
            "title": pr["title"],
            "url": pr["url"],
            "repo": pr.get("repository", {}).get("nameWithOwner", repo),
            "author": pr.get("author", {}).get("login", "unknown"),
            "is_release_bump": is_release_bump_pr(pr),
        }
        for pr in review_requests
    ]
    has_actionable_review_requests = any(
        not entry["is_release_bump"] for entry in review_request_entries
    )
    return {
        "user": user,
        "repo": repo,
        "own_prs": [issues.to_json() for issues in pr_issues],
        "review_requests": review_request_entries,
        "own_pr_count": len(pr_issues),
        "review_request_count": len(review_request_entries),
        "total_pr_count": len(pr_issues) + len(review_request_entries),
        "has_own_issues": has_own_issues,
        "has_review_requests": bool(review_requests),
        "has_actionable_review_requests": has_actionable_review_requests,
        "clean": not has_own_issues and not has_actionable_review_requests,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo",
        help="OWNER/REPO to check (defaults to the current git repository)",
    )
    parser.add_argument(
        "--all-repos",
        action="store_true",
        help="Search across every repository you can see, not just --repo",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of the human-readable report",
    )
    args = parser.parse_args()

    repo = args.repo or (None if args.all_repos else current_repo())
    user = current_user()

    pr_issues = gather_own_pr_issues(repo, args.all_repos)
    review_requests = prs_needing_review(repo, args.all_repos)

    if args.json:
        report = build_json_report(user, repo, pr_issues, review_requests)
        print(json.dumps(report, indent=2))
        return 0 if report["clean"] else 1

    print(f"Checking outstanding GitHub work for @{user}" + (f" in {repo}" if repo else " across all repos"))
    total_prs = len(pr_issues) + len(review_requests)
    print(f"Total of {total_prs} PR(s): {len(pr_issues)} yours, {len(review_requests)} awaiting your review")
    print()

    has_own_issues = print_own_pr_report(pr_issues, args.all_repos)
    has_actionable_review_requests = print_review_requests(review_requests, repo)

    if not has_own_issues and not has_actionable_review_requests:
        print("\nNothing outstanding. You're all caught up!")
        return 0

    print("\nAction needed on the items marked above.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
