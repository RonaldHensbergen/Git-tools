#!/usr/bin/env bash

set -euo pipefail

REMOTE="${1:-origin}"
CURRENT_BRANCH="$(git branch --show-current)"

# Make sure this is a Git repository
git rev-parse --is-inside-work-tree >/dev/null

echo "Fetching and pruning '$REMOTE'..."
git fetch "$REMOTE" --prune

# Get the names of branches that exist on the remote
mapfile -t REMOTE_BRANCHES < <(
  git for-each-ref \
    --format='%(refname:strip=3)' \
    "refs/remotes/$REMOTE" |
  grep -v "^HEAD$"
)

branch_exists_remotely() {
  local branch="$1"

  for remote_branch in "${REMOTE_BRANCHES[@]}"; do
    if [[ "$branch" == "$remote_branch" ]]; then
      return 0
    fi
  done

  return 1
}

while IFS= read -r local_branch; do
  # Never delete the currently checked-out branch
  if [[ "$local_branch" == "$CURRENT_BRANCH" ]]; then
    continue
  fi

  if ! branch_exists_remotely "$local_branch"; then
    read -r -p "Delete local branch '$local_branch'? [y/N] " answer

    if [[ "$answer" =~ ^[Yy]$ ]]; then
      git branch -d "$local_branch" ||
        git branch -D "$local_branch"
    fi
  fi
done < <(git for-each-ref --format='%(refname:strip=2)' refs/heads)
