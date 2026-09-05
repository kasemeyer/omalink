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

from dataclasses import dataclass, field

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, GObject

SERVICE = "org.kde.kdeconnect"
DAEMON_PATH = "/modules/kdeconnect"
DAEMON_IFACE = "org.kde.kdeconnect.daemon"
DEVICE_IFACE = "org.kde.kdeconnect.device"
BATTERY_IFACE = "org.kde.kdeconnect.device.battery"
SMS_IFACE = "org.kde.kdeconnect.device.sms"
CONVERSATIONS_IFACE = "org.kde.kdeconnect.device.conversations"
NOTIFICATIONS_IFACE = "org.kde.kdeconnect.device.notifications"
NOTIFICATION_IFACE = "org.kde.kdeconnect.device.notifications.notification"
TELEPHONY_IFACE = "org.kde.kdeconnect.device.telephony"

MESSAGE_RECEIVED = 1
MESSAGE_SENT = 2


@dataclass
class Message:
    body: str
    addresses: list
    date: int  # epoch ms
    type: int
    thread_id: int
    uid: int
    has_attachments: bool

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
            has_attachments=bool(attachments),
        )


@dataclass
class Notification:
    nid: str
    app_name: str
    title: str
    text: str
    dismissable: bool
    icon_path: str = ""


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
        "notifications-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "call-event": (GObject.SignalFlags.RUN_FIRST, None, (str, str, str)),
    }

    def __init__(self):
        super().__init__()
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.daemon = _proxy(self.bus, DAEMON_PATH, DAEMON_IFACE)
        self.daemon.connect("g-signal", self._on_daemon_signal)

        self.device_id = None
        self.device_path = None
        self.device = None
        self.battery = None
        self.sms = None
        self.notifications = None
        self.conversations: dict[int, Conversation] = {}
        self._attach_first_device()

    # -- device lifecycle -------------------------------------------------

    def _attach_first_device(self):
        ids = self.daemon.call_sync(
            "devices", GLib.Variant("(bb)", (False, True)), Gio.DBusCallFlags.NONE, -1, None
        ).unpack()[0]
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
                "activeConversations", None, Gio.DBusCallFlags.NONE, -1, None
            ).unpack()[0]
        except GLib.Error:
            cached = []
        for fields in cached:
            self._store(Message.from_tuple(fields))
        self.emit("conversations-loaded")
        self.sms.call(
            "requestAllConversations", None, Gio.DBusCallFlags.NONE, -1, None, None
        )

    def request_conversation(self, thread_id, start=0, end=50):
        if self.convs_proxy:
            self.convs_proxy.call(
                "requestConversation",
                GLib.Variant("(xii)", (thread_id, start, end)),
                Gio.DBusCallFlags.NONE, -1, None, None,
            )

    def reply_to_conversation(self, thread_id, text):
        self.convs_proxy.call(
            "replyToConversation",
            GLib.Variant("(xsav)", (thread_id, text, [])),
            Gio.DBusCallFlags.NONE, -1, None, None,
        )

    def send_new_sms(self, addresses, text):
        addr_variants = [GLib.Variant("(s)", (a,)) for a in addresses]
        self.convs_proxy.call(
            "sendWithoutConversation",
            GLib.Variant("(avsav)", (addr_variants, text, [])),
            Gio.DBusCallFlags.NONE, -1, None, None,
        )

    def _store(self, msg):
        conv = self.conversations.setdefault(msg.thread_id, Conversation(msg.thread_id))
        conv.messages[msg.uid] = msg
        if msg.addresses:
            conv.addresses = msg.addresses
        return conv

    def _on_conversation_signal(self, bus, sender, path, iface, signal, params):
        if signal not in ("conversationCreated", "conversationUpdated"):
            return
        msg = Message.from_variant(params.get_child_value(0).get_variant())
        self._store(msg)
        self.emit("message", msg)

    # -- notifications ----------------------------------------------------

    def active_notifications(self):
        if not self.notifications:
            return []
        try:
            ids = self.notifications.call_sync(
                "activeNotifications", None, Gio.DBusCallFlags.NONE, -1, None
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
                )
            )
        return items

    def dismiss_notification(self, nid):
        try:
            p = _proxy(self.bus, f"{self.device_path}/notifications/{nid}", NOTIFICATION_IFACE)
            p.call("dismiss", None, Gio.DBusCallFlags.NONE, -1, None, None)
        except GLib.Error:
            pass

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
