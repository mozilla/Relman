#!/usr/bin/env python3
"""Convert colored terminal output (ANSI escape codes) to an SVG image.

Used to make the screenshots in ../README.md, since GitHub markdown does not
render ANSI colors. Reads from stdin, writes the SVG to stdout:

    script -qc "./firefox_versions.py" /dev/null \\
        | docs/ansi2svg.py --command "./firefox_versions.py" > docs/firefox_versions.svg

`script` runs the command in a pseudo-terminal so it prints colors.
Only the SGR codes the scripts in this folder use are supported: reset, bold,
dim, and the 8 normal and 8 bright foreground colors.

--chrome picks the window decoration (macos, windows, gnome, none) and
--theme the color scheme; run with --help for the list.
"""

import argparse
import re
import sys
from html import escape

FONT = "'DejaVu Sans Mono', Menlo, Consolas, 'Liberation Mono', monospace"
FONT_SIZE = 14
CHAR_W = 8.43   # advance width of DejaVu Sans Mono at 14px
LINE_H = 19
PAD_X = 16
PAD_BOTTOM = 14
BAR_H = 28      # title bar height


def palette(normal, bright=None):
    """ANSI colors 30-37 and 90-97 from two lists of 8 colors."""
    bright = bright or normal
    return {**{30 + i: c for i, c in enumerate(normal)}, **{90 + i: c for i, c in enumerate(bright)}}


# bg, title bar, title bar text/icons, default text, prompt, then the ANSI colors
# (black, red, green, yellow, blue, magenta, cyan, white)
THEMES = {
    "catppuccin-mocha": dict(
        bg="#1e1e2e", bar="#2a2a3c", bar_fg="#a6adc8", fg="#cdd6f4", prompt="#a6adc8",
        colors=palette(["#45475a", "#f38ba8", "#a6e3a1", "#f9e2af", "#89b4fa", "#f5c2e7", "#94e2d5", "#bac2de"],
                       ["#585b70", "#f38ba8", "#a6e3a1", "#f9e2af", "#89b4fa", "#f5c2e7", "#89dceb", "#ffffff"])),
    "github-dark": dict(
        bg="#0d1117", bar="#161b22", bar_fg="#8b949e", fg="#e6edf3", prompt="#8b949e",
        colors=palette(["#484f58", "#ff7b72", "#3fb950", "#d29922", "#58a6ff", "#bc8cff", "#39c5cf", "#b1bac4"],
                       ["#6e7681", "#ffa198", "#56d364", "#e3b341", "#79c0ff", "#d2a8ff", "#56d4dd", "#ffffff"])),
    "github-light": dict(
        bg="#ffffff", bar="#f6f8fa", bar_fg="#57606a", fg="#1f2328", prompt="#57606a",
        colors=palette(["#24292f", "#cf222e", "#116329", "#9a6700", "#0969da", "#8250df", "#1b7c83", "#6e7781"],
                       ["#57606a", "#a40e26", "#1a7f37", "#7d4e00", "#218bff", "#a475f9", "#3192aa", "#8c959f"]),
        border="#d0d7de"),
    "solarized-dark": dict(
        bg="#002b36", bar="#073642", bar_fg="#839496", fg="#93a1a1", prompt="#839496",
        colors=palette(["#073642", "#dc322f", "#859900", "#b58900", "#268bd2", "#d33682", "#2aa198", "#eee8d5"],
                       ["#586e75", "#cb4b16", "#859900", "#b58900", "#268bd2", "#6c71c4", "#2aa198", "#fdf6e3"])),
    "dracula": dict(
        bg="#282a36", bar="#21222c", bar_fg="#bfbfbf", fg="#f8f8f2", prompt="#bfbfbf",
        colors=palette(["#21222c", "#ff5555", "#50fa7b", "#f1fa8c", "#bd93f9", "#ff79c6", "#8be9fd", "#f8f8f2"],
                       ["#6272a4", "#ff6e6e", "#69ff94", "#ffffa5", "#d6acff", "#ff92df", "#a4ffff", "#ffffff"])),
}

CHROMES = ("macos", "windows", "gnome", "none")

SGR = re.compile(r"\x1b\[([0-9;]*)m")
OTHER_ESCAPES = re.compile(r"\x1b\[[0-9;?]*[A-Za-ln-z]")


def parse_line(line):
    """Split a line into (text, style) runs; style is (color, bold, dim)."""
    runs = []
    color, bold, dim = None, False, False
    pos = 0
    for m in SGR.finditer(line):
        if m.start() > pos:
            runs.append((line[pos:m.start()], (color, bold, dim)))
        for code in [int(c) for c in m.group(1).split(";") if c] or [0]:
            if code == 0:
                color, bold, dim = None, False, False
            elif code == 1:
                bold = True
            elif code == 2:
                dim = True
            elif code == 22:
                bold = dim = False
            elif code == 39:
                color = None
            elif 30 <= code <= 37 or 90 <= code <= 97:
                color = code
        pos = m.end()
    if pos < len(line):
        runs.append((line[pos:], (color, bold, dim)))
    return runs


