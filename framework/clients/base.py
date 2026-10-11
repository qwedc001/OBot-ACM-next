"""Reusable async facilities without NoneBot matcher or command behavior."""

from ..components import Component


class Client(Component):
    """Await I/O and offload blocking library calls instead of blocking the event loop."""
