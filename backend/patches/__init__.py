"""OBot-owned upstream adapter patches; installed only by the bot entry point.

This package is intentionally outside framework and plugins. Each patch documents
the exact upstream target and its removal conditions. Never modify site-packages.
"""


def install():
    from .qq_trace import install_trace

    install_trace()
