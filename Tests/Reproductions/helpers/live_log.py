"""Save an unchanged byte stream and show a fixed-height rolling terminal preview.
We use this to print some verbose output in the experiment runners"""
import argparse
import codecs
from collections import deque
import os
from pathlib import Path
import re
import select
import shutil
import signal
import sys
import time
import unicodedata


ANSI = re.compile(r"\x1b\][^\x07]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]|\x1b[@-_]")
PREVIEW_LINES = 10
RAW_TAIL_LINES = 2


def display_line(text, width):
    """Remove terminal controls and truncate by display cells to prevent wrapping."""
    text = ANSI.sub("", text).expandtabs(4)
    result, cells = [], 0
    for char in text:
        if unicodedata.category(char).startswith("C"):
            continue
        size = 0 if unicodedata.combining(char) else (2 if unicodedata.east_asian_width(char) in "WF" else 1)
        if cells + size > width:
            break
        result.append(char)
        cells += size
    return "".join(result)


def preview(log_path, append=False):
    interactive = sys.stdout.isatty() and os.environ.get("TERM") != "dumb"
    lines = deque(maxlen=PREVIEW_LINES)
    partial = ""
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    started = time.monotonic()
    last_draw = 0
    section = "=== EXPERIMENT RUNNER OUTPUT ==="
    reserved = False

    def reserve():
        nonlocal reserved
        if interactive:
            sys.stdout.write("\n" * PREVIEW_LINES + f"\x1b[{PREVIEW_LINES}A\r")
            sys.stdout.flush()
            reserved = True

    def draw(clear=False):
        width = max(1, shutil.get_terminal_size().columns - 1)
        shown = [] if clear else list(lines)
        if not clear:
            if partial:
                shown.append(partial)
            elif not shown:
                shown.append(f"Waiting for output... {int(time.monotonic() - started)}s elapsed")
        shown = ([section] + shown[-(PREVIEW_LINES - 1):]) if not clear else []
        sys.stdout.write("".join("\r\x1b[2K" + display_line(line, width) + "\n"
                                 for line in shown + [""] * (PREVIEW_LINES - len(shown))))
        sys.stdout.write(f"\x1b[{PREVIEW_LINES}A\r")
        sys.stdout.flush()

    def retain_raw_tail():
        nonlocal reserved
        if not section.startswith("=== RAW TOOL OUTPUT"):
            return
        if reserved:
            draw(clear=True)
            reserved = False
        # Leave a permanent compact record above the next transient preview.
        header = section[:-4] + f" | last {RAW_TAIL_LINES} nonempty lines of raw output ==="
        print(header)
        width = max(1, shutil.get_terminal_size().columns - 1) if interactive else 4096
        for line in list(lines)[-RAW_TAIL_LINES:]:
            print(display_line(line, width))
        if not lines:
            print("[no nonempty raw output]")
        sys.stdout.flush()

    def consume(line):
        nonlocal section
        if line.startswith("=== ") and line.endswith(" ==="):
            retain_raw_tail()
            section = line
            lines.clear()
            if not reserved:
                reserve()
        elif line.strip():
            lines.append(line[-4096:])

    # Reserve the preview rows beneath the launcher's run header, then reuse them.
    with log_path.open("ab" if append else "wb", buffering=0) as log:
        reserve()
        try:
            while True:
                ready, _, _ = select.select([sys.stdin.buffer], [], [], 0.2)
                if ready:
                    data = os.read(sys.stdin.fileno(), 65536)
                    if not data:
                        partial += decoder.decode(b"", final=True)
                        if partial:
                            consume(partial)
                            partial = ""
                        retain_raw_tail()
                        break
                    log.write(data)
                    # Also retain raw tails when stdout is redirected, without ANSI redraws.
                    pieces = re.split(r"[\r\n]", partial + decoder.decode(data))
                    for line in pieces[:-1]:
                        consume(line)
                    partial = pieces[-1][-4096:]
                if interactive and time.monotonic() - last_draw >= 0.2:
                    draw()
                    last_draw = time.monotonic()
        finally:
            if reserved:
                draw(clear=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--append", action="store_true", help="Keep the launcher's log header")
    args = parser.parse_args()

    def interrupted(signum, _frame):
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGINT, interrupted)
    signal.signal(signal.SIGTERM, interrupted)
    preview(args.log, append=args.append)


if __name__ == "__main__":
    main()
