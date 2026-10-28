from pathlib import Path

import fitz
from PIL import Image

from docudesk.operations import images_to_pdf, merge_pdfs, pdf_to_images, split_pdf


def make_pdf(path: Path, text: str):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    doc.save(path)
    doc.close()


def test_merge_and_split(tmp_path):
    a, b = tmp_path / "a.pdf", tmp_path / "b.pdf"
    make_pdf(a, "Alpha")
    make_pdf(b, "Beta")
    merged = merge_pdfs([a, b], tmp_path / "merged.pdf")
    with fitz.open(merged) as doc:
        assert doc.page_count == 2
    outputs = split_pdf(merged, tmp_path / "split")
    assert len(outputs) == 2
    with fitz.open(outputs[0]) as doc:
        assert doc.page_count == 1


def test_image_roundtrip(tmp_path):
    img = Image.new("RGB", (100, 100), "white")
    image_path = tmp_path / "page.png"
    img.save(image_path)
    pdf = images_to_pdf([image_path], tmp_path / "image.pdf")
    with fitz.open(pdf) as doc:
        assert doc.page_count == 1
    output = pdf_to_images(pdf, tmp_path / "out", "PNG", 96)
    assert len(output) == 1 and output[0].exists()
