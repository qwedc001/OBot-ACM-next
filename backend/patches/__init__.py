"""OBot-owned upstream adapter patches; installed only by the bot entry point.

This package is intentionally outside framework and plugins. Each patch documents
the exact upstream target and its removal conditions. Never modify site-packages.
"""


def install():
    from .qq_group_reply import install_group_reply
    from .qq_mentions import install_mentions
    from .qq_trace import install_trace

    install_mentions()
    install_group_reply()
    # Keep the diagnostic wrapper outermost, so "received" remains the original
    # gateway data rather than the normalized copy passed to upstream Pydantic.
    install_trace()
