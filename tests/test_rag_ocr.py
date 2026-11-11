"""OCR fills only pages whose text layer is too short; a failure keeps the text layer."""

from __future__ import annotations

import pypdf

from rag import loaders
from rag.ocr import OcrOptions


class FakePage:
    def __init__(self, text: str) -> None:
        self._text = text

    def extract_text(self) -> str:
        return self._text


class FakeReader:
    def __init__(self, _path: str) -> None:
        self.pages = [
            FakePage(""),
            FakePage("couche texte suffisante pour cette page, largement au-dessus du seuil"),
        ]
        self.is_encrypted = False


def scanned_pdf(tmp_path):
    path = tmp_path / "scan.pdf"
    path.write_bytes(b"%PDF-1.4\n")
    return path


def test_ocr_replaces_a_page_with_too_little_text(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(pypdf, "PdfReader", FakeReader)
    seen: dict = {}

    def fake_ocr(path, options, pages):
        seen["pages"] = pages
        return {1: "texte reconnu par l'OCR"}

    monkeypatch.setattr(loaders, "ocr_pdf", fake_ocr)

    loaded = loaders.load(scanned_pdf(tmp_path), "pdf", 500, 50, OcrOptions(enabled=True))

    assert seen["pages"] == {1}
    assert "texte reconnu par l'OCR" in loaded.full_text()
    assert "couche texte suffisante" in loaded.full_text()


def test_a_broken_ocr_never_loses_the_text_layer(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(pypdf, "PdfReader", FakeReader)

    def broken_ocr(path, options, pages):
        raise RuntimeError("modèle absent")

    monkeypatch.setattr(loaders, "ocr_pdf", broken_ocr)

    loaded = loaders.load(scanned_pdf(tmp_path), "pdf", 500, 50, OcrOptions(enabled=True))

    assert "couche texte suffisante" in loaded.full_text()
