"""Unified I/O abstraction over .epub ZIP files."""
from __future__ import annotations
import io
import posixpath
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

# XML namespaces
_NS = {
    "container": "urn:oasis:names:tc:opendocument:xmlns:container",
    "opf":       "http://www.idpf.org/2007/opf",
    "dc":        "http://purl.org/dc/elements/1.1/",
    "ncx":       "http://www.daisy.org/z3986/2005/ncx/",
}


class EpubReader:
    """Read-only wrapper around a .epub ZIP file."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._zf = zipfile.ZipFile(self.path, "r")
        self._name_set: Optional[set[str]] = None

    def close(self):
        self._zf.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    # ------------------------------------------------------------------
    # Low-level ZIP access
    # ------------------------------------------------------------------

    def list_files(self) -> list[str]:
        """All entry names in the ZIP (files only, no dir entries)."""
        return [n for n in self._zf.namelist() if not n.endswith("/")]

    def _names(self) -> set[str]:
        if self._name_set is None:
            self._name_set = set(self._zf.namelist())
        return self._name_set

    def has_file(self, internal_path: str) -> bool:
        return internal_path in self._names()

    def read_file(self, internal_path: str) -> bytes:
        return self._zf.read(internal_path)

    def read_text(self, internal_path: str, encoding: str = "utf-8") -> str:
        return self.read_file(internal_path).decode(encoding, errors="replace")

    def get_zip_info(self, internal_path: str) -> zipfile.ZipInfo:
        return self._zf.getinfo(internal_path)

    def compressed_size(self, internal_path: str) -> int:
        return self._zf.getinfo(internal_path).compress_size

    def uncompressed_size(self, internal_path: str) -> int:
        return self._zf.getinfo(internal_path).file_size

    def compress_type(self, internal_path: str) -> int:
        return self._zf.getinfo(internal_path).compress_type

    def first_entry_name(self) -> str:
        """Name of the first entry in the ZIP (for mimetype check)."""
        return self._zf.namelist()[0]

    # ------------------------------------------------------------------
    # EPUB structure
    # ------------------------------------------------------------------

    def get_opf_path(self) -> Optional[str]:
        """Parse META-INF/container.xml and return the OPF path."""
        if not self.has_file("META-INF/container.xml"):
            return None
        try:
            root = ET.fromstring(self.read_file("META-INF/container.xml"))
            rf = root.find(".//container:rootfile", _NS)
            if rf is not None:
                return rf.get("full-path")
        except ET.ParseError:
            pass
        return None

    def parse_opf(self) -> Optional[ET.Element]:
        opf_path = self.get_opf_path()
        if not opf_path or not self.has_file(opf_path):
            return None
        try:
            return ET.fromstring(self.read_file(opf_path))
        except ET.ParseError:
            return None

    def get_metadata(self) -> dict[str, list[str]]:
        """Return DC metadata fields as {field_name: [value, ...]}."""
        root = self.parse_opf()
        if root is None:
            return {}
        meta: dict[str, list[str]] = {}
        metadata_el = root.find("opf:metadata", _NS)
        if metadata_el is None:
            # try without namespace
            metadata_el = root.find("metadata")
        if metadata_el is None:
            return {}
        for child in metadata_el:
            local = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            text = (child.text or "").strip()
            if text:
                meta.setdefault(local, []).append(text)
        return meta

    def get_manifest(self) -> dict[str, dict]:
        """Return manifest items keyed by id: {id: {href, media-type}}."""
        root = self.parse_opf()
        if root is None:
            return {}
        opf_path = self.get_opf_path() or ""
        opf_dir = posixpath.dirname(opf_path)
        manifest: dict[str, dict] = {}
        manifest_el = root.find("opf:manifest", _NS)
        if manifest_el is None:
            manifest_el = root.find("manifest")
        if manifest_el is None:
            return {}
        for item in manifest_el:
            item_id = item.get("id", "")
            href = item.get("href", "")
            media_type = item.get("media-type", "")
            # resolve href relative to OPF location
            full_path = posixpath.normpath(posixpath.join(opf_dir, href)) if opf_dir else href
            manifest[item_id] = {
                "href": href,
                "full_path": full_path,
                "media-type": media_type,
                "properties": item.get("properties", ""),
            }
        return manifest

    def get_spine_idrefs(self) -> list[str]:
        """Return ordered list of idref values from the spine."""
        root = self.parse_opf()
        if root is None:
            return []
        spine_el = root.find("opf:spine", _NS)
        if spine_el is None:
            spine_el = root.find("spine")
        if spine_el is None:
            return []
        return [item.get("idref", "") for item in spine_el if item.get("idref")]

    def get_ncx_path(self) -> Optional[str]:
        """Find the NCX file path from the manifest."""
        for item in self.get_manifest().values():
            if item["media-type"] == "application/x-dtbncx+xml":
                return item["full_path"]
        return None

    def parse_ncx(self) -> Optional[ET.Element]:
        ncx_path = self.get_ncx_path()
        if not ncx_path or not self.has_file(ncx_path):
            return None
        try:
            return ET.fromstring(self.read_file(ncx_path))
        except ET.ParseError:
            return None

    # ------------------------------------------------------------------
    # Image helper
    # ------------------------------------------------------------------

    def open_image_bytes(self, internal_path: str) -> io.BytesIO:
        return io.BytesIO(self.read_file(internal_path))
