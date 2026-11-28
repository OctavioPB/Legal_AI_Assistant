"""Tiny PDF builder for tests (ADR-030): pages with a real text layer, and scanned pages (one
full-page grey image, no text). Hand-written PDF so the tests need no PDF library to create them."""

from __future__ import annotations

import zlib
from collections.abc import Sequence

from PIL import Image, ImageDraw, ImageFont

PAGE_W, PAGE_H = 612, 792  # US Letter in points


def scan_image(lines: Sequence[str], *, dpi: int = 200, noise: bool = False) -> Image.Image:
    """A page as a scanner would see it: black text on white, at `dpi`."""
    w, h = PAGE_W * dpi // 72, PAGE_H * dpi // 72
    img = Image.new("L", (w, h), 255)
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=max(dpi // 6, 12))
    y = dpi
    for line in lines:
        draw.text((dpi, y), line, fill=0, font=font)
        y += dpi // 3
    if noise:  # a ruined scan: dense random speckles over the text
        import random

        rnd = random.Random(7)
        for _ in range(w * h // 6):
            draw.point((rnd.randrange(w), rnd.randrange(h)), fill=rnd.choice((0, 255)))
    return img


def build_pdf(pages: Sequence[str | Image.Image]) -> bytes:
    """A str is a page with that text in its text layer; an Image is a scanned page."""
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    kids: list[int] = []
    pages_id = add(b"")  # placeholder, filled at the end
    for page in pages:
        resources = f"<< /Font << /F1 {font} 0 R >> ".encode()
        if isinstance(page, str):
            lines = page.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)").split("\n")
            ops = ["BT", "/F1 12 Tf", "72 720 Td", "14 TL"]
            ops += [f"({ln}) Tj T*" for ln in lines]
            ops.append("ET")
            content = "\n".join(ops).encode("latin-1")
        else:
            img = page.convert("L")
            data = zlib.compress(img.tobytes())
            image = add(
                f"<< /Type /XObject /Subtype /Image /Width {img.width} /Height {img.height} "
                f"/ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /FlateDecode "
                f"/Length {len(data)} >>\nstream\n".encode()
                + data
                + b"\nendstream"
            )
            resources += f"/XObject << /Im1 {image} 0 R >> ".encode()
            content = f"q {PAGE_W} 0 0 {PAGE_H} 0 0 cm /Im1 Do Q".encode()
        resources += b">>"
        stream = add(b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream")
        kids.append(
            add(
                f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 {PAGE_W} {PAGE_H}] "
                f"/Contents {stream} 0 R /Resources ".encode()
                + resources
                + b" >>"
            )
        )
    objects[pages_id - 1] = (
        f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>"
    ).encode()
    catalog = add(f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode())
    out = bytearray(b"%PDF-1.7\n")
    offsets: list[int] = []
    for n, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root {catalog} 0 R >>\n".encode()
    out += f"startxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)
