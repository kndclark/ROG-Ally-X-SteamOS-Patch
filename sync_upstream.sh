#!/bin/bash
# sync_upstream.sh
# Safely pulls and merges upstream changes for hid-asus.c from 'nero/for-next'
# using a temporary branch for normalization.

set -e

# Detect the active branch to return to
ACTIVE_BRANCH=$(git symbolic-ref --short HEAD)
echo "Active branch detected: $ACTIVE_BRANCH"

# Check for uncommitted changes in hid-asus.c specifically
if [ -n "$(git status --porcelain hid-asus.c)" ]; then
    echo "Error: You have uncommitted changes in hid-asus.c."
    echo "Please commit, stash, or discard changes to hid-asus.c before running this script."
    exit 1
fi

echo "Fetching latest changes from 'nero' remote..."
git fetch nero

# Safely delete any pre-existing temp-upstream-sync branch
if git show-ref --quiet refs/heads/temp-upstream-sync; then
    echo "Deleting existing 'temp-upstream-sync' branch..."
    git branch -D temp-upstream-sync
fi

# Use the last clean upstream common ancestor commit as the base for the temp branch
# to prevent Git from performing a fast-forward merge that discards your custom LED changes.
BASE_COMMIT="7267f5623efb3314d752c447fad1a3924cc2b3cc"
echo "Creating temporary branch 'temp-upstream-sync' from base commit $BASE_COMMIT..."
git checkout -b temp-upstream-sync "$BASE_COMMIT"

echo "Checking out hid-asus.c from nero/for-next..."
git checkout nero/for-next -- drivers/hid/hid-asus.c

echo "Normalizing hid-asus.c to repository root..."
mv drivers/hid/hid-asus.c ./hid-asus.c
rm -rf drivers/

git add hid-asus.c
git commit -m "Normalize upstream hid-asus.c to root for merging"

echo "Switching back to active branch '$ACTIVE_BRANCH'..."
git checkout "$ACTIVE_BRANCH"

echo "Merging temporary branch 'temp-upstream-sync'..."
# Disable set -e temporarily because git merge returns non-zero when conflicts exist
set +e
git merge temp-upstream-sync -m "Merge upstream changes from nero/for-next"
MERGE_STATUS=$?
set -e

# Delete the temporary branch
echo "Cleaning up temporary branch..."
git branch -D temp-upstream-sync

if [ $MERGE_STATUS -eq 0 ]; then
    echo "Success! Upstream changes merged cleanly."
else
    echo "Merge finished with conflicts (this is expected when both branches modify adjacent code)."
    echo "Please resolve conflict markers in hid-asus.c (or open your merge editor), then commit the merge."
fi
