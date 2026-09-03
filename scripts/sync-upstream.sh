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

# docker-compose.yml is the one upstream file this fork rewrites wholesale (it
# is the local dev stack here). Without help, every upstream edit to it — about
# one a month — stops the merge with a conflict whose resolution is always
# "keep ours". The `ours` merge driver makes git do exactly that, silently.
#
# This lives in .git/info/attributes rather than .gitattributes on purpose:
# .gitattributes is an upstream file, so putting it there would trade one
# recurring conflict for another. The cost is that it is per-clone state, which
# is why this script re-establishes it on every run.
ensure_merge_ours() {
    git config merge.ours.driver true
    local attrs=".git/info/attributes"
    local rule="docker-compose.yml merge=ours"
    if [ ! -f "$attrs" ] || ! grep -qxF "$rule" "$attrs"; then
        mkdir -p "$(dirname "$attrs")"
        printf '%s\n' "$rule" >> "$attrs"
        echo "Installed the 'ours' merge driver for docker-compose.yml"
    fi
}
ensure_merge_ours

CURRENT="$(git rev-parse --abbrev-ref HEAD)"

echo "==> Fetching upstream"
git fetch upstream --tags --prune

OLD_MAIN="$(git rev-parse --verify --quiet main || true)"

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

# The 'ours' driver keeps our compose file without asking, which also means a
# real upstream improvement to it (a new env var, a port fix) arrives silently
# and is dropped. Name those commits so they can be ported by hand.
if [ -n "$OLD_MAIN" ]; then
    COMPOSE_CHANGES="$(git log --oneline "$OLD_MAIN..main" -- docker-compose.yml)"
    if [ -n "$COMPOSE_CHANGES" ]; then
        echo
        echo "!! Upstream changed docker-compose.yml — our version is kept, review by hand:"
        echo "$COMPOSE_CHANGES" | sed 's/^/     /'
        echo "     git diff master:docker-compose.yml main:docker-compose.yml"
    fi
fi

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
