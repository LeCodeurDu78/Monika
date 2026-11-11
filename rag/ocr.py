"""OCR for scanned PDF pages (RapidOCR + pypdfium2, both pip-installable).

pypdf extracts the text layer when there is one. For a page with too little text, this module
renders it and runs RapidOCR on the image. Nothing is downloaded: RapidOCR ships its ONNX models
with the wheel, and pypdfium2 ships PDFium.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class OcrOptions:
    enabled: bool = True
    min_chars: int = 40
    max_pages: int = 50
    dpi: int = 200


def available() -> bool:
    """Whether the optional OCR stack is installed."""
    try:
        import pypdfium2  # noqa: F401
        import rapidocr_onnxruntime  # noqa: F401
    except ImportError:
        return False
    return True


_ENGINE: Any = None


def _engine() -> Any:
    """One RapidOCR instance: its ONNX models are expensive to load for every document."""
    global _ENGINE
    if _ENGINE is None:
        from rapidocr_onnxruntime import RapidOCR

        _ENGINE = RapidOCR()
    return _ENGINE


def ocr_pdf(
    path: Path, options: OcrOptions, pages: set[int] | None = None
) -> dict[int, str]:
    """OCR the requested 1-based pages (default: the first `max_pages`) and return their text."""
    if not options.enabled or not available():
        return {}
    import numpy as np
    import pypdfium2 as pdfium

    engine = _engine()
    document = pdfium.PdfDocument(str(path))
    wanted = pages if pages is not None else set(range(1, len(document) + 1))
    out: dict[int, str] = {}
    try:
        for number in sorted(wanted)[: options.max_pages]:
            if number < 1 or number > len(document):
                continue
            page = document[number - 1]
            bitmap = page.render(scale=options.dpi / 72)
            image = np.asarray(bitmap.to_pil().convert("RGB"))[:, :, ::-1].copy()
            lines, _ = engine(image)
            text = "\n".join(str(line[1]) for line in (lines or []) if line[1]).strip()
            if text:
                out[number] = text
    finally:
        document.close()
    return out
