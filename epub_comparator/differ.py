"""Diff logic: compare original vs one optimized version of an EPUB."""
from __future__ import annotations
import difflib
import posixpath
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

try:
    from PIL import Image as _PilImage
    _HAS_PILLOW = True
except ImportError:
    _HAS_PILLOW = False

from .epub_reader import EpubReader
from .models import (
    BookTriplet, DiffKind, DiffResult, FileDiff, ImageDiff, ImageInfo,
    MetadataDiff, TextDiff, VersionLabel,
)

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg"}


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def diff_pair(
    triplet: BookTriplet,
    compared_label: VersionLabel,
) -> DiffResult:
    """Build a full DiffResult between original and the specified version."""
    original_src = triplet.original
    compared_src = (
        triplet.web_optimized
        if compared_label == VersionLabel.WEB_OPTIMIZED
        else triplet.integrate_optimized
    )

    result = DiffResult(triplet=triplet, compared_label=compared_label)

    if original_src:
        result.container_sizes[VersionLabel.ORIGINAL.value] = original_src.file_size
    if compared_src:
        result.container_sizes[compared_label.value] = compared_src.file_size

    if not original_src or not compared_src:
        return result

    with EpubReader(original_src.path) as orig_r, EpubReader(compared_src.path) as cmp_r:
        _diff_files(orig_r, cmp_r, result)
        _diff_images(orig_r, cmp_r, result)
        _diff_metadata(orig_r, cmp_r, result)
        _diff_text_files(orig_r, cmp_r, result, media_type="text/css", target=result.css_diffs)
        _diff_text_files(
            orig_r, cmp_r, result,
            media_type="application/xhtml+xml",
            target=result.xhtml_diffs,
            use_text_extract=True,
        )

    return result


def diff_all(triplet: BookTriplet) -> list[DiffResult]:
    """Return diffs vs web-optimized and integrate-optimized."""
    results = []
    if triplet.web_optimized:
        results.append(diff_pair(triplet, VersionLabel.WEB_OPTIMIZED))
    if triplet.integrate_optimized:
        results.append(diff_pair(triplet, VersionLabel.INTEGRATE_OPTIMIZED))
    return results


# ---------------------------------------------------------------------------
# File-list diff
# ---------------------------------------------------------------------------

def _diff_files(orig: EpubReader, cmp: EpubReader, result: DiffResult):
    orig_files = set(orig.list_files())
    cmp_files  = set(cmp.list_files())

    # Build stem → path maps for detecting format-change conversions
    def stem_map(files):
        m: dict[str, str] = {}
        for f in files:
            stem = posixpath.splitext(posixpath.basename(f))[0]
            m.setdefault(stem, f)  # first match wins (most books have unique stems)
        return m

    orig_stems = stem_map(orig_files)
    cmp_stems  = stem_map(cmp_files)

    handled: set[str] = set()

    for f in sorted(orig_files):
        if f in cmp_files:
            # identical path — no FileDiff needed (no change in presence)
            handled.add(f)
            continue
        stem = posixpath.splitext(posixpath.basename(f))[0]
        if stem in cmp_stems:
            cmp_path = cmp_stems[stem]
            result.file_diffs.append(FileDiff(
                kind=DiffKind.FORMAT_CHANGED,
                path=f,
                original_path=f,
                optimized_path=cmp_path,
            ))
            handled.add(f)
            handled.add(cmp_path)
        else:
            result.file_diffs.append(FileDiff(kind=DiffKind.REMOVED, path=f))
            handled.add(f)

    for f in sorted(cmp_files):
        if f not in handled:
            result.file_diffs.append(FileDiff(kind=DiffKind.ADDED, path=f))


# ---------------------------------------------------------------------------
# Image comparison
# ---------------------------------------------------------------------------

def _image_info(reader: EpubReader, internal_path: str) -> ImageInfo:
    ext = posixpath.splitext(internal_path)[1].lower()
    stem = posixpath.splitext(posixpath.basename(internal_path))[0]
    compressed = reader.compressed_size(internal_path)
    uncompressed = reader.uncompressed_size(internal_path)

    width: Optional[int] = None
    height: Optional[int] = None

    if _HAS_PILLOW and ext != ".svg":
        try:
            img = _PilImage.open(reader.open_image_bytes(internal_path))
            width, height = img.size
            ext = f".{img.format.lower()}" if img.format else ext
        except Exception:
            pass

    return ImageInfo(
        internal_path=internal_path,
        stem=stem,
        extension=ext,
        width=width,
        height=height,
        compressed_size=compressed,
        uncompressed_size=uncompressed,
    )


