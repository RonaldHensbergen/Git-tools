#!/usr/bin/env python3
"""Generate a Markdown delivery plan from GitHub issues, grouped by date.

Builds a chronological delivery plan for a repository using the authenticated
`gh` CLI. Issues are pulled from one or more milestones and/or an explicit
list of issue numbers, then grouped by a target date:

- A milestone's due date (GitHub's `due_on`/`dueOn` field), if set.
- An explicit override via `--due "Milestone Name=YYYY-MM-DD"`, for
  milestones that don't have a due date set on GitHub (the common case for
  repos that don't otherwise use milestone due dates).
- Otherwise the issue is placed in an "Unscheduled" section.

`--since`/`--until` filter dated groups to a date range; unscheduled items
are unaffected by the range (there's no date to compare) but can be dropped
entirely with `--no-unscheduled`.

Requires the GitHub CLI (`gh`) to be installed and authenticated
(`gh auth status`). Prints Markdown to stdout by default; pass `--output`
to write it to a file instead.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date, datetime

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
        raise SystemExit(f"`gh {' '.join(args)}` failed:\n{result.stderr.strip()}")
    return result.stdout


def run_gh_json(args: list[str]) -> object:
    """Run a `gh` command and parse its JSON stdout, or exit on failure."""
    return json.loads(run_gh(args) or "null")


def current_repo() -> str:
    return run_gh(
        ["repo", "view", "--json", "owner,name", "-q", '.owner.login + "/" + .name']
    ).strip()


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value[:10])
    except ValueError as exc:
        raise SystemExit(f"Invalid date {value!r}, expected YYYY-MM-DD") from exc


@dataclass
class Milestone:
    number: int
    title: str
    due_on: date | None


@dataclass
class Issue:
    number: int
    title: str
    url: str
    state: str
    assignees: list[str]
    labels: list[str]
    milestone_title: str | None

    @property
    def is_closed(self) -> bool:
        return self.state.upper() == "CLOSED"

    def checklist_line(self) -> str:
        box = "x" if self.is_closed else " "
        who = f" ({', '.join(self.assignees)})" if self.assignees else ""
        tags = f" [{', '.join(self.labels)}]" if self.labels else ""
        return f"- [{box}] #{self.number} {self.title}{who}{tags} ({self.url})"


def fetch_milestones(repo: str) -> list[Milestone]:
    data = run_gh_json(["api", f"repos/{repo}/milestones?state=all", "--paginate"])
    return [
        Milestone(
            number=m["number"],
            title=m["title"],
            due_on=parse_date(m["due_on"]) if m.get("due_on") else None,
        )
        for m in data  # type: ignore[union-attr]
    ]


def parse_issue_json(raw: dict) -> Issue:
    milestone = raw.get("milestone")
    return Issue(
        number=raw["number"],
        title=raw["title"],
        url=raw["url"],
        state=raw["state"],
        assignees=[a["login"] for a in raw.get("assignees", [])],
        labels=[label["name"] for label in raw.get("labels", [])],
        milestone_title=milestone["title"] if milestone else None,
    )


ISSUE_FIELDS = "number,title,url,state,assignees,labels,milestone"


def fetch_issues_for_milestone(repo: str, title: str, state: str) -> list[Issue]:
    raw = run_gh_json(
        [
            "issue",
            "list",
            "--repo",
            repo,
            "--milestone",
            title,
            "--state",
            state,
            "--json",
            ISSUE_FIELDS,
            "--limit",
            "500",
        ]
    )
    return [parse_issue_json(item) for item in raw]  # type: ignore[union-attr]


def fetch_issue(repo: str, number: int) -> Issue:
    raw = run_gh_json(
        ["issue", "view", str(number), "--repo", repo, "--json", ISSUE_FIELDS]
    )
    return parse_issue_json(raw)  # type: ignore[arg-type]


def gather_issues(
    repo: str, milestones: list[Milestone], explicit_numbers: list[int], state: str
) -> list[Issue]:
    issues: dict[int, Issue] = {}
    for milestone in milestones:
        for issue in fetch_issues_for_milestone(repo, milestone.title, state):
            issues[issue.number] = issue
    for number in explicit_numbers:
        if number not in issues:
            issues[number] = fetch_issue(repo, number)
    return list(issues.values())


def parse_due_overrides(raw_overrides: list[str]) -> dict[str, date]:
    overrides: dict[str, date] = {}
    for entry in raw_overrides:
        if "=" not in entry:
            raise SystemExit(f"Invalid --due {entry!r}, expected 'Milestone Name=YYYY-MM-DD'")
        title, _, raw_date = entry.partition("=")
        overrides[title.strip()] = parse_date(raw_date.strip())
    return overrides


@dataclass
class DateGroup:
    due_on: date | None
    milestones: dict[str, list[Issue]] = field(default_factory=dict)

    @property
    def sort_key(self) -> tuple[int, date]:
        # Unscheduled (None) sorts after every real date.
        return (1, date.max) if self.due_on is None else (0, self.due_on)

    @property
    def label(self) -> str:
        return "Unscheduled" if self.due_on is None else self.due_on.isoformat()

    @property
    def all_issues(self) -> list[Issue]:
        return [issue for issues in self.milestones.values() for issue in issues]


def build_date_groups(
    issues: list[Issue], milestone_due_dates: dict[str, date | None]
) -> dict[date | None, DateGroup]:
    groups: dict[date | None, DateGroup] = {}
    for issue in issues:
        due_on = milestone_due_dates.get(issue.milestone_title) if issue.milestone_title else None
        group = groups.setdefault(due_on, DateGroup(due_on=due_on))
        bucket_title = issue.milestone_title or "No milestone"
        group.milestones.setdefault(bucket_title, []).append(issue)
    return groups


def filter_groups_by_range(
    groups: dict[date | None, DateGroup],
    since: date | None,
    until: date | None,
    include_unscheduled: bool,
) -> list[DateGroup]:
    result = []
    for due_on, group in groups.items():
        if due_on is None:
            if include_unscheduled:
                result.append(group)
            continue
        if since is not None and due_on < since:
            continue
        if until is not None and due_on > until:
            continue
        result.append(group)
    return sorted(result, key=lambda g: g.sort_key)


def render_markdown(title: str, repo: str, groups: list[DateGroup]) -> str:
    lines = [f"# {title}", "", f"Repository: `{repo}`", f"Generated: {datetime.now().isoformat(timespec='seconds')}", ""]

    if not groups:
        lines.append("_No issues matched the given filters._")
        return "\n".join(lines) + "\n"

    lines.append("## Summary")
    lines.append("")
    lines.append("| Date | Milestone | Open | Closed | Total | % done |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for group in groups:
        for milestone_title, milestone_issues in sorted(group.milestones.items()):
            open_count = sum(1 for i in milestone_issues if not i.is_closed)
            closed_count = len(milestone_issues) - open_count
            total = len(milestone_issues)
            pct = round(100 * closed_count / total) if total else 0
            lines.append(
                f"| {group.label} | {milestone_title} | {open_count} | {closed_count} | {total} | {pct}% |"
            )
    lines.append("")

    for group in groups:
        heading = "Unscheduled" if group.due_on is None else group.due_on.isoformat()
        lines.append(f"## {heading}")
        lines.append("")
        for milestone_title, milestone_issues in sorted(group.milestones.items()):
            lines.append(f"### {milestone_title}")
            lines.append("")
            for issue in sorted(milestone_issues, key=lambda i: i.number):
                lines.append(issue.checklist_line())
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", help="OWNER/REPO (defaults to the current git repository)")
    parser.add_argument(
        "--milestone",
        action="append",
        default=[],
        metavar="TITLE",
        help="Restrict to this milestone (repeatable). Default: every milestone with due-date info or overrides.",
    )
    parser.add_argument(
        "--issue",
        action="append",
        default=[],
        type=int,
        metavar="N",
        help="Include this specific issue number regardless of milestone (repeatable)",
    )
    parser.add_argument(
        "--due",
        action="append",
        default=[],
        metavar="'Milestone Name=YYYY-MM-DD'",
        help="Assign/override a milestone's due date, for milestones without one set on GitHub (repeatable)",
    )
    parser.add_argument("--since", type=parse_date, help="Only include dated groups on/after this date (YYYY-MM-DD)")
    parser.add_argument("--until", type=parse_date, help="Only include dated groups on/before this date (YYYY-MM-DD)")
    parser.add_argument(
        "--state", choices=["open", "closed", "all"], default="open", help="Issue state to include (default: open)"
    )
    parser.add_argument(
        "--no-unscheduled",
        action="store_true",
        help="Drop issues that have no resolvable due date instead of listing them under 'Unscheduled'",
    )
    parser.add_argument("--title", default="Delivery Plan", help="Heading for the generated plan")
    parser.add_argument("--output", metavar="PATH", help="Write Markdown to this file instead of stdout")
    args = parser.parse_args()

    repo = args.repo or current_repo()
    overrides = parse_due_overrides(args.due)

    all_milestones = fetch_milestones(repo)
    milestone_due_dates = {m.title: (overrides.get(m.title, m.due_on)) for m in all_milestones}
    milestone_due_dates.update({title: due for title, due in overrides.items() if title not in milestone_due_dates})

    target_milestones = (
        [m for m in all_milestones if m.title in args.milestone] if args.milestone else all_milestones
    )
    if args.milestone:
        missing = set(args.milestone) - {m.title for m in target_milestones}
        if missing:
            raise SystemExit(f"Unknown milestone(s): {', '.join(sorted(missing))}")

    issues = gather_issues(repo, target_milestones, args.issue, args.state)
    groups = build_date_groups(issues, milestone_due_dates)
    ordered_groups = filter_groups_by_range(groups, args.since, args.until, include_unscheduled=not args.no_unscheduled)

    markdown = render_markdown(args.title, repo, ordered_groups)

    if args.output:
        with open(args.output, "w", encoding="utf-8", newline="\n") as f:
            f.write(markdown)
        print(f"Wrote delivery plan to {args.output}", file=sys.stderr)
    else:
        print(markdown, end="")

    return 0


if __name__ == "__main__":
    sys.exit(main())
