"""Small size-bounded dict for short-lived UI state (pagination tokens, pending confirmations).

Several handlers kept per-message UI state in plain module-level dicts that were never
pruned, so every /player, /buy, /sell, /delp or /plstats invocation leaked an entry for
the lifetime of the process. This drop-in replacement evicts the oldest entries once
``max_items`` is exceeded. It is only used for non-critical UI state; economy and match
state stays in PostgreSQL.
"""
from __future__ import annotations

from collections import OrderedDict


class BoundedDict(OrderedDict):
    def __init__(self, max_items: int = 2000):
        super().__init__()
        self.max_items = max(1, int(max_items))

    def __setitem__(self, key, value):
        if key in self:
            self.move_to_end(key)
        super().__setitem__(key, value)
        while len(self) > self.max_items:
            self.popitem(last=False)
