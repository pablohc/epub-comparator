"""Data models for epub-comparator."""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional


class VersionLabel(str, Enum):
    ORIGINAL = "original"
    WEB_OPTIMIZED = "web-optimized"
    INTEGRATE_OPTIMIZED = "integrate-optimized"


class ValidationStatus(str, Enum):
    OK = "ok"
    WARNING = "warning"
    ERROR = "error"


class DiffKind(str, Enum):
    ADDED = "added"
    REMOVED = "removed"
    CHANGED = "changed"
    FORMAT_CHANGED = "format_changed"   # same stem, different extension (e.g. png→jpg)
    IDENTICAL = "identical"


# ---------------------------------------------------------------------------
# Source / triplet
# ---------------------------------------------------------------------------

@dataclass
class EpubSource:
    """One version of a book (a single .epub file)."""
    label: VersionLabel
    path: Path                      # absolute path to .epub
    file_size: int = 0              # on-disk bytes of the ZIP container


@dataclass
class BookTriplet:
    """The three (or fewer) versions of the same book."""
    canonical_name: str             # normalised book name used for matching
    original: Optional[EpubSource] = None
    web_optimized: Optional[EpubSource] = None
    integrate_optimized: Optional[EpubSource] = None

    def versions(self) -> list[EpubSource]:
        return [v for v in (self.original, self.web_optimized, self.integrate_optimized) if v]

    def is_complete(self) -> bool:
        return all([self.original, self.web_optimized, self.integrate_optimized])


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

@dataclass
class ValidationIssue:
    rule: str
    status: ValidationStatus
    message: str


@dataclass
class ValidationResult:
    source: EpubSource
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def status(self) -> ValidationStatus:
        if any(i.status == ValidationStatus.ERROR for i in self.issues):
            return ValidationStatus.ERROR
        if any(i.status == ValidationStatus.WARNING for i in self.issues):
            return ValidationStatus.WARNING
        return ValidationStatus.OK

    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.status == ValidationStatus.ERROR]

    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.status == ValidationStatus.WARNING]


# ---------------------------------------------------------------------------
# Image info
# ---------------------------------------------------------------------------

@dataclass
class ImageInfo:
    """Metadata for one image inside an EPUB."""
    internal_path: str              # path inside the ZIP
    stem: str                       # filename without extension
    extension: str                  # lowercase, e.g. ".jpg"
    width: Optional[int] = None
    height: Optional[int] = None
    compressed_size: int = 0        # bytes as stored in the ZIP
    uncompressed_size: int = 0      # bytes when decompressed

    @property
    def dimensions(self) -> Optional[str]:
        if self.width and self.height:
            return f"{self.width}×{self.height}"
        return None

    @property
    def compression_ratio(self) -> Optional[float]:
        if self.uncompressed_size:
            return self.compressed_size / self.uncompressed_size
        return None


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------

@dataclass
class FileDiff:
    kind: DiffKind
    path: str                           # canonical internal path
    original_path: Optional[str] = None
    optimized_path: Optional[str] = None


@dataclass
class ImageDiff:
    kind: DiffKind
    stem: str
    original: Optional[ImageInfo] = None
    compared: Optional[ImageInfo] = None    # web-opt or intg-opt image


@dataclass
class MetadataDiff:
    field: str
    original_value: Optional[str]
    compared_value: Optional[str]


@dataclass
class TextDiff:
    file_path: str
    unified_diff: list[str]             # lines of unified diff output


@dataclass
class DiffResult:
    """Full diff between original and one optimized version."""
    triplet: BookTriplet
    compared_label: VersionLabel
    container_sizes: dict[str, int] = field(default_factory=dict)  # label → bytes
    file_diffs: list[FileDiff] = field(default_factory=list)
    image_diffs: list[ImageDiff] = field(default_factory=list)
    metadata_diffs: list[MetadataDiff] = field(default_factory=list)
    css_diffs: list[TextDiff] = field(default_factory=list)
    xhtml_diffs: list[TextDiff] = field(default_factory=list)

    def has_changes(self) -> bool:
        return bool(
            self.file_diffs or self.image_diffs or self.metadata_diffs
            or self.css_diffs or self.xhtml_diffs
        )