def _diff_images(orig: EpubReader, cmp: EpubReader, result: DiffResult):
    def image_files(reader: EpubReader) -> dict[str, str]:
        """stem → internal_path for image files."""
        m: dict[str, str] = {}
        for f in reader.list_files():
            ext = posixpath.splitext(f)[1].lower()
            if ext in _IMAGE_EXTS:
                stem = posixpath.splitext(posixpath.basename(f))[0]
                m.setdefault(stem, f)
        return m

    orig_images = image_files(orig)
    cmp_images  = image_files(cmp)

    all_stems = sorted(set(orig_images) | set(cmp_images))

    for stem in all_stems:
        orig_path = orig_images.get(stem)
        cmp_path  = cmp_images.get(stem)

        if orig_path and cmp_path:
            orig_info = _image_info(orig, orig_path)
            cmp_info  = _image_info(cmp, cmp_path)
            kind = DiffKind.FORMAT_CHANGED if orig_info.extension != cmp_info.extension else DiffKind.CHANGED
            result.image_diffs.append(ImageDiff(
                kind=kind,
                stem=stem,
                original=orig_info,
                compared=cmp_info,
            ))
        elif orig_path:
            result.image_diffs.append(ImageDiff(
                kind=DiffKind.REMOVED,
                stem=stem,
                original=_image_info(orig, orig_path),
            ))
        else:
            assert cmp_path
            result.image_diffs.append(ImageDiff(
                kind=DiffKind.ADDED,
                stem=stem,
                compared=_image_info(cmp, cmp_path),
            ))


# ---------------------------------------------------------------------------
# Metadata diff
# ---------------------------------------------------------------------------

def _diff_metadata(orig: EpubReader, cmp: EpubReader, result: DiffResult):
    orig_meta = orig.get_metadata()
    cmp_meta  = cmp.get_metadata()
    all_fields = sorted(set(orig_meta) | set(cmp_meta))
    for field in all_fields:
        orig_vals = orig_meta.get(field, [])
        cmp_vals  = cmp_meta.get(field, [])
        orig_str  = "; ".join(orig_vals)
        cmp_str   = "; ".join(cmp_vals)
        if orig_str != cmp_str:
            result.metadata_diffs.append(MetadataDiff(
                field=field,
                original_value=orig_str or None,
                compared_value=cmp_str or None,
            ))


# ---------------------------------------------------------------------------
# CSS / XHTML text diff
# ---------------------------------------------------------------------------

def _extract_text(data: bytes) -> str:
    """Extract plain text from XHTML via itertext."""
    try:
        root = ET.fromstring(data)
        return " ".join(root.itertext()).split()
        # join with newlines to make diff readable
    except ET.ParseError:
        return data.decode("utf-8", errors="replace")

    # actually return line-by-line text
    try:
        root = ET.fromstring(data)
        words = list(root.itertext())
        return "\n".join("".join(words).split("\n"))
    except ET.ParseError:
        return data.decode("utf-8", errors="replace")


def _text_lines(data: bytes, use_text_extract: bool) -> list[str]:
    if use_text_extract:
        try:
            root = ET.fromstring(data)
            text = "".join(root.itertext())
            return [line + "\n" for line in text.splitlines()]
        except ET.ParseError:
            pass
    return data.decode("utf-8", errors="replace").splitlines(keepends=True)


def _diff_text_files(
    orig: EpubReader,
    cmp: EpubReader,
    result: DiffResult,
    media_type: str,
    target: list[TextDiff],
    use_text_extract: bool = False,
):
    orig_manifest = orig.get_manifest()
    cmp_manifest  = cmp.get_manifest()

    # Build basename → full_path maps for the given media_type
    def by_basename(manifest: dict) -> dict[str, str]:
        m: dict[str, str] = {}
        for item in manifest.values():
            if item["media-type"] == media_type:
                m[posixpath.basename(item["full_path"])] = item["full_path"]
        return m

    orig_by_name = by_basename(orig_manifest)
    cmp_by_name  = by_basename(cmp_manifest)

    for name in sorted(set(orig_by_name) & set(cmp_by_name)):
        orig_path = orig_by_name[name]
        cmp_path  = cmp_by_name[name]
        if not orig.has_file(orig_path) or not cmp.has_file(cmp_path):
            continue
        orig_lines = _text_lines(orig.read_file(orig_path), use_text_extract)
        cmp_lines  = _text_lines(cmp.read_file(cmp_path),  use_text_extract)
        if orig_lines == cmp_lines:
            continue
        diff_lines = list(difflib.unified_diff(
            orig_lines, cmp_lines,
            fromfile=f"original/{name}",
            tofile=f"compared/{name}",
            n=2,
        ))
        if diff_lines:
            target.append(TextDiff(file_path=name, unified_diff=diff_lines))
