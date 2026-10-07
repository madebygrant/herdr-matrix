import os
import random
import select
import shutil
import sys
import termios
import time
import tty

GLYPHS = "ｱｲｳｴｵｶｷｸｹｺｻｼｽｾｿﾀﾁﾂﾃﾄﾅﾆﾇﾈﾉﾊﾋﾌﾍﾎﾏﾐﾑﾒﾓﾔﾕﾖﾗﾘﾙﾚﾛﾜﾝ0123456789"
HEAD = "\x1b[38;2;220;255;230m"
BRIGHT = "\x1b[38;2;0;255;65m"
MID = "\x1b[38;2;0;143;17m"
DIM = "\x1b[38;2;0;59;0m"


def main():
    cols, rows = shutil.get_terminal_size()
    # Half-width katakana are one cell wide, so one drop per column works.
    drops = [random.randint(-rows, 0) for _ in range(cols)]
    speed = [random.choice((1, 1, 2)) for _ in range(cols)]
    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    out = sys.stdout
    out.write("\x1b[?1049h\x1b[?25l\x1b[2J")
    try:
        tty.setcbreak(fd)
        while True:
            for c in range(cols):
                y = drops[c]
                for dy, color in ((0, HEAD), (1, BRIGHT), (3, MID), (8, DIM)):
                    r = y - dy
                    if 0 <= r < rows:
                        out.write(f"\x1b[{r + 1};{c + 1}H{color}{random.choice(GLYPHS)}")
                tail = y - 18
                if 0 <= tail < rows:
                    out.write(f"\x1b[{tail + 1};{c + 1}H ")
                drops[c] += speed[c]
                if drops[c] - 18 > rows:
                    drops[c] = random.randint(-rows // 2, 0)
            out.write("\x1b[0m")
            out.flush()
            if select.select([fd], [], [], 0.06)[0]:
                os.read(fd, 1024)
                break
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)
        out.write("\x1b[0m\x1b[?25h\x1b[?1049l")
        out.flush()


main()
