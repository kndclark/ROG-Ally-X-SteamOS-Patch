#!/bin/bash
# sync_upstream.sh
# Safely pulls and merges upstream changes for hid-asus.c from 'nero/for-next'
# directly using a clean 3-way merge on the file. No branch switching, no temporary
# branches, and completely immune to Windows case-insensitive filesystem crashes.

set -e

# Check for uncommitted changes in hid-asus.c specifically
if [ -n "$(git status --porcelain hid-asus.c)" ]; then
    echo "Error: You have uncommitted changes in hid-asus.c."
    echo "Please commit, stash, or discard changes to hid-asus.c before running this script."
    exit 1
fi

echo "Fetching latest changes from 'nero' remote..."
git fetch nero

# 1. Extract the clean base version of the file from the last common upstream ancestor
# (this has triggers configuration but NO LED changes, ensuring Git treats your LED code as a local addition)
BASE_COMMIT="7267f5623efb3314d752c447fad1a3924cc2b3cc"
echo "Extracting clean base version from commit $BASE_COMMIT..."
git show "$BASE_COMMIT:drivers/hid/hid-asus.c" > .hid-asus.c.base

# 2. Extract Nero's latest upstream version
echo "Extracting latest upstream version..."
git show nero/for-next:drivers/hid/hid-asus.c > .hid-asus.c.upstream

# 3. Perform the in-place 3-way merge directly on hid-asus.c
echo "================================================================="
echo "Performing in-place 3-way merge directly on hid-asus.c..."
echo "================================================================="

# Disable set -e temporarily because git merge-file returns non-zero when conflicts exist
set +e
git merge-file hid-asus.c .hid-asus.c.base .hid-asus.c.upstream
MERGE_STATUS=$?
set -e

# 4. Cleanup temporary files
rm -f .hid-asus.c.base .hid-asus.c.upstream

if [ $MERGE_STATUS -eq 0 ]; then
    echo "Success! Upstream changes merged cleanly into hid-asus.c."
else
    echo ""
    echo "Merge finished with CONFLICTS (this is expected when both sides modify the same lines)."
    echo "Conflict markers have been inserted into 'hid-asus.c' in your active branch."
    echo "Please open 'hid-asus.c' in your merge editor (e.g. VS Code Merge Editor) to review and resolve the conflicts."
fi
