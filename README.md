# Omalink

A phone hub for [Omarchy](https://omarchy.org), modeled on Microsoft Phone Link.
Messages, phone notifications, and call events for your Android phone — native
GTK4/libadwaita on Wayland, powered by the KDE Connect daemon (no Plasma
required).

## Requirements

- `kdeconnect` (daemon only — `kdeconnectd` runs headless)
- `gtk4`, `libadwaita`, `python-gobject` (all stock on Omarchy)
- KDE Connect app on the phone, paired, with SMS, notification, and contact
  permissions granted

## Run

```bash
bin/omalink
```

Optional install:

```bash
ln -sf "$PWD/bin/omalink" ~/.local/bin/omalink
cp data/omalink.desktop ~/.local/share/applications/
```

## Layout

- **Sidebar** — device name, connection state, battery, ring button, and the
  phone's active notifications with dismiss / clear-all.
- **Messages** — conversation list with contact names (resolved from the KDE
  Connect contacts sync), thread view with bubbles, composer to reply, and a
  new-message dialog.
- **Calls** — live incoming/missed call events. KDE Connect does not expose
  the call log or dialing, so this tab only shows events that arrive while
  the app is running.

## How it talks to the phone

Everything goes through `kdeconnectd`'s session D-Bus API
(`org.kde.kdeconnect`). Conversation history arrives as
`conversationCreated`/`conversationUpdated` signals on the device object
path under the `org.kde.kdeconnect.device.conversations` interface; sending
uses `sms.sendSms`. Contact names are parsed from the vCards the contacts
plugin syncs to `~/.local/share/kpeoplevcard/`.
