"""Client for kdeconnectd's session D-Bus API.

Verified against kdeconnect 26.08 by live introspection:

  service org.kde.kdeconnect
    /modules/kdeconnect                  org.kde.kdeconnect.daemon
    /modules/kdeconnect/devices/<id>     org.kde.kdeconnect.device
    .../<id>/battery                     org.kde.kdeconnect.device.battery
    .../<id>/sms                         org.kde.kdeconnect.device.sms
    .../<id>/notifications               org.kde.kdeconnect.device.notifications
    .../<id>/notifications/<nid>         ...notifications.notification
    .../<id>/telephony                   org.kde.kdeconnect.device.telephony

The conversations API lives on the *device path itself* under interface
org.kde.kdeconnect.device.conversations (there is no /conversations
object):

  activeConversations() -> av          one cached message per thread, sync
  requestConversation(x tid, i start, i end)
                                       history; replies stream back as
                                       conversationUpdated signals
  replyToConversation(x tid, s text, av attachments)
  sendWithoutConversation(av addresses, s text, av attachments)

Each message (in the av items and in conversationCreated /
conversationUpdated signal variants) is a 10-field struct:

  (event i, body s, addresses a(s), date x ms, type i, read i,
   thread_id x, uid i, sub_id x, attachments a(...))

type: 1 = received, 2 = sent.
"""

import json
import os
from dataclasses import dataclass, field

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, GObject

CACHE_PATH = os.path.expanduser("~/.local/state/omalink/conversations.json")

SERVICE = "org.kde.kdeconnect"
DAEMON_PATH = "/modules/kdeconnect"
DAEMON_IFACE = "org.kde.kdeconnect.daemon"
DEVICE_IFACE = "org.kde.kdeconnect.device"
BATTERY_IFACE = "org.kde.kdeconnect.device.battery"
SMS_IFACE = "org.kde.kdeconnect.device.sms"
CONVERSATIONS_IFACE = "org.kde.kdeconnect.device.conversations"
NOTIFICATIONS_IFACE = "org.kde.kdeconnect.device.notifications"
CONTACTS_IFACE = "org.kde.kdeconnect.device.contacts"
NOTIFICATION_IFACE = "org.kde.kdeconnect.device.notifications.notification"
TELEPHONY_IFACE = "org.kde.kdeconnect.device.telephony"
MPRIS_IFACE = "org.kde.kdeconnect.device.mprisremote"
SFTP_IFACE = "org.kde.kdeconnect.device.sftp"

# Blocking D-Bus calls fail fast rather than hang the UI (or, unguarded,
# crash) for the full 25s default when the daemon or phone is slow.
CALL_TIMEOUT_MS = 8000

MESSAGE_RECEIVED = 1
MESSAGE_SENT = 2


@dataclass
class Attachment:
    part_id: int
    mime_type: str
    thumbnail_b64: str  # base64-encoded PNG preview
    part_name: str  # unique identifier, e.g. "PART_1787372105585"


@dataclass
class Message:
    body: str
    addresses: list
    date: int  # epoch ms
    type: int
    thread_id: int
    uid: int
    attachments: list

    @property
    def has_attachments(self):
        return bool(self.attachments)

    @classmethod
    def from_variant(cls, variant):
        return cls.from_tuple(variant.unpack())

    @classmethod
    def from_tuple(cls, fields):
        event, body, addresses, date, mtype, read, thread_id, uid, sub_id, attachments = fields
        return cls(
            body=body,
            addresses=[a[0] for a in addresses],
            date=date,
            type=mtype,
            thread_id=thread_id,
            uid=uid,
            attachments=[Attachment(*a) for a in attachments],
        )


@dataclass
class Notification:
    nid: str
    app_name: str
    title: str
    text: str
    dismissable: bool
    icon_path: str = ""
    reply_id: str = ""  # non-empty when the notification supports inline reply


@dataclass
class Conversation:
    thread_id: int
    addresses: list = field(default_factory=list)
    messages: dict = field(default_factory=dict)  # uid -> Message

    @property
    def last_message(self):
        return max(self.messages.values(), key=lambda m: m.date, default=None)

    def sorted_messages(self):
        return sorted(self.messages.values(), key=lambda m: m.date)


def _proxy(bus, path, iface):
    return Gio.DBusProxy.new_sync(
        bus, Gio.DBusProxyFlags.NONE, None, SERVICE, path, iface, None
    )


