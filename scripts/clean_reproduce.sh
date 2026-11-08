#!/usr/bin/env bash
# Clean-environment reproduction: copy the pack (excluding outputs,
# .git, caches, and the local submission text) to a fresh directory,
# run the full suite there, copy its logs back as outputs/run2-clean,
# and diff run1 against run2-clean. The two runs must be byte-identical.
set -u
cd "$(dirname "$0")/.." || exit 1

CLEAN_DIR="${CLEAN_DIR:-/tmp/atrf-clean-repro}"
rm -rf "$CLEAN_DIR"
mkdir -p "$CLEAN_DIR"
tar --exclude='./outputs' --exclude='./.git' --exclude='__pycache__' \
    --exclude='./submission-text-final.txt' --exclude='./SHA256SUMS' \
    -cf - . | (cd "$CLEAN_DIR" && tar xf -)
echo "clean copy: $CLEAN_DIR"

(cd "$CLEAN_DIR" && bash scripts/run_all.sh outputs/run1) > outputs/run2-clean.tmp.log 2>&1
clean_status=$?
mkdir -p outputs/run2-clean
cp -r "$CLEAN_DIR/outputs/run1/." outputs/run2-clean/
mv outputs/run2-clean.tmp.log outputs/run2-clean/clean-reproduce.log

echo "clean run exit status: $clean_status"
if diff -r --exclude=clean-reproduce.log outputs/run1 outputs/run2-clean > outputs/clean-reproduce-diff.txt 2>&1; then
  echo "clean reproduction: MATCH (run1 and run2-clean are byte-identical)"
  printf 'clean reproduction: MATCH (run1 and run2-clean are byte-identical)\n' >> outputs/run2-clean/clean-reproduce.log
  exit 0
else
  echo "clean reproduction: MISMATCH (see outputs/clean-reproduce-diff.txt)"
  printf 'clean reproduction: MISMATCH\n' >> outputs/run2-clean/clean-reproduce.log
  exit 1
fi
