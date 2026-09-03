#!/usr/bin/env bash
# Sync the fork's mirror branches and tags with upstream (maziggy/bambuddy).
#
#   scripts/sync-upstream.sh            # fast-forward main + dev, mirror tags
#   scripts/sync-upstream.sh --merge    # ...then merge main into master
#
# `main` and `dev` are mirrors and must stay fast-forwardable — never commit to
# them. Own work belongs on `master`. The same job runs nightly as the
# `Sync upstream` workflow; this script is for when you want the merge too.

set -euo pipefail

MERGE=false
[ "${1:-}" = "--merge" ] && MERGE=true

cd "$(git rev-parse --show-toplevel)"

if ! git remote get-url upstream >/dev/null 2>&1; then
    echo "Adding upstream remote"
    git remote add upstream https://github.com/maziggy/bambuddy.git
fi

if [ -n "$(git status --porcelain)" ]; then
    echo "Working tree is dirty — commit or stash first." >&2
    exit 1
fi

CURRENT="$(git rev-parse --abbrev-ref HEAD)"

echo "==> Fetching upstream"
git fetch upstream --tags --prune

for branch in main dev; do
    echo "==> Fast-forwarding $branch"
    if [ "$CURRENT" = "$branch" ]; then
        git merge --ff-only "upstream/$branch"
    else
        # update-ref only moves the branch when it is a true fast-forward,
        # so a mirror that somehow gained a commit fails loudly instead of
        # being silently rewritten.
        git fetch . "upstream/$branch:$branch"
    fi
done

echo "==> Pushing mirrors and tags to origin"
git push origin main dev
git push origin --tags

if [ "$MERGE" = true ]; then
    echo "==> Merging main into master"
    git checkout master
    git merge main
    echo "Review the merge, then: git push origin master"
else
    echo
    echo "Mirrors updated. To bring the release into your work branch:"
    echo "  git checkout master && git merge main"
fi
