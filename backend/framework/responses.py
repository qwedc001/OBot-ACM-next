"""Shared response text and pagination, independent of adapters and delivery."""


def command_failure(text: str) -> str:
    """Identify the failed command without echoing arbitrary arguments."""
    parts = text.split(maxsplit=1)
    command = "".join(char for char in (parts[0] if parts else "") if char.isprintable())[:80]
    return f"指令匹配失败 {command}，请检查指令语法后重试。"


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
