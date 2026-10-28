from __future__ import annotations

from pathlib import Path
from typing import Iterable

import fitz
from PIL import Image


def merge_pdfs(paths: Iterable[str | Path], output: str | Path) -> Path:
    sources = [Path(p) for p in paths]
    if len(sources) < 2:
        raise ValueError("Для объединения выберите минимум два PDF-файла.")
    for source in sources:
        if source.suffix.lower() != ".pdf" or not source.is_file():
            raise ValueError(f"Это не существующий PDF-файл: {source}")
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    merged = fitz.open()
    try:
        for source in sources:
            with fitz.open(source) as doc:
                if doc.is_encrypted and not doc.authenticate(""):
                    raise ValueError(f"PDF защищён паролем: {source.name}")
                merged.insert_pdf(doc)
        merged.save(output, garbage=4, deflate=True)
    finally:
        merged.close()
    return output


def split_pdf(path: str | Path, output_dir: str | Path) -> list[Path]:
    source = Path(path)
    if not source.is_file() or source.suffix.lower() != ".pdf":
        raise ValueError("Выберите существующий PDF-файл.")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []
    with fitz.open(source) as doc:
        if doc.is_encrypted and not doc.authenticate(""):
            raise ValueError("PDF защищён паролем.")
        for index in range(doc.page_count):
            page_doc = fitz.open()
            try:
                page_doc.insert_pdf(doc, from_page=index, to_page=index)
                target = output_dir / f"{source.stem}_page_{index + 1:03}.pdf"
                page_doc.save(target, garbage=4, deflate=True)
                outputs.append(target)
            finally:
                page_doc.close()
    return outputs


def images_to_pdf(paths: Iterable[str | Path], output: str | Path) -> Path:
    images = [Path(p) for p in paths]
    if not images:
        raise ValueError("Добавьте хотя бы одно изображение.")
    pages: list[Image.Image] = []
    try:
        for path in images:
            with Image.open(path) as image:
                pages.append(image.convert("RGB"))
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        pages[0].save(output, "PDF", save_all=True, append_images=pages[1:])
        return output
    finally:
        for page in pages:
            page.close()


def pdf_to_images(path: str | Path, output_dir: str | Path, image_format: str = "PNG", dpi: int = 150) -> list[Path]:
    source = Path(path)
    fmt = image_format.upper()
    if fmt not in {"PNG", "JPG", "JPEG"}:
        raise ValueError("Поддерживаются форматы PNG и JPG.")
    if not source.is_file() or source.suffix.lower() != ".pdf":
        raise ValueError("Выберите существующий PDF-файл.")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    extension = "jpg" if fmt in {"JPG", "JPEG"} else "png"
    outputs: list[Path] = []
    with fitz.open(source) as doc:
        if doc.is_encrypted and not doc.authenticate(""):
            raise ValueError("PDF защищён паролем.")
        for index, page in enumerate(doc):
            pix = page.get_pixmap(dpi=dpi, alpha=False)
            target = output_dir / f"{source.stem}_page_{index + 1:03}.{extension}"
            pix.save(target)
            outputs.append(target)
    return outputs


def compress_pdf(path: str | Path, output: str | Path) -> Path:
    source, output = Path(path), Path(output)
    if not source.is_file() or source.suffix.lower() != ".pdf":
        raise ValueError("Выберите существующий PDF-файл.")
    output.parent.mkdir(parents=True, exist_ok=True)
    with fitz.open(source) as doc:
        if doc.is_encrypted and not doc.authenticate(""):
            raise ValueError("PDF защищён паролем.")
        doc.save(output, garbage=4, deflate=True, deflate_images=True, deflate_fonts=True)
    return output
