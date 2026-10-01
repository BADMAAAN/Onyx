from __future__ import annotations


def terminal_confirmation(summary: str) -> bool:
    answer = input(f"[CONFIRM] {summary}\nВведите ДА для подтверждения: ").strip()
    return answer.casefold() == "да"

