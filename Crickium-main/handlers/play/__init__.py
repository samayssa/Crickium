from __future__ import annotations

print("play package loading...")

# Import order matters here: challenge -> pitch -> toss -> lineup -> live is
# the natural progression of the flow, and each earlier stage imports
# the "send_*" function of the next stage to advance to it. This
# import chain is what actually registers every /play command and
# callback with handlers/registry.py.
from . import challenge  # noqa: F401
from . import pitch  # noqa: F401
from . import toss  # noqa: F401
from . import lineup  # noqa: F401
from . import live  # noqa: F401

print("play package loaded (challenge, pitch, toss, lineup, live).")
