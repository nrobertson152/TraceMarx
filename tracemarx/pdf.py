# SPDX-License-Identifier: GPL-3.0-or-later
"""A small PDF writer (standard library only): pages of text, lines and rectangles in the built-in Helvetica
fonts. Units are points (1/72 inch); the origin is the top-left corner, y grows downwards (converted here).

Text is encoded in WinAnsi (cp1252); characters outside it print as "?". Widths use Helvetica's real metrics
for ASCII so centred and fitted text lands where it should."""
from __future__ import annotations

import zlib

MM = 72 / 25.4
PAGE_SIZES = {"A4": (210 * MM, 297 * MM), "Letter": (8.5 * 72, 11 * 72)}

# Helvetica advance widths (1/1000 em) for 32..126, from the standard AFM.
_HELV = [278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278, 556, 556, 556, 556, 556,
         556, 556, 556, 556, 556, 278, 278, 584, 584, 584, 556, 1015, 667, 667, 722, 722, 667, 611, 778, 722, 278,
         500, 667, 556, 833, 722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469,
         556, 333, 556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556, 556, 556, 333, 500,
         278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584]
_HELV_BOLD = [278, 333, 474, 556, 556, 889, 722, 238, 333, 333, 389, 584, 278, 333, 278, 278, 556, 556, 556, 556,
              556, 556, 556, 556, 556, 556, 333, 333, 584, 584, 584, 611, 975, 722, 722, 722, 722, 667, 611, 778,
              722, 278, 556, 722, 611, 833, 722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 333,
              278, 333, 584, 556, 333, 556, 611, 556, 611, 556, 333, 611, 611, 278, 278, 556, 278, 889, 611, 611,
              611, 611, 389, 556, 333, 611, 556, 778, 556, 556, 500, 389, 280, 389, 584]


def text_width(text: str, size: float, bold: bool = False) -> float:
    text = _plain(text)
    table = _HELV_BOLD if bold else _HELV
    return sum(table[ord(ch) - 32] if 32 <= ord(ch) <= 126 else 556 for ch in text) * size / 1000


def fit_size(text: str, width: float, size: float, bold: bool = False, minimum: float = 4.0) -> float:
    """The largest font size up to ``size`` at which ``text`` fits in ``width``."""
    w = text_width(text, size, bold)
    return size if w <= width or w == 0 else max(minimum, size * width / w)


ASCII_FOR = {"→": "->", "←": "<-", "↔": "<->", "⅝": "5/8", "¾": "3/4", "½": "1/2", "✓": "v", "≥": ">=", "≤": "<="}


def _plain(text: str) -> str:
    for k, v in ASCII_FOR.items():
        text = text.replace(k, v)
    return text


def _esc(text: str) -> str:
    raw = _plain(text).encode("cp1252", "replace").decode("latin-1")
    return raw.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)").replace("\r", " ").replace("\n", " ")


class Page:
    def __init__(self, width: float, height: float):
        self.width, self.height = width, height
        self.ops: list[str] = []

    def _y(self, y: float) -> float:
        return self.height - y

    def text(self, x: float, y: float, text: str, size: float = 10, bold: bool = False, align: str = "left",
             gray: float = 0.0) -> None:
        """``y`` is the text baseline."""
        if not text:
            return
        w = text_width(text, size, bold)
        if align == "center":
            x -= w / 2
        elif align == "right":
            x -= w
        font = "F2" if bold else "F1"
        self.ops.append(f"{gray:.3f} g BT /{font} {size:.2f} Tf {x:.2f} {self._y(y):.2f} Td ({_esc(text)}) Tj ET")

    def line(self, x0: float, y0: float, x1: float, y1: float, width: float = 0.5, gray: float = 0.0,
             dash: bool = False) -> None:
        d = "[2 2] 0 d " if dash else "[] 0 d "
        self.ops.append(f"{gray:.3f} G {width:.2f} w {d}{x0:.2f} {self._y(y0):.2f} m {x1:.2f} {self._y(y1):.2f} l S")

    def rect(self, x: float, y: float, w: float, h: float, width: float = 0.5, gray: float = 0.0,
             fill: float | None = None, dash: bool = False, radius: float = 0.0) -> None:
        d = "[2 2] 0 d " if dash else "[] 0 d "
        y0 = self._y(y + h)
        if radius > 0:
            r = min(radius, w / 2, h / 2)
            k = r * 0.5523
            x1, y1 = x + w, y0 + h
            path = (f"{x + r:.2f} {y0:.2f} m {x1 - r:.2f} {y0:.2f} l {x1 - r + k:.2f} {y0:.2f} {x1:.2f} {y0 + r - k:.2f} "
                    f"{x1:.2f} {y0 + r:.2f} c {x1:.2f} {y1 - r:.2f} l {x1:.2f} {y1 - r + k:.2f} {x1 - r + k:.2f} "
                    f"{y1:.2f} {x1 - r:.2f} {y1:.2f} c {x + r:.2f} {y1:.2f} l {x + r - k:.2f} {y1:.2f} {x:.2f} "
                    f"{y1 - r + k:.2f} {x:.2f} {y1 - r:.2f} c {x:.2f} {y0 + r:.2f} l {x:.2f} {y0 + r - k:.2f} "
                    f"{x + r - k:.2f} {y0:.2f} {x + r:.2f} {y0:.2f} c h")
        else:
            path = f"{x:.2f} {y0:.2f} {w:.2f} {h:.2f} re"
        if fill is not None:
            self.ops.append(f"{fill:.3f} g {path} f")
        if width > 0:
            self.ops.append(f"{gray:.3f} G {width:.2f} w {d}{path} S")


class Document:
    def __init__(self, title: str = "", author: str = "TraceMarx"):
        self.title, self.author = title, author
        self.pages: list[Page] = []

    def add_page(self, width: float, height: float) -> Page:
        page = Page(width, height)
        self.pages.append(page)
        return page

    def to_bytes(self) -> bytes:
        objects: list[bytes] = []

        def add(body: bytes) -> int:
            objects.append(body)
            return len(objects)

        catalog = add(b"")                      # filled in below
        pages_id = add(b"")
        f1 = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
        f2 = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
        kids = []
        for page in self.pages or [Page(*PAGE_SIZES["A4"])]:
            content = zlib.compress("\n".join(page.ops).encode("latin-1"))
            cid = add(b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(content) + content + b"\nendstream")
            pid = add((f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 {page.width:.2f} {page.height:.2f}] "
                       f"/Resources << /Font << /F1 {f1} 0 R /F2 {f2} 0 R >> >> /Contents {cid} 0 R >>").encode())
            kids.append(pid)
        objects[catalog - 1] = f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode()
        objects[pages_id - 1] = (f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] "
                                 f"/Count {len(kids)} >>").encode()
        info = add(f"<< /Title ({_esc(self.title)}) /Producer ({_esc(self.author)}) >>".encode("latin-1"))
        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for i, body in enumerate(objects, 1):
            offsets.append(len(out))
            out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
        xref = len(out)
        out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
        for off in offsets:
            out += f"{off:010d} 00000 n \n".encode()
        out += (f"trailer\n<< /Size {len(objects) + 1} /Root {catalog} 0 R /Info {info} 0 R >>\n"
                f"startxref\n{xref}\n%%EOF\n").encode()
        return bytes(out)
