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


def _read_key(fd: int) -> str:
    """Read one keypress from an already-raw fd (or a CSI escape sequence)."""
    ch = sys.stdin.read(1)
    if ch != "\x1b":
        return ch
    # Esc-prefixed sequence. Drain everything that arrives within the window;
    # 0.05s was too tight on slower terminals and made bare arrow keys look
    # like a lone Esc.
    ready, _, _ = select.select([sys.stdin], [], [], 0.15)
    if not ready:
        return ch
    ch += sys.stdin.read(1)
    if ch == "\x1b[":
        ch += sys.stdin.read(1)
    return ch


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

    import termios
    import tty

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

    fd = sys.stdin.fileno()
    old_attrs = termios.tcgetattr(fd)
    sys.stdout.write("\x1b[?25l")  # hide cursor
    sys.stdout.flush()
    try:
        tty.setcbreak(fd)
        render(first=True)
        while True:
            ch = _read_key(fd)
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
        termios.tcsetattr(fd, termios.TCSADRAIN, old_attrs)
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
