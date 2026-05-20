"""Stdlib-only arrow-key picker for the CLI.

In a TTY, renders an interactive list (↑/↓ or j/k, enter to pick, q/Esc to quit).
When stdin/stdout aren't TTYs (pipes, scripts), falls back to a numeric menu so
the command still composes.
"""

from __future__ import annotations

import os
import select
import sys
from typing import Optional


def is_interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _read_key(fd: int) -> str:
    """Read one keystroke from a cbreak/raw fd.

    Handles three forms of arrow keys:
      - CSI: ESC [ A/B/C/D
      - SS3: ESC O A/B/C/D    (iTerm in application-cursor mode, etc.)
      - bare ESC (cancel)
    Returns "" on read errors / non-decodable bytes.
    """
    try:
        first = os.read(fd, 1)
    except OSError:
        return ""
    if first != b"\x1b":
        try:
            return first.decode()
        except UnicodeDecodeError:
            return ""
    # ESC arrived. Drain whatever follows within ~200ms; bare ESC stays bare.
    buf = first
    deadline_passed = False
    while not deadline_passed:
        ready, _, _ = select.select([fd], [], [], 0.2)
        if not ready:
            deadline_passed = True
            break
        try:
            more = os.read(fd, 16)
        except OSError:
            break
        if not more:
            break
        buf += more
        # Once we've grabbed the introducer + final byte we're done.
        if len(buf) >= 3 and buf[1:2] in (b"[", b"O"):
            break
    try:
        return buf.decode()
    except UnicodeDecodeError:
        return ""


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
            if ch in ("\x1b[A", "\x1bOA", "k"):
                idx = (idx - 1) % len(items)
                render(first=False)
            elif ch in ("\x1b[B", "\x1bOB", "j"):
                idx = (idx + 1) % len(items)
                render(first=False)
            elif ch in ("\r", "\n"):
                return items[idx]
            elif ch in ("q", "\x03", "\x1b"):
                return None
            # any other key (including unrecognized escape sequences) is ignored
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
