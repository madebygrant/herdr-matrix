#!/usr/bin/env bash
# Usage: scripts/release.sh <patch|minor|major|current> [--dry-run]
# "current" tags the version already in herdr-plugin.toml without bumping it.
set -euo pipefail

kind="${1:-}"
dry=0
[ "${2:-}" = "--dry-run" ] && dry=1
case "$kind" in
  patch | minor | major | current) ;;
  *) sed -n '2,3p' "$0" | sed 's/^# //' >&2; exit 2 ;;
esac

cd "$(git rev-parse --show-toplevel)"
manifest=herdr-plugin.toml

die() { echo "release: $*" >&2; exit 1; }
run() { if [ "$dry" = 1 ]; then echo "+ $*"; else "$@"; fi; }

git rev-parse --verify -q HEAD >/dev/null || die "no commits yet, make the first commit first"
[ "$(git symbolic-ref --short HEAD)" = main ] || die "not on main"
[ -z "$(git status --porcelain)" ] || die "working tree is not clean"
git fetch -q origin main 2>/dev/null || true
if git rev-parse -q --verify origin/main >/dev/null; then
  [ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ] || die "main is not in sync with origin/main"
fi

current="$(sed -n 's/^version = "\(.*\)"$/\1/p' "$manifest" | head -1)"
[[ "$current" =~ ^([0-9]+)\.([0-9]+)\.([0-9]+)$ ]] || die "cannot read version from $manifest"
major="${BASH_REMATCH[1]}"; minor="${BASH_REMATCH[2]}"; patch="${BASH_REMATCH[3]}"

case "$kind" in
  patch) next="$major.$minor.$((patch + 1))" ;;
  minor) next="$major.$((minor + 1)).0" ;;
  major) next="$((major + 1)).0.0" ;;
  current) next="$current" ;;
esac
tag="v$next"
git rev-parse -q --verify "refs/tags/$tag" >/dev/null && die "tag $tag already exists"

echo "release: $current -> $next ($tag)"

if [ "$next" != "$current" ]; then
  if [ "$dry" = 1 ]; then
    echo "+ set version = \"$next\" in $manifest"
  else
    sed "s/^version = \".*\"$/version = \"$next\"/" "$manifest" > "$manifest.tmp" && mv "$manifest.tmp" "$manifest"
  fi
  run git add "$manifest"
  run git commit -m "chore: release $tag"
fi

run git tag -a "$tag" -m "$tag"
run git push --atomic origin main "$tag"

if command -v gh >/dev/null 2>&1; then
  run gh release create "$tag" --title "$tag" --generate-notes
else
  echo "release: gh not found, skipped the GitHub release"
fi
