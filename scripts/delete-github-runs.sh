#!/usr/bin/env bash
# Delete GitHub Actions workflow runs (and their logs) created between two dates, in India time (IST, +05:30).
# Usage: ./scripts/delete-github-runs.sh [FROM] [TO]          list what would be deleted (nothing is removed)
#        ./scripts/delete-github-runs.sh [FROM] [TO] --yes    delete them
#   FROM / TO: YYYY-MM-DD, inclusive; default 2026-11-17 to 2026-11-19.
# Runs still queued or in progress are skipped (GitHub cannot delete them; cancel them first).
# Deleted runs cannot be restored. Needs gh, logged in with access to this repository.
set -euo pipefail

from="2026-11-17"
to="2026-11-19"
delete=false
positional=()
for arg in "$@"; do
  case "$arg" in
    --yes) delete=true ;;
    -h|--help) sed -n '2,7p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) positional+=("$arg") ;;
  esac
done
[ "${#positional[@]}" -ge 1 ] && from="${positional[0]}"
[ "${#positional[@]}" -ge 2 ] && to="${positional[1]}"
for d in "$from" "$to"; do
  case "$d" in
    [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]) ;;
    *) echo "Dates must be YYYY-MM-DD (got '$d')" >&2; exit 2 ;;
  esac
done

command -v gh >/dev/null || { echo "Install the GitHub CLI: https://cli.github.com" >&2; exit 1; }
gh auth status >/dev/null 2>&1 || { echo "Log in first: gh auth login" >&2; exit 1; }
repo="$(gh repo view --json nameWithOwner --jq .nameWithOwner)"
range="${from}T00:00:00+05:30..${to}T23:59:59+05:30"

list="$(mktemp)"
trap 'rm -f "$list"' EXIT
# id, status, created time, workflow name, title. --paginate walks every page.
gh api --paginate -X GET "repos/$repo/actions/runs" -f created="$range" -f per_page=100 \
  --jq '.workflow_runs[] | [.id, .status, .created_at, .name, .display_title] | @tsv' > "$list"

total="$(wc -l < "$list" | tr -d ' ')"
done_runs="$(awk -F'\t' '$2 == "completed"' "$list" | wc -l | tr -d ' ')"
active="$((total - done_runs))"
echo "Repository: $repo"
echo "Runs created $from to $to (IST): $total ($done_runs finished, $active still queued/running and skipped)"
[ "$total" -ge 1000 ] && echo "Note: GitHub returns at most 1000 runs per date filter; run the script again afterwards for the rest."
[ "$total" -eq 0 ] && exit 0
echo
awk -F'\t' '{printf "  %-12s %-11s %s  %s / %s\n", $1, $2, substr($3, 1, 16), $4, $5}' "$list" | head -n 40
[ "$total" -gt 40 ] && echo "  ... and $((total - 40)) more"

if [ "$delete" != true ]; then
  echo
  echo "Nothing deleted. To delete the $done_runs finished runs listed above:"
  echo "  $0 $from $to --yes"
  exit 0
fi

echo
deleted=0
failed=0
while IFS="$(printf '\t')" read -r id status _; do
  [ "$status" = completed ] || continue
  if gh api -X DELETE "repos/$repo/actions/runs/$id" >/dev/null 2>&1; then
    deleted=$((deleted + 1))
    printf '\r  deleted %d of %d' "$deleted" "$done_runs"
  else
    failed=$((failed + 1))
    echo; echo "  could not delete run $id" >&2
  fi
done < "$list"
echo
echo "Deleted $deleted run(s); $failed failed; $active still queued/running were left."
