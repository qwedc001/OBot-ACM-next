"""Shared response text and pagination, independent of adapters and delivery."""


def command_failure(text: str, *, usage: str = "", suggestions=(), help_command: str = "/help") -> str:
    """One complete syntax-error response, without echoing arbitrary arguments."""
    parts = text.split(maxsplit=1)
    command = "".join(char for char in (parts[0] if parts else "") if char.isprintable())[:80]
    lines = [f"指令匹配失败 {command}，请检查指令语法后重试。"]
    if suggestions:
        lines.extend(["你可能想用：", *suggestions])
    elif usage:
        lines.append("用法：" + usage)
    lines.append(f"使用 {help_command} 查看可用指令。")
    return "\n".join(lines)


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
