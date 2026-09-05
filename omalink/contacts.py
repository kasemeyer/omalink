"""Resolve phone numbers to names from the KDE Connect contacts plugin.

The contacts plugin syncs the phone's address book as vCard 2.1 files to
~/.local/share/kpeoplevcard/kdeconnect-<device-id>/*.vcf. Matching is done
on the last 10 digits so formatting differences (+1, dashes, spaces) don't
matter.
"""

import os
import quopri
import re

VCARD_ROOT = os.path.expanduser("~/.local/share/kpeoplevcard")

_TEL_RE = re.compile(r"^TEL[;:]", re.IGNORECASE)
_FN_RE = re.compile(r"^FN([;][^:]*)?:(.*)$", re.IGNORECASE)


def _normalize(number):
    digits = re.sub(r"\D", "", number)
    return digits[-10:] if len(digits) > 10 else digits


def _decode_line(prefix, line):
    value = line.split(":", 1)[1].strip()
    if "QUOTED-PRINTABLE" in prefix.upper():
        try:
            value = quopri.decodestring(value).decode("utf-8", "replace")
        except Exception:
            pass
    return value


class ContactBook:
    def __init__(self, device_id=None):
        self._by_number = {}
        self.reload(device_id)

    def reload(self, device_id=None):
        self._by_number.clear()
        if not os.path.isdir(VCARD_ROOT):
            return
        dirs = (
            [os.path.join(VCARD_ROOT, f"kdeconnect-{device_id}")]
            if device_id
            else [os.path.join(VCARD_ROOT, d) for d in os.listdir(VCARD_ROOT)]
        )
        for d in dirs:
            if not os.path.isdir(d):
                continue
            for fname in os.listdir(d):
                if fname.endswith(".vcf"):
                    self._parse_vcard(os.path.join(d, fname))

    def _parse_vcard(self, path):
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                lines = f.read().splitlines()
        except OSError:
            return
        name = None
        numbers = []
        for line in lines:
            m = _FN_RE.match(line)
            if m:
                name = _decode_line(line.split(":", 1)[0], line)
            elif _TEL_RE.match(line):
                numbers.append(_decode_line(line.split(":", 1)[0], line))
        if name:
            for n in numbers:
                key = _normalize(n)
                if key:
                    self._by_number[key] = name

    def lookup(self, number):
        return self._by_number.get(_normalize(number))

    def display(self, addresses):
        """Human-readable name for one or more raw addresses."""
        names = []
        for a in addresses:
            name = self.lookup(a) or a
            if name not in names:
                names.append(name)
        return ", ".join(names) if names else "Unknown"

    def initials(self, addresses):
        name = self.display(addresses)
        parts = [p for p in re.split(r"\s+", name) if p and p[0].isalpha()]
        if len(parts) >= 2:
            return (parts[0][0] + parts[1][0]).upper()
        if parts:
            return parts[0][0].upper()
        return "#"
