"""In-memory stand-ins for Microsoft Graph, Claude and CAPE."""

import itertools
import json
from types import SimpleNamespace

from phishguard.emailparse import parse_email


class FakeGraph:
    def __init__(self, mailboxes):
        self.boxes = {mb: {} for mb in mailboxes}
        self.sent = []
        self._ids = itertools.count(1)
        self.delta_calls = 0

    def deliver(self, mailbox, raw):
        gid = f"m{next(self._ids)}"
        self.boxes[mailbox][gid] = {"raw": raw, "folder": "inbox", "categories": [],
                                    "imid": parse_email(raw).message_id, "new": True}
        return gid

    def mailboxes(self):
        return sorted(self.boxes)

    def inbox_delta(self, mailbox, delta_link):
        self.delta_calls += 1
        new = [gid for gid, m in self.boxes[mailbox].items() if m["new"] and m["folder"] == "inbox"]
        for gid in new:
            self.boxes[mailbox][gid]["new"] = False
        return new, f"delta-{mailbox}-{self.delta_calls}"

    def get_mime(self, mailbox, gid):
        return self.boxes[mailbox][gid]["raw"]

    def quarantine_folder(self, mailbox, name):
        return f"quarantine-{mailbox}"

    def move(self, mailbox, gid, destination):
        msg = self.boxes[mailbox].pop(gid)
        msg["folder"] = destination
        new_id = f"m{next(self._ids)}"
        self.boxes[mailbox][new_id] = msg
        return new_id

    def permanent_delete(self, mailbox, gid):
        del self.boxes[mailbox][gid]

    def tag(self, mailbox, gid, category):
        self.boxes[mailbox][gid]["categories"] = [category]

    def find_by_internet_message_id(self, mailbox, imid):
        return [{"id": gid, "parentFolderId": m["folder"]} for gid, m in self.boxes[mailbox].items() if m["imid"] == imid]

    def send_mail(self, sender, to, subject, html):
        self.sent.append({"from": sender, "to": to, "subject": subject, "html": html})

    def folder_of(self, mailbox, imid):
        return [m["folder"] for m in self.boxes[mailbox].values() if m["imid"] == imid]


class FakeClaude:
    """Mimics client.beta.messages.create with a scripted verdict."""

    def __init__(self, verdict=None, stop_reason="end_turn"):
        self.verdict = verdict
        self.stop_reason = stop_reason
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        text = json.dumps(self.verdict) if self.verdict else ""
        return SimpleNamespace(stop_reason=self.stop_reason, stop_details=SimpleNamespace(category="cyber"),
                               model=kwargs["model"], content=[SimpleNamespace(type="text", text=text)])


class FakeCape:
    def __init__(self, malscore):
        self.malscore = malscore
        self.submitted = []

    def submit(self, filename, data):
        self.submitted.append(filename)
        return str(len(self.submitted))

    def result(self, task_id):
        return "done", self.malscore, {"signatures": ["Creates a process from Office"], "family": "Emotet"}


class FakeDNS:
    def __init__(self, txt=None, a=None, mx=None):
        self._txt, self._a, self._mx = txt or {}, a or {}, mx or {}

    def txt(self, name):
        return self._txt.get(name, [])

    def a(self, name):
        return self._a.get(name, [])

    def mx(self, name):
        return self._mx.get(name, [])
