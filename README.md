# Omalink

A phone hub for [Omarchy](https://omarchy.org), modeled on Microsoft Phone Link.
Messages, phone notifications, and call events for your Android phone — native
GTK4/libadwaita on Wayland, powered by the KDE Connect daemon (no Plasma
required).

## Requirements

- `kdeconnect` (daemon only — `kdeconnectd` runs headless)
- `gtk4`, `libadwaita`, `python-gobject`, `gdk-pixbuf2` (all stock on Omarchy)
- KDE Connect app on the phone, paired, with SMS, notification, and contact
  permissions granted

Optional, feature-gated (the app tells you when one is missing):

- `sshfs` — Photos tab (phone filesystem mount)
- `scrcpy` + `android-tools` — screen mirroring (guided wireless-ADB setup
  is built into the mirror button)
- `libheif` — HEIC attachments in the in-app viewer

A `PKGBUILD` capturing all of this ships in the repo (`makepkg -si`).

## Run

```bash
bin/omalink
```

Optional install:

```bash
./install.sh   # symlink onto PATH + desktop entry + Omarchy theme template
```

Reverse it with `./uninstall.sh` (add `--purge` to also drop the
hidden-thread list). The kdeconnect pairing is left untouched.

## Layout

- **Sidebar** — device name, connection state, battery, ring button, and the
  phone's active notifications with dismiss / clear-all.
- **Messages** — conversation list with contact names (resolved from the KDE
  Connect contacts sync), thread view with bubbles, composer to reply, and a
  new-message dialog.
- **Calls** — live incoming/missed call events. KDE Connect does not expose
  the call log or dialing, so this tab only shows events that arrive while
  the app is running.

## Roadmap

- [x] Adaptive layout for tiling: notification sidebar collapses to an
  overlay (pin/unpin via the header button), conversation list and thread
  collapse to back-button navigation when narrow
- [x] MMS attachments: incoming thumbnails auto-upgrade to real previews,
  in-app image viewer with save-as, cache-aware downloads; outgoing via
  the composer paperclip
- [x] Photos tab via the sftp plugin (mounts the phone, newest camera shots)
- [x] Media player card (`mprisremote` plugin)
- [x] Inline reply to app notifications (`notification.sendReply`)
- [x] Screen mirror button (spawns scrcpy when installed — `pacman -S scrcpy`,
  needs USB or wireless ADB debugging enabled on the phone)
- [x] Omarchy theme integration: `bin/omalink-setup-theme` installs a
  `themed/` template; the app follows theme switches live, and avatar
  colors come from the theme's ANSI palette
- [ ] Calls: dialer and call log (blocked — KDE Connect exposes neither)
- [ ] Decide: fork/extend the KDE Connect Android app to unlock
  phone-side features the protocol doesn't carry today — call log +
  dialing, notification tap-to-open actions, archive state, DND/volume
  toggles. Options, in rough order of preference:
  1. Upstream patches to kdeconnect-android (GPL, active project) with
     matching kdeconnectd plugins — most work per feature, benefits
     everyone, no maintenance burden of a fork
  2. A small companion Android app speaking the KDE Connect protocol
     with custom packet types (the protocol allows unknown packet
     types; omalink reads them via a custom kdeconnectd plugin or
     directly) — no fork, ships on our schedule
  3. Full fork of the Android app — fastest iteration, but Play Store
     publishing plus tracking upstream forever

## How it talks to the phone

Everything goes through `kdeconnectd`'s session D-Bus API
(`org.kde.kdeconnect`). Conversation history arrives as
`conversationCreated`/`conversationUpdated` signals on the device object
path under the `org.kde.kdeconnect.device.conversations` interface; sending
uses `sms.sendSms`. Contact names are parsed from the vCards the contacts
plugin syncs to `~/.local/share/kpeoplevcard/`.
