"""Discover and match EPUB files across version directories.

Directory names and display labels are configurable via epub_dirs.json in the
project root. Example:

    {
      "library_dir": "epubs",
      "original":           {"dir": "original",   "label": "Original"},
      "web_optimized":      {"dir": "kindle",     "label": "Kindle"},
      "integrate_optimized":{"dir": "integrated", "label": "Integrated"}
    }

- "library_dir": subdirectory under the project root that contains all version
  folders. Defaults to "epubs". Set to "." to use the project root directly.
- Any version key can be omitted to keep its default value.
"""
from __future__ import annotations
import json
import re
from pathlib import Path
from typing import Optional

from .models import BookTriplet, EpubSource, VersionLabel

# --- Defaults ------------------------------------------------------------------

_DEFAULT_LIBRARY_DIR = "epubs"

_DEFAULTS: dict[str, dict] = {
    "original":            {"dir": "original_epubs",           "label": "Original"},
    "web_optimized":       {"dir": "web-optimized_epubs",      "label": "Web-Optimized"},
    "integrate_optimized": {"dir": "integrate-optimized_epub", "label": "Integrate-Optimized"},
}

_KEY_TO_LABEL = {
    "original":            VersionLabel.ORIGINAL,
    "web_optimized":       VersionLabel.WEB_OPTIMIZED,
    "integrate_optimized": VersionLabel.INTEGRATE_OPTIMIZED,
}

# Populated by discover(); read by reporter to get current display names.
ACTIVE_DIRS:   dict[VersionLabel, Path] = {}
ACTIVE_LABELS: dict[VersionLabel, str]  = {}

# Suffixes stripped from stems to produce variant filenames
_OPT_SUFFIXES = ["_optimized"]


# --- Config loading ------------------------------------------------------------

def load_config(root: Path) -> tuple[Path, dict[str, dict]]:
    """Load epub_dirs.json (if present) and return (library_base, version_cfg).

    library_base is the directory that contains the version sub-folders.
    """
    cfg = {k: dict(v) for k, v in _DEFAULTS.items()}
    library_dir = _DEFAULT_LIBRARY_DIR
    config_path = root / "epub_dirs.json"
    if config_path.exists():
        try:
            overrides = json.loads(config_path.read_text(encoding="utf-8"))
            if "library_dir" in overrides:
                library_dir = overrides.pop("library_dir")
            for key, values in overrides.items():
                if key in cfg:
                    cfg[key].update(values)
        except (json.JSONDecodeError, OSError):
            pass
    library_base = root / library_dir
    return library_base, cfg


# --- Name normalisation -------------------------------------------------------

def _canonical(stem: str, opt_suffixes: list[str]) -> str:
    """Strip known version suffixes to get the canonical book name."""
    stem = re.sub(r"\s+-\s+integrate-optimized$", "", stem)
    stem = re.sub(r"\s+-\s+original$", "", stem)
    for suffix in opt_suffixes:
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    return stem.strip()


def _scan_dir(directory: Path, label: VersionLabel, opt_suffixes: list[str]) -> dict[str, EpubSource]:
    """Return {canonical_name: EpubSource} for all .epub files in directory."""
    result: dict[str, EpubSource] = {}
    if not directory.is_dir():
        return result
    for epub_path in sorted(directory.glob("*.epub")):
        canon = _canonical(epub_path.stem, opt_suffixes)
        result[canon] = EpubSource(
            label=label,
            path=epub_path,
            file_size=epub_path.stat().st_size,
        )
    return result


# --- Discovery ----------------------------------------------------------------

def discover(root: Path) -> list[BookTriplet]:
    """
    Scan version directories under *root* and return a sorted list of
    BookTriplet objects. Updates module-level ACTIVE_DIRS and ACTIVE_LABELS.
    """
    library_base, cfg = load_config(root)

    ACTIVE_DIRS.clear()
    ACTIVE_LABELS.clear()

    # Collect all configured suffixes (for normalisation)
    opt_suffixes = list(_OPT_SUFFIXES)

    scanned: dict[VersionLabel, dict[str, EpubSource]] = {}
    for key, label in _KEY_TO_LABEL.items():
        entry    = cfg[key]
        dir_path = library_base / entry["dir"]
        ACTIVE_DIRS[label]   = dir_path
        ACTIVE_LABELS[label] = entry["label"]
        scanned[label] = _scan_dir(dir_path, label, opt_suffixes)

    originals   = scanned[VersionLabel.ORIGINAL]
    web_opts    = scanned[VersionLabel.WEB_OPTIMIZED]
    intg_opts   = scanned[VersionLabel.INTEGRATE_OPTIMIZED]

    all_names = sorted(set(originals) | set(web_opts) | set(intg_opts))

    return [
        BookTriplet(
            canonical_name=name,
            original=originals.get(name),
            web_optimized=web_opts.get(name),
            integrate_optimized=intg_opts.get(name),
        )
        for name in all_names
    ]


# --- Search ------------------------------------------------------------------

def find_triplet(triplets: list[BookTriplet], query: str) -> Optional[BookTriplet]:
    """Case-insensitive substring search over canonical names."""
    q = query.lower()
    matches = [t for t in triplets if q in t.canonical_name.lower()]
    if not matches:
        return None
    exact = [t for t in matches if t.canonical_name.lower() == q]
    return exact[0] if exact else matches[0]


# --- Helpers for reporter -----------------------------------------------------

_LABEL_TO_ATTR = {
    VersionLabel.ORIGINAL:            "original",
    VersionLabel.WEB_OPTIMIZED:       "web_optimized",
    VersionLabel.INTEGRATE_OPTIMIZED: "integrate_optimized",
}


def active_versions(triplets: list[BookTriplet]) -> list[VersionLabel]:
    """Return only the VersionLabels that have at least one book."""
    order = [VersionLabel.ORIGINAL, VersionLabel.WEB_OPTIMIZED, VersionLabel.INTEGRATE_OPTIMIZED]
    return [
        lbl for lbl in order
        if any(getattr(t, _LABEL_TO_ATTR[lbl]) is not None for t in triplets)
    ]
