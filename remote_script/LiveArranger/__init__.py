"""LiveArranger Remote Script.

Runs inside Ableton Live. Exposes a small JSON-over-TCP command server on
127.0.0.1:11020 that the external `arranger` Python package talks to.
"""

try:
    from .manager import Manager
except ImportError:  # allow importing outside Live (tests)
    Manager = None


def create_instance(c_instance):
    return Manager(c_instance)
