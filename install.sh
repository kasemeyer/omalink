#!/usr/bin/env bash
# Dev install for Omalink: symlink the launcher onto PATH, install the
# desktop entry, and register the Omarchy theme template. Idempotent.
# For a real system package use the PKGBUILD instead.
set -euo pipefail

repo="$(cd "$(dirname "$0")" && pwd)"

# 1. launchers on PATH
mkdir -p "$HOME/.local/bin"
ln -sf "$repo/bin/omalink" "$HOME/.local/bin/omalink"
ln -sf "$repo/bin/omalink-doctor" "$HOME/.local/bin/omalink-doctor"
ln -sf "$repo/bin/omalink-setup-theme" "$HOME/.local/bin/omalink-setup-theme"
echo "✓ omalink, omalink-doctor → ~/.local/bin/"

case ":$PATH:" in
  *":$HOME/.local/bin:"*) ;;
  *) echo "  ⚠ ~/.local/bin is not on your PATH — add it to your shell profile." ;;
esac

# 2. desktop entry (app launcher / walker)
mkdir -p "$HOME/.local/share/applications"
cp "$repo/data/omalink.desktop" "$HOME/.local/share/applications/omalink.desktop"
echo "✓ desktop entry installed"

# 3. Omarchy theme template (optional — only if Omarchy theming is present)
if command -v omarchy-theme-set >/dev/null 2>&1; then
  "$repo/bin/omalink-setup-theme"
else
  echo "· Omarchy theming not detected; skipping theme template"
fi

echo
echo "Running the setup check…"
echo
"$repo/bin/omalink-doctor" "$@"