def window(chrome, theme, width, height, title):
    """Background, title bar and window buttons."""
    rx = {"macos": 8, "gnome": 12, "windows": 0, "none": 8}[chrome]
    stroke = f' stroke="{theme["border"]}"' if theme.get("border") else ""
    out = [f'<rect x="0.5" y="0.5" width="{width - 1}" height="{height - 1}" rx="{rx}" fill="{theme["bg"]}"{stroke}/>']
    if chrome == "none":
        return out

    # Title bar with only its top corners rounded
    out.append(f'<path d="M0.5 {rx + 0.5}a{rx} {rx} 0 0 1 {rx} -{rx}h{width - 1 - 2 * rx}'
               f'a{rx} {rx} 0 0 1 {rx} {rx}V{BAR_H}H0.5z" fill="{theme["bar"]}"/>' if rx else
               f'<rect x="0.5" y="0.5" width="{width - 1}" height="{BAR_H - 0.5}" fill="{theme["bar"]}"/>')
    icon = f'stroke="{theme["bar_fg"]}" stroke-width="1.2" fill="none"'
    mid = BAR_H / 2
    if chrome == "macos":
        for i, color in enumerate(("#ff5f57", "#febc2e", "#28c840")):
            out.append(f'<circle cx="{18 + i * 20}" cy="{mid}" r="6" fill="{color}"/>')
    elif chrome == "windows":
        # Minimize, maximize, close glyphs, right-aligned, 46px apart like Windows 11
        x = width - 23
        out.append(f'<path d="M{x - 5} {mid - 5}l10 10M{x + 5} {mid - 5}l-10 10" {icon}/>')
        x -= 46
        out.append(f'<rect x="{x - 5}" y="{mid - 5}" width="10" height="10" {icon}/>')
        x -= 46
        out.append(f'<path d="M{x - 5} {mid}h10" {icon}/>')
    elif chrome == "gnome":
        # Adwaita: a round close button on the right, others hidden by default
        x = width - 18
        out.append(f'<circle cx="{x}" cy="{mid}" r="9" fill="{theme["bar_fg"]}" fill-opacity="0.18"/>')
        out.append(f'<path d="M{x - 3.5} {mid - 3.5}l7 7M{x + 3.5} {mid - 3.5}l-7 7" {icon}/>')
    if title:
        anchor = ('x="12" text-anchor="start"' if chrome == "windows"
                  else f'x="{width / 2}" text-anchor="middle"')
        weight = ' font-weight="bold"' if chrome == "gnome" else ""
        out.append(f'<text {anchor} y="{mid + 4.5}" font-family="system-ui, sans-serif" font-size="13" '
                   f'fill="{theme["bar_fg"]}"{weight}>{escape(title)}</text>')
    return out


def to_svg(text, command=None, chrome="macos", theme_name="catppuccin-mocha", title=None):
    theme = THEMES[theme_name]
    text = OTHER_ESCAPES.sub("", text.replace("\r\n", "\n").replace("\r", ""))
    lines = text.rstrip("\n").split("\n")
    # Drop leading blank lines, the prompt line takes their place
    while lines and not SGR.sub("", lines[0]).strip():
        lines.pop(0)
    rows = [[(f"$ {command}", ("prompt", False, False))]] if command else []
    rows += [parse_line(line) for line in lines]

    pad_top = (BAR_H if chrome != "none" else 0) + 12
    cols = max((sum(len(t) for t, _ in r) for r in rows), default=0)
    width = round(PAD_X * 2 + cols * CHAR_W)
    if chrome == "windows":
        width = max(width, 200)
    height = pad_top + len(rows) * LINE_H + PAD_BOTTOM

    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
           f'viewBox="0 0 {width} {height}">']
    out += window(chrome, theme, width, height, title)
    out.append(f'<g font-family="{FONT}" font-size="{FONT_SIZE}" fill="{theme["fg"]}" xml:space="preserve">')
    for i, runs in enumerate(rows):
        y = pad_top + i * LINE_H + FONT_SIZE
        col = 0
        spans = []
        for chunk, (color, bold, dim) in runs:
            if chunk.strip():
                attrs = [f'x="{PAD_X + col * CHAR_W:.2f}"',
                         # Force the run width so glyphs from a fallback font stay on the grid
                         f'textLength="{len(chunk) * CHAR_W:.2f}"', 'lengthAdjust="spacingAndGlyphs"']
                if color == "prompt":
                    attrs.append(f'fill="{theme["prompt"]}"')
                elif color is not None:
                    attrs.append(f'fill="{theme["colors"][color]}"')
                if bold:
                    attrs.append('font-weight="bold"')
                if dim:
                    attrs.append('opacity="0.6"')
                spans.append(f'<tspan {" ".join(attrs)}>{escape(chunk, quote=False)}</tspan>')
            col += len(chunk)
        if spans:
            out.append(f'<text y="{y}">{"".join(spans)}</text>')
    out += ["</g>", "</svg>"]
    return "\n".join(out) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--command", help="command line to show as a prompt on the first line")
    parser.add_argument("--chrome", choices=CHROMES, default="macos", help="window decoration (default: macos)")
    parser.add_argument("--theme", choices=sorted(THEMES), default="catppuccin-mocha",
                        help="color scheme (default: catppuccin-mocha)")
    parser.add_argument("--title", help="text shown in the title bar")
    args = parser.parse_args()
    sys.stdout.write(to_svg(sys.stdin.read(), args.command, args.chrome, args.theme, args.title))


if __name__ == "__main__":
    main()
