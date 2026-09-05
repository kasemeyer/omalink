# Clean-room setup test

Goal: experience the full first-run setup a new user hits, with nothing
pre-configured. Run this when ready — it removes the working setup, so
do it when you can spare the phone connection for a bit.

## What "clean" means here

Three layers get torn down and rebuilt:

1. **Desktop app** (omalink) — launcher symlink, desktop entry, theme template
2. **Bar plugin** (omalink-bar) — the Omarchy shell plugin
3. **Backend** (kdeconnect pairing) — optional, the deepest reset

The phone-side KDE Connect app and its pairing are the one thing to be
careful with: re-pairing needs the phone in hand.

## Teardown

```bash
# 1. Desktop app
~/dev/omalink/uninstall.sh --purge

# 2. Bar plugin
omarchy plugin disable kc.omalink-bar
omarchy plugin remove kc.omalink-bar --yes

# 3. (optional, deepest) unpair, to test the pairing flow too
#    Re-pairing requires accepting the request on the phone.
#    kdeconnectd stays; only the pairing is dropped.
#    Skip this unless you specifically want to test first-pair UX.
# busctl --user call org.kde.kdeconnect \
#   /modules/kdeconnect/devices/<id> org.kde.kdeconnect.device unpair
```

Confirm gone: `which omalink` empty, `omarchy plugin list | grep omalink`
empty, phone icon out of the bar.

## Rebuild — the path a real user follows

The honest test is to follow the READMEs verbatim, noting every place
they're wrong or incomplete.

```bash
# Prerequisites a user must already have (note if missing):
#   kdeconnect gtk4 libadwaita python-gobject gdk-pixbuf2
#   optional: sshfs scrcpy android-tools libheif

# App
cd ~/dev/omalink && ./install.sh
omalink                         # first launch — does it find the device?

# Bar plugin
omarchy plugin add https://github.com/<you>/omalink-bar.git --enable --yes
#   (until pushed: cp -r ~/dev/omalink-bar ~/.config/omarchy/plugins/kc.omalink-bar
#    then: omarchy plugin enable kc.omalink-bar right)
```

## What to watch for (the actual findings)

- [ ] Does the app show a useful empty state when no device is paired,
      or just a blank window?
- [ ] First launch before contacts sync: do threads show raw numbers?
- [ ] `install.sh` on a machine where `~/.local/bin` isn't on PATH
- [ ] Missing optional deps: is each feature's failure message clear?
      (sshfs → Photos, scrcpy/adb → Mirror, libheif → HEIC)
- [ ] Bar plugin before the app is installed: does "Open Omalink" fail
      loudly or silently? (Currently silent — candidate fix: detect and
      toast.)
- [ ] Theme template on a fresh theme switch — avatars + surfaces recolor?
- [ ] Does the plugin land disabled (as Omarchy intends) and is that clear?

Record findings back here as a punch list; fix, then re-run.
