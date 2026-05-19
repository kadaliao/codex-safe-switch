"""Stdlib-only arrow-key picker for the CLI.

In a TTY, renders an interactive list (↑/↓ or j/k, enter to pick, q/Esc to quit).
When stdin/stdout aren't TTYs (pipes, scripts), falls back to a numeric menu so
the command still composes.
"""

from __future__ import annotations

import select
import sys
from typing import Optional


def is_interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _read_key() -> str:
    """Read one keypress (or a 3-byte escape sequence for arrow keys)."""
    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == "\x1b":
            # Short timeout — bare ESC vs. a CSI sequence
            ready, _, _ = select.select([sys.stdin], [], [], 0.05)
            if ready:
                ch += sys.stdin.read(2)
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def pick(
    items: list[str],
    *,
    active: Optional[str] = None,
    prompt: str = "Pick a profile",
) -> Optional[str]:
    if not items:
        return None
    if not is_interactive():
        return _pick_numeric(items, active, prompt)

    idx = items.index(active) if active in items else 0
    rendered_lines = 0
    header = f"{prompt} (↑/↓ to move, enter to switch, q to cancel)"

    def render(first: bool) -> None:
        nonlocal rendered_lines
        if not first:
            # cursor up to the start of the block we drew last time
            sys.stdout.write(f"\x1b[{rendered_lines}A\r")
        lines = [header, ""]
        for i, item in enumerate(items):
            cursor = "▸" if i == idx else " "
            star = "★" if item == active else " "
            lines.append(f" {cursor} {star} {item}")
        for line in lines:
            sys.stdout.write("\x1b[2K" + line + "\n")
        rendered_lines = len(lines)
        sys.stdout.flush()

    sys.stdout.write("\x1b[?25l")  # hide cursor
    sys.stdout.flush()
    try:
        render(first=True)
        while True:
            ch = _read_key()
            if ch in ("\x1b[A", "k"):
                idx = (idx - 1) % len(items)
                render(first=False)
            elif ch in ("\x1b[B", "j"):
                idx = (idx + 1) % len(items)
                render(first=False)
            elif ch in ("\r", "\n"):
                return items[idx]
            elif ch in ("q", "\x03", "\x1b"):
                return None
    finally:
        sys.stdout.write("\x1b[?25h")  # show cursor again
        sys.stdout.flush()


def _pick_numeric(
    items: list[str],
    active: Optional[str],
    prompt: str,
) -> Optional[str]:
    print(prompt)
    for i, item in enumerate(items, 1):
        marker = "★" if item == active else " "
        print(f"  {i}) {marker} {item}")
    while True:
        try:
            raw = input("number (empty to cancel): ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return None
        if not raw:
            return None
        try:
            n = int(raw)
        except ValueError:
            print("not a number")
            continue
        if 1 <= n <= len(items):
            return items[n - 1]
        print(f"out of range (1..{len(items)})")
