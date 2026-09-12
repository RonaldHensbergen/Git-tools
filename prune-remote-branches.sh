#!/usr/bin/env bash

set -euo pipefail

REMOTE="${1:-origin}"
REPO="$(gh repo view --json nameWithOwner --jq '.nameWithOwner')"
DEFAULT_BRANCH="$(gh repo view --json defaultBranchRef --jq '.defaultBranchRef.name')"

git fetch "$REMOTE" --prune

declare -A LOCAL_BRANCHES

while IFS= read -r branch; do
  LOCAL_BRANCHES["$branch"]=1
done < <(git for-each-ref --format='%(refname:strip=2)' refs/heads)

candidates=()

while IFS= read -r remote_ref; do
  branch="${remote_ref#"$REMOTE"/}"

  [[ "$branch" == "HEAD" ]] && continue
  [[ "$branch" == "$DEFAULT_BRANCH" ]] && continue

  has_local=false
  [[ -n "${LOCAL_BRANCHES[$branch]+x}" ]] && has_local=true

  has_pr=false
  if gh pr list \
      --repo "$REPO" \
      --head "$branch" \
      --state all \
      --limit 1 |
      grep -q .; then
    has_pr=true
  fi

  # Change && to || if you want either condition to qualify
  if [[ "$has_local" == false && "$has_pr" == false ]]; then
    candidates+=("$branch")
  fi
done < <(
  git for-each-ref \
    --format='%(refname:strip=2)' \
    "refs/remotes/$REMOTE"
)

if [[ ${#candidates[@]} -eq 0 ]]; then
  echo "No candidate branches found."
  exit 0
fi

echo "The following remote branches will be deleted:"
printf '  %s/%s\n' "$REMOTE" "${candidates[@]}"

echo
read -r -p "Delete all of these branches? [y/N] " answer

if [[ ! "$answer" =~ ^[Yy]$ ]]; then
  echo "Cancelled."
  exit 0
fi

for branch in "${candidates[@]}"; do
  echo "Deleting $REMOTE/$branch..."
  git push "$REMOTE" --delete "$branch"
done

