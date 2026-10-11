"""Optional text pagination, independent of adapters and message delivery."""


def text_chunks(text: str, limit: int = 1500) -> tuple[str, ...]:
    """Preserve every character while preferring line boundaries for long help."""
    if limit < 1:
        raise ValueError("Text chunk limit must be positive")
    chunks = []
    while len(text) > limit:
        boundary = text.rfind("\n", 0, limit) + 1
        boundary = boundary or limit
        chunks.append(text[:boundary])
        text = text[boundary:]
    if text:
        chunks.append(text)
    return tuple(chunks)
