#!/usr/bin/env bash
# Remove a dev install of Omalink. Leaves the repo itself untouched.
# Pass --purge to also delete user data (hidden-thread list).
set -euo pipefail

rm -f "$HOME/.local/bin/omalink" "$HOME/.local/bin/omalink-doctor" \
      "$HOME/.local/bin/omalink-setup-theme" && echo "✓ removed launchers from ~/.local/bin"
rm -f "$HOME/.local/share/applications/omalink.desktop" && echo "✓ removed desktop entry"
rm -f "$HOME/.config/omarchy/themed/omalink.css.tpl" && echo "✓ removed theme template"
rm -f "$HOME/.local/state/omarchy/current/theme/omalink.css" 2>/dev/null || true

if [[ "${1:-}" == "--purge" ]]; then
  rm -rf "$HOME/.config/omalink" && echo "✓ purged ~/.config/omalink (hidden threads)"
else
  echo "· kept ~/.config/omalink (pass --purge to remove hidden-thread list)"
fi

echo "Done. The kdeconnect pairing and phone-side app are untouched."
