"""Async application operations called by NoneBot plugins and integrations."""

from ..components import Component


class Service(Component):
    """Own application workflow and domain decisions; delegate reusable I/O to clients."""
