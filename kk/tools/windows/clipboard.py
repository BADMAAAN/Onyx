from __future__ import annotations

import pyperclip


def read_clipboard() -> dict[str, str | int]:
    text = pyperclip.paste()
    if len(text) > 100_000:
        raise ValueError("Clipboard text exceeds 100,000 characters")
    return {"text": text, "length": len(text)}


def write_clipboard(text: str) -> dict[str, int]:
    if len(text) > 100_000:
        raise ValueError("Clipboard text exceeds 100,000 characters")
    pyperclip.copy(text)
    return {"written_characters": len(text)}

