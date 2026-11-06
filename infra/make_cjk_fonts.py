#!/usr/bin/env python3
"""Make the PDF's Chinese/Japanese/Korean fallback fonts (M3h §18a) from the full Noto fonts.

    python infra/make_cjk_fonts.py <NotoSansSC[wght].ttf> <NotoSansKR[wght].ttf>

The full fonts (google/fonts, ofl/notosanssc and ofl/notosanskr) are 28 MB together. This keeps
the regular weight and only the characters of the national standard sets (GB 2312, Big5,
JIS X 0208/0212, KS X 1001), about 8 MB, and writes them to neurolens/fonts/. Laptop only.
"""

import sys
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

FONTS = Path(__file__).resolve().parent.parent / "neurolens" / "fonts"
BASICS = set(range(0x20, 0x7F)) | set(range(0x3000, 0x3100)) | set(range(0xFF00, 0xFFF0))


def standard_set(codec, lead, trail):
    """Every character a two-byte codec can decode from the given lead and trail bytes."""
    found = set()
    for a in lead:
        for b in trail:
            try:
                found.add(ord(bytes([a, b]).decode(codec)))
            except (UnicodeDecodeError, TypeError):
                pass
    return found


def trim(source, target, unicodes):
    font = instancer.instantiateVariableFont(TTFont(source), {"wght": 400})
    options = subset.Options()
    options.layout_features = ["*"]
    subsetter = subset.Subsetter(options)
    subsetter.populate(unicodes=unicodes)
    subsetter.subset(font)
    font.save(target)
    print(f"Wrote {target} ({target.stat().st_size // 1024} KB)")


def main():
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    chinese = standard_set("gb2312", range(0xA1, 0xF8), range(0xA1, 0xFF)) | standard_set(
        "big5", range(0xA1, 0xFA), [*range(0x40, 0x7F), *range(0xA1, 0xFF)]
    )
    japanese = standard_set("euc_jp", range(0xA1, 0xF5), range(0xA1, 0xFF))
    korean = standard_set("euc_kr", range(0xA1, 0xFE), range(0xA1, 0xFF))
    trim(sys.argv[1], FONTS / "NotoSansSC.ttf", chinese | japanese | BASICS)
    trim(sys.argv[2], FONTS / "NotoSansKR.ttf", korean | BASICS)


if __name__ == "__main__":
    main()
