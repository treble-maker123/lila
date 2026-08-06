"""Flatten a raw email seed into a single-line, de-anonymized body string.

Reads a seed file under ``datasets/seed/`` whose HTML body lives inside the
first ``` fenced code block (see datasets/seed/dunkin_promotion.md), decodes the
quoted-printable HTML, drops scripts/styles, reduces images to their alt text,
neutralizes anchor hrefs, strips invisible preheader padding, and collapses the
result to a single line written next to the seed as ``<stem>.body.txt``.

Run from the experiment root:

    python datasets/scripts/deanonymize.py dunkin_promotion.md
"""

from __future__ import annotations

import quopri
import re
import sys
from pathlib import Path

from bs4 import BeautifulSoup

SEED_DIR = Path(__file__).resolve().parent.parent / "seed"

# Zero-width spaces, directional marks, word joiner, BOM, combining grapheme
# joiner, and soft hyphen — all used as invisible preheader padding.
INVISIBLE = re.compile(r"[​-‏‪-‮⁠﻿͏­]")


def deanonymize(seed_path: Path) -> str:
    src = seed_path.read_text()

    # The HTML body lives inside the first ``` fenced code block after "Body:".
    body_qp = src.split("```", 2)[1]
    first_line = body_qp.lstrip().split("\n", 1)[0].strip()
    if first_line and not first_line.startswith("<"):  # drop a language token
        body_qp = body_qp.split("\n", 1)[1]

    raw = quopri.decodestring(body_qp.encode("utf-8")).decode("utf-8", "replace")
    soup = BeautifulSoup(raw, "html.parser")

    for tag in soup(["script", "style"]):
        tag.decompose()
    for img in soup("img"):  # keep alt text, drop URLs
        img.replace_with(f"[img: {img.get('alt', '')}]" if img.get("alt") else "")
    for a in soup("a"):  # keep anchor text, drop hrefs
        a.attrs = {"href": "https://example.com/link"}

    text = re.sub(r"\n{3,}", "\n\n", soup.get_text("\n", strip=True))
    text = INVISIBLE.sub("", text)

    single = re.sub(r"\s*\n\s*", " ", text).strip()
    single = re.sub(r"[ \t]{2,}", " ", single)
    return single


def main() -> None:
    name = sys.argv[1] if len(sys.argv) > 1 else "dunkin_promotion.md"
    seed_path = SEED_DIR / name
    body = deanonymize(seed_path)
    out_path = seed_path.with_suffix(".body.txt")
    out_path.write_text(body + "\n")
    print(f"Wrote {len(body)} chars to {out_path}")


if __name__ == "__main__":
    main()