class KdeConnect(GObject.Object):
    """Wraps the daemon plus the first paired device's plugins."""

    __gsignals__ = {
        "device-state": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "conversations-loaded": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "message": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "attachment-received": (GObject.SignalFlags.RUN_FIRST, None, (str, str)),
        "notifications-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "call-event": (GObject.SignalFlags.RUN_FIRST, None, (str, str, str)),
        "contacts-synced": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "media-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "sftp-mounted": (GObject.SignalFlags.RUN_FIRST, None, (bool, str)),
    }

    def __init__(self):
        super().__init__()
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.daemon = _proxy(self.bus, DAEMON_PATH, DAEMON_IFACE)
        self.daemon.connect("g-signal", self._on_daemon_signal)

        # All per-device proxies default to None so the app runs cleanly
        # with no phone attached (e.g. daemon slow at startup, or unpaired).
        # Every accessor/method below must tolerate these being None.
        self.device_id = None
        self.device_path = None
        self.device = None
        self.battery = None
        self.sms = None
        self.notifications = None
        self.convs_proxy = None
        self.media = None
        self.sftp = None
        self.contacts = None
        self.conversations: dict[int, Conversation] = {}
        self._refreshing = False
        self._refresh_announced = set()
        self._cache_save_pending = False
        self._load_cache()
        self._attach_first_device()

    # -- local cache ------------------------------------------------------
    # Persist conversations to disk so the app shows messages instantly on
    # the next open instead of waiting on a full daemon/phone re-sync. The
    # live sync still runs in the background and updates the cache.

    def _load_cache(self):
        try:
            with open(CACHE_PATH, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return
        for tid_s, c in data.get("threads", {}).items():
            try:
                tid = int(tid_s)
            except ValueError:
                continue
            conv = Conversation(tid, addresses=c.get("addresses", []))
            for m in c.get("messages", []):
                atts = [Attachment(*a) for a in m.get("attachments", [])]
                conv.messages[m["uid"]] = Message(
                    body=m.get("body", ""), addresses=m.get("addresses", []),
                    date=m.get("date", 0), type=m.get("type", 1),
                    thread_id=tid, uid=m["uid"], attachments=atts)
            if conv.messages:
                self.conversations[tid] = conv

    def schedule_cache_save(self):
        if self._cache_save_pending:
            return
        self._cache_save_pending = True
        GLib.timeout_add(3000, self._do_cache_save)

    def _do_cache_save(self):
        self._cache_save_pending = False
        # Cap per-thread history so the file stays bounded; drop attachment
        # thumbnails if the payload would get very large.
        threads = {}
        for tid, conv in self.conversations.items():
            msgs = conv.sorted_messages()[-100:]
            threads[str(tid)] = {
                "addresses": conv.addresses,
                "messages": [{
                    "body": m.body, "addresses": m.addresses, "date": m.date,
                    "type": m.type, "uid": m.uid,
                    "attachments": [[a.part_id, a.mime_type, a.thumbnail_b64, a.part_name]
                                    for a in m.attachments],
                } for m in msgs],
            }
        payload = {"version": 1, "threads": threads}
        text = json.dumps(payload)
        if len(text) > 20_000_000:  # too big — re-serialize without thumbnails
            for t in threads.values():
                for m in t["messages"]:
                    m["attachments"] = [[a[0], a[1], "", a[3]] for a in m["attachments"]]
            text = json.dumps({"version": 1, "threads": threads})
        try:
            os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
            tmp = CACHE_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(tmp, CACHE_PATH)
        except OSError:
            pass
        return False

    # -- device lifecycle -------------------------------------------------

    def _attach_first_device(self):
        # Prefer a reachable + paired device. Reinstalling the phone app
        # mints a new device id while the old pairing lingers as a stale
        # "paired but unreachable" entry, so picking the first *paired*
        # device can attach to the dead one. (onlyReachable, onlyPaired)
        #
        # Guarded: a slow/unresponsive daemon made this blocking call time
        # out and raised an unhandled GError that crashed the app at
        # startup. Now it fails fast (short timeout) and quietly — the app
        # opens with the empty state, and a later deviceAdded signal
        # re-attaches once the daemon responds.
        def query(reachable, paired):
            try:
                return self.daemon.call_sync(
                    "devices", GLib.Variant("(bb)", (reachable, paired)),
                    Gio.DBusCallFlags.NONE, CALL_TIMEOUT_MS, None,
                ).unpack()[0]
            except GLib.Error:
                return []

        ids = query(True, True) or query(False, True)
        if ids:
            self._attach_device(ids[0])

    def _attach_device(self, device_id):
        self.device_id = device_id
        self.device_path = f"{DAEMON_PATH}/devices/{device_id}"
        self.device = _proxy(self.bus, self.device_path, DEVICE_IFACE)
        self.battery = _proxy(self.bus, f"{self.device_path}/battery", BATTERY_IFACE)
        self.sms = _proxy(self.bus, f"{self.device_path}/sms", SMS_IFACE)
        self.convs_proxy = _proxy(self.bus, self.device_path, CONVERSATIONS_IFACE)
        self.notifications = _proxy(
            self.bus, f"{self.device_path}/notifications", NOTIFICATIONS_IFACE
        )
        self.media = _proxy(self.bus, f"{self.device_path}/mprisremote", MPRIS_IFACE)
        self.sftp = _proxy(self.bus, f"{self.device_path}/sftp", SFTP_IFACE)
        self.contacts = _proxy(self.bus, f"{self.device_path}/contacts", CONTACTS_IFACE)
        self.media.connect("g-properties-changed", lambda *a: self.emit("media-changed"))
        self.bus.signal_subscribe(
            SERVICE, MPRIS_IFACE, "propertiesChanged", f"{self.device_path}/mprisremote",
            None, Gio.DBusSignalFlags.NONE, lambda *a: self.emit("media-changed"),
        )

        for proxy in (self.device, self.battery):
            proxy.connect("g-properties-changed", lambda *a: self.emit("device-state"))

        self.bus.signal_subscribe(
            SERVICE, CONVERSATIONS_IFACE, None, self.device_path, None,
            Gio.DBusSignalFlags.NONE, self._on_conversation_signal,
        )
        self.bus.signal_subscribe(
            SERVICE, NOTIFICATIONS_IFACE, None, f"{self.device_path}/notifications", None,
            Gio.DBusSignalFlags.NONE, lambda *a: self.emit("notifications-changed"),
        )
        # Contacts sync — a fresh pair has no local vcards until requested,
        # so names show as raw numbers. Kick a sync and reload when it lands.
        self.bus.signal_subscribe(
            SERVICE, CONTACTS_IFACE, "localCacheSynchronized",
            f"{self.device_path}/contacts", None, Gio.DBusSignalFlags.NONE,
            lambda *a: self.emit("contacts-synced"),
        )
        self.contacts.call("synchronizeRemoteWithLocal", None,
                           Gio.DBusCallFlags.NONE, -1, None, None)
        self.bus.signal_subscribe(
            SERVICE, TELEPHONY_IFACE, "callReceived", f"{self.device_path}/telephony", None,
            Gio.DBusSignalFlags.NONE, self._on_call_signal,
        )
        self.emit("device-state")

    def _on_daemon_signal(self, proxy, sender, signal, params):
        if signal in ("deviceAdded", "deviceRemoved", "deviceVisibilityChanged"):
            if self.device_id is None:
                self._attach_first_device()
            self.emit("device-state")

    # -- device state -----------------------------------------------------

    @property
    def device_name(self):
        if not self.device:
            return None
        v = self.device.get_cached_property("name")
        return v.unpack() if v else None

    @property
    def is_reachable(self):
        if not self.device:
            return False
        v = self.device.get_cached_property("isReachable")
        return bool(v.unpack()) if v else False

    @property
    def reachable_address(self):
        if not self.device:
            return ""
        v = self.device.get_cached_property("reachableAddresses")
        addrs = v.unpack() if v else []
        return addrs[0] if addrs else ""

    @property
    def is_paired(self):
        if not self.device:
            return False
        v = self.device.get_cached_property("isPaired")
        return bool(v.unpack()) if v else False

    def unpair(self):
        if self.device:
            self.device.call("unpair", None, Gio.DBusCallFlags.NONE, -1, None, None)

    def request_pairing(self):
        if self.device:
            self.device.call("requestPairing", None, Gio.DBusCallFlags.NONE, -1, None, None)

    @property
    def battery_charge(self):
        if not self.battery:
            return -1
        v = self.battery.get_cached_property("charge")
        return v.unpack() if v else -1

    @property
    def battery_charging(self):
        if not self.battery:
            return False
        v = self.battery.get_cached_property("isCharging")
        return bool(v.unpack()) if v else False

    # -- SMS --------------------------------------------------------------

    def load_conversations(self):
        """Populate from the daemon's cache (one latest message per thread),
        then ask the phone to sync anything newer."""
        if not self.convs_proxy:
            return
        try:
            cached = self.convs_proxy.call_sync(
                "activeConversations", None, Gio.DBusCallFlags.NONE, CALL_TIMEOUT_MS, None
            ).unpack()[0]
        except GLib.Error:
            cached = []
        for fields in cached:
            self._store(Message.from_tuple(fields))
        self.emit("conversations-loaded")
        self.sms.call(
            "requestAllConversations", None, Gio.DBusCallFlags.NONE, -1, None, None
        )

    def refresh_conversations(self):
        """Re-sync against the phone's live thread list and prune threads
        the phone no longer has (archived/deleted).

        The daemon caches every thread it has ever seen and never prunes,
        so archived/deleted threads linger. But when the phone answers
        requestAllConversations it announces only threads that still exist,
        one conversationLoaded(thread_id, count) each. We collect those
        announced ids, then in _finalize drop any local thread not among
        them — without clearing first, so loaded per-thread history (and
        the on-disk cache) survives for the threads that remain.
        """
        if not self.sms or not self.convs_proxy:
            return
        self._refresh_announced = set()
        self._refreshing = True
        self.sms.call(
            "requestAllConversations", None, Gio.DBusCallFlags.NONE, -1, None, None
        )
        GLib.timeout_add(6000, self._finalize_refresh)

    def _finalize_refresh(self):
        self._refreshing = False
        # Drop threads the phone didn't announce (archived/deleted), but only
        # if the phone actually announced something — an empty set means the
        # sync failed and we must not wipe everything.
        if self._refresh_announced:
            for tid in [t for t in self.conversations
                        if t not in self._refresh_announced]:
                del self.conversations[tid]
        try:
            cached = self.convs_proxy.call_sync(
                "activeConversations", None, Gio.DBusCallFlags.NONE, CALL_TIMEOUT_MS, None
            ).unpack()[0]
        except GLib.Error:
            cached = []
        for fields in cached:
            msg = Message.from_tuple(fields)
            if msg.thread_id in self._refresh_announced:
                self._store(msg)
        self.schedule_cache_save()
        self.emit("conversations-loaded")
        return False

    def request_conversation(self, thread_id, start=0, end=50):
        if self.convs_proxy:
            self.convs_proxy.call(
                "requestConversation",
                GLib.Variant("(xii)", (thread_id, start, end)),
                Gio.DBusCallFlags.NONE, -1, None, None,
            )

    def reply_to_conversation(self, thread_id, text, attachment_paths=()):
        if not self.convs_proxy:
            return
        atts = [GLib.Variant("s", p) for p in attachment_paths]
        self.convs_proxy.call(
            "replyToConversation",
            GLib.Variant("(xsav)", (thread_id, text, atts)),
            Gio.DBusCallFlags.NONE, -1, None, None,
        )

    def send_new_sms(self, addresses, text, attachment_paths=()):
        if not self.convs_proxy:
            return
        addr_variants = [GLib.Variant("(s)", (a,)) for a in addresses]
        atts = [GLib.Variant("s", p) for p in attachment_paths]
        self.convs_proxy.call(
            "sendWithoutConversation",
            GLib.Variant("(avsav)", (addr_variants, text, atts)),
            Gio.DBusCallFlags.NONE, -1, None, None,
        )

    def _store(self, msg):
        conv = self.conversations.setdefault(msg.thread_id, Conversation(msg.thread_id))
        conv.messages[msg.uid] = msg
        if msg.addresses:
            conv.addresses = msg.addresses
        self.schedule_cache_save()
        return conv

    def _on_conversation_signal(self, bus, sender, path, iface, signal, params):
        if signal == "attachmentReceived":
            file_path, part_name = params.unpack()
            self.emit("attachment-received", file_path, part_name)
            return
        if signal == "conversationRemoved":
            self.conversations.pop(params.unpack()[0], None)
            self.schedule_cache_save()
            self.emit("conversations-loaded")
            return
        if signal == "conversationLoaded":
            if self._refreshing:
                self._refresh_announced.add(params.unpack()[0])
            return
        if signal not in ("conversationCreated", "conversationUpdated"):
            return
        msg = Message.from_variant(params.get_child_value(0).get_variant())
        self._store(msg)
        self.emit("message", msg)

    def request_attachment(self, part_id, part_name):
        if not self.convs_proxy:
            return
        self.convs_proxy.call(
            "requestAttachmentFile",
            GLib.Variant("(xs)", (part_id, part_name)),
            Gio.DBusCallFlags.NONE, -1, None, None,
        )

    # -- notifications ----------------------------------------------------

    def active_notifications(self):
        if not self.notifications:
            return []
        try:
            ids = self.notifications.call_sync(
                "activeNotifications", None, Gio.DBusCallFlags.NONE, CALL_TIMEOUT_MS, None
            ).unpack()[0]
        except GLib.Error:
            return []
        items = []
        for nid in ids:
            try:
                p = _proxy(
                    self.bus, f"{self.device_path}/notifications/{nid}", NOTIFICATION_IFACE
                )
            except GLib.Error:
                continue

            def prop(name, default=""):
                v = p.get_cached_property(name)
                return v.unpack() if v else default

            items.append(
                Notification(
                    nid=nid,
                    app_name=prop("appName"),
                    title=prop("title"),
                    text=prop("text"),
                    dismissable=bool(prop("dismissable", False)),
                    icon_path=prop("iconPath"),
                    reply_id=prop("replyId"),
                )
            )
        return items

    def dismiss_notification(self, nid):
        try:
            p = _proxy(self.bus, f"{self.device_path}/notifications/{nid}", NOTIFICATION_IFACE)
            p.call("dismiss", None, Gio.DBusCallFlags.NONE, -1, None, None)
        except GLib.Error:
            pass

    # -- media (mprisremote) ----------------------------------------------

    def _media_prop(self, name, default=""):
        if not self.media:
            return default
        v = self.media.get_cached_property(name)
        return v.unpack() if v else default

    @property
    def media_title(self):
        return self._media_prop("title")

    @property
    def media_artist(self):
        return self._media_prop("artist")

    @property
    def media_player(self):
        return self._media_prop("player")

    @property
    def media_is_playing(self):
        return bool(self._media_prop("isPlaying", False))

    def media_action(self, action):
        """action: Play, Pause, PlayPause, Next, Previous."""
        if not self.media:
            return
        self.media.call(
            "sendAction", GLib.Variant("(s)", (action,)),
            Gio.DBusCallFlags.NONE, -1, None, None,
        )

    # -- notifications: inline reply --------------------------------------

    def notification_reply(self, nid, text):
        try:
            p = _proxy(self.bus, f"{self.device_path}/notifications/{nid}", NOTIFICATION_IFACE)
            p.call("sendReply", GLib.Variant("(s)", (text,)),
                   Gio.DBusCallFlags.NONE, -1, None, None)
        except GLib.Error:
            pass

    # -- sftp (photos) ----------------------------------------------------

    def sftp_mount(self):
        """Mount the phone filesystem; emits sftp-mounted(ok, mount_point)."""
        if not self.sftp:
            self.emit("sftp-mounted", False, "")
            return

        def done(proxy, result):
            try:
                ok = proxy.call_finish(result).unpack()[0]
            except GLib.Error:
                ok = False
            mp = ""
            if ok:
                try:
                    mp = self.sftp.call_sync(
                        "mountPoint", None, Gio.DBusCallFlags.NONE, CALL_TIMEOUT_MS, None
                    ).unpack()[0]
                except GLib.Error:
                    ok = False
            self.emit("sftp-mounted", ok, mp)

        self.sftp.call("mountAndWait", None, Gio.DBusCallFlags.NONE, 30000, None, done)

    def sftp_mount_error(self):
        if not self.sftp:
            return ""
        try:
            return self.sftp.call_sync(
                "getMountError", None, Gio.DBusCallFlags.NONE, CALL_TIMEOUT_MS, None
            ).unpack()[0]
        except GLib.Error:
            return ""

    def sftp_directories(self):
        """Friendly name -> absolute path of browseable phone directories."""
        if not self.sftp:
            return {}
        try:
            return self.sftp.call_sync(
                "getDirectories", None, Gio.DBusCallFlags.NONE, CALL_TIMEOUT_MS, None
            ).unpack()[0]
        except GLib.Error:
            return {}

    # -- telephony --------------------------------------------------------

    def _on_call_signal(self, bus, sender, path, iface, signal, params):
        event, number, contact_name = params.unpack()
        self.emit("call-event", event, number, contact_name)

    def ring_phone(self):
        try:
            p = _proxy(
                self.bus, f"{self.device_path}/findmyphone",
                "org.kde.kdeconnect.device.findmyphone",
            )
            p.call("ring", None, Gio.DBusCallFlags.NONE, -1, None, None)
        except GLib.Error:
            pass
