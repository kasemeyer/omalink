# Maintainer: Kasey Prichard
pkgname=omalink
pkgver=0.1.0
pkgrel=1
pkgdesc="Phone hub for Omarchy — messages, notifications, photos, and calls via KDE Connect"
arch=('any')
url="https://github.com/kasemeyer/omalink"
license=('MIT')
depends=(
  'python'
  'python-gobject'      # GTK bindings
  'gtk4'
  'libadwaita'
  'kdeconnect'          # kdeconnectd daemon — the entire backend
  'gdk-pixbuf2'         # attachment/photo previews
)
optdepends=(
  'sshfs: Photos tab (mounts the phone via the KDE Connect sftp plugin)'
  'scrcpy: screen mirroring'
  'android-tools: adb, required by scrcpy wireless setup'
  'avahi: auto-discover the wireless-debugging port (Arch adb lacks mdns)'
  'libheif: decode HEIC attachments in the image viewer'
)
source=()
sha256sums=()

package() {
  cd "$startdir"
  local site="$pkgdir/usr/lib/$pkgname"
  install -d "$site"
  cp -r omalink "$site/"
  install -Dm755 /dev/stdin "$pkgdir/usr/bin/omalink" <<'EOF'
#!/usr/bin/env python3
import sys
sys.path.insert(0, "/usr/lib/omalink")
from omalink.app import main
sys.exit(main())
EOF
  install -Dm644 data/omalink.desktop "$pkgdir/usr/share/applications/omalink.desktop"
  install -Dm644 data/omalink.css.tpl "$pkgdir/usr/share/omalink-app/omalink.css.tpl"
  install -Dm755 bin/omalink-setup-theme "$pkgdir/usr/bin/omalink-setup-theme"
  install -Dm644 LICENSE "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
}
