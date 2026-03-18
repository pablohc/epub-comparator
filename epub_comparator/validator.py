"""EPUB spec validation rules.

Rule IDs follow epubcheck naming conventions where applicable:
  PKG_* — ZIP container / file system checks
  OPF_* — Package document (OPF) checks
  RSC_* — Resource / reference checks
  NCX_* — NCX table-of-contents checks
  HTM_* — XHTML content checks
"""
from __future__ import annotations
import posixpath
import re
import unicodedata
import zipfile
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

try:
    from PIL import Image as _PilImage
    _HAS_PILLOW = True
except ImportError:
    _HAS_PILLOW = False

from .epub_reader import EpubReader
from .models import EpubSource, ValidationIssue, ValidationResult, ValidationStatus

_META_INF_PREFIX = "META-INF/"

# Characters disallowed in ZIP entry names per OCF spec (epubcheck PKG_009)
_DISALLOWED_FILENAME_CHARS = re.compile(r'[\\?%*:|"<>]')

# Image extensions → expected Pillow format strings
_IMG_EXT_TO_FORMAT = {
    ".jpg": {"JPEG"}, ".jpeg": {"JPEG"},
    ".png": {"PNG"},
    ".gif": {"GIF"},
    ".webp": {"WEBP"},
    ".svg": None,   # text format, skip Pillow check
}

# BCP 47 language tag: simplified well-formed check (primary-subtag[-subtag]*)
_BCP47_RE = re.compile(r'^[a-zA-Z]{1,8}(-[a-zA-Z0-9]{1,8})*$')

# W3C date/datetime: YYYY, YYYY-MM, YYYY-MM-DD, or full ISO 8601
_W3C_DATE_RE = re.compile(
    r'^\d{4}(-\d{2}(-\d{2}(T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2})?)?)?)?$'
)

# Simple UUID v4 pattern
_UUID_RE = re.compile(
    r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
)


def validate(source: EpubSource) -> ValidationResult:
    result = ValidationResult(source=source)
    try:
        with EpubReader(source.path) as r:
            # PKG — container / ZIP
            _check_mimetype(r, result)
            _check_mimetype_extra_field(r, result)
            _check_filenames(r, result)
            _check_duplicate_entries(r, result)
            # container.xml
            _check_container(r, result)
            # OPF
            _check_manifest(r, result)
            _check_manifest_no_opf(r, result)
            _check_manifest_unique_hrefs(r, result)
            _check_manifest_href_no_fragment(r, result)
            _check_spine(r, result)
            _check_spine_no_duplicates(r, result)
            _check_orphans(r, result)
            _check_unique_identifier(r, result)
            _check_metadata_empty(r, result)
            _check_date_format(r, result)
            _check_language_tags(r, result)
            # RSC — resources
            _check_xhtml(r, result)
            _check_xml_encoding(r, result)
            _check_remote_resources(r, result)
            # image extension vs format
            _check_image_formats(r, result)
            # NCX
            _check_ncx(r, result)
    except zipfile.BadZipFile as exc:
        result.issues.append(ValidationIssue(
            rule="PKG_003",
            status=ValidationStatus.ERROR,
            message=f"Invalid ZIP file: {exc}",
        ))
    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ok(result: ValidationResult, rule: str, message: str = ""):
    result.issues.append(ValidationIssue(rule=rule, status=ValidationStatus.OK, message=message))


def _warn(result: ValidationResult, rule: str, message: str):
    result.issues.append(ValidationIssue(rule=rule, status=ValidationStatus.WARNING, message=message))


def _error(result: ValidationResult, rule: str, message: str):
    result.issues.append(ValidationIssue(rule=rule, status=ValidationStatus.ERROR, message=message))


# ---------------------------------------------------------------------------
# PKG — ZIP / container checks
# ---------------------------------------------------------------------------

def _check_mimetype(r: EpubReader, result: ValidationResult):
    """PKG_006 / PKG_007 — mimetype must be first, uncompressed, correct value."""
    rule = "PKG_006"
    first = r.first_entry_name()
    if first != "mimetype":
        _error(result, rule, f"First ZIP entry must be 'mimetype', got '{first}'")
        return
    rule2 = "PKG_007"
    if r.compress_type("mimetype") != 0:
        _error(result, rule2, "mimetype entry must be stored uncompressed (compress_type=0)")
        return
    value = r.read_file("mimetype").decode("ascii", errors="replace").strip()
    if value != "application/epub+zip":
        _error(result, rule2, f"mimetype value is '{value}', expected 'application/epub+zip'")
        return
    _ok(result, "PKG_007")


def _check_mimetype_extra_field(r: EpubReader, result: ValidationResult):
    """PKG_005 — mimetype ZIP entry must not have an extra field."""
    rule = "PKG_005"
    try:
        info = r.get_zip_info("mimetype")
        if info.extra:
            _error(result, rule, "mimetype ZIP entry has an extra field (not permitted by OCF spec)")
            return
    except KeyError:
        return  # already caught by mimetype check
    _ok(result, rule)


def _check_filenames(r: EpubReader, result: ValidationResult):
    """PKG_009/010/012/016 — filename character and encoding checks."""
    disallowed, spaces, non_ascii, uppercase_ext = [], [], [], []
    for name in r.list_files():
        basename = posixpath.basename(name)
        if _DISALLOWED_FILENAME_CHARS.search(name):
            disallowed.append(name)
        if ' ' in name:
            spaces.append(name)
        try:
            name.encode('ascii')
        except UnicodeEncodeError:
            non_ascii.append(name)
        ext = posixpath.splitext(basename)[1]
        if ext and ext != ext.lower():
            uppercase_ext.append(name)

    for f in disallowed[:5]:
        _error(result, "PKG_009", f"Filename contains disallowed characters: {f}")
    for f in spaces[:5]:
        _warn(result, "PKG_010", f"Filename contains spaces (interoperability issues): {f}")
    for f in non_ascii[:5]:
        _warn(result, "PKG_012", f"Filename contains non-ASCII characters: {f}")
    for f in uppercase_ext[:5]:
        _warn(result, "PKG_016", f"File extension should be lowercase: {f}")

    if not disallowed and not spaces and not non_ascii and not uppercase_ext:
        _ok(result, "PKG_009", "All filenames are valid")


def _check_duplicate_entries(r: EpubReader, result: ValidationResult):
    """OPF_060 — no duplicate ZIP entries after Unicode normalization."""
    rule = "OPF_060"
    names = r.list_files()
    normalized: dict[str, str] = {}
    dups = []
    for name in names:
        nf = unicodedata.normalize("NFC", name)
        if nf in normalized:
            dups.append(f"'{name}' duplicates '{normalized[nf]}'")
        else:
            normalized[nf] = name
    if dups:
        for d in dups:
            _error(result, rule, f"Duplicate ZIP entry after Unicode normalization: {d}")
    else:
        _ok(result, rule, "No duplicate ZIP entries")


# ---------------------------------------------------------------------------
# Container
# ---------------------------------------------------------------------------

def _check_container(r: EpubReader, result: ValidationResult):
    """RSC_002 / RSC_003 / OPF_016 / OPF_017 — container.xml structure."""
    if not r.has_file("META-INF/container.xml"):
        _error(result, "RSC_002", "Missing META-INF/container.xml")
        return
    try:
        root = ET.fromstring(r.read_file("META-INF/container.xml"))
    except ET.ParseError as exc:
        _error(result, "RSC_005", f"META-INF/container.xml is not valid XML: {exc}")
        return
    ns = {"c": "urn:oasis:names:tc:opendocument:xmlns:container"}
    rf = root.find(".//c:rootfile", ns)
    if rf is None:
        _error(result, "RSC_003", "No <rootfile> with media-type application/oebps-package+xml found in container.xml")
        return
    opf_path = rf.get("full-path", "")
    if not opf_path:
        _error(result, "OPF_017", "<rootfile> full-path attribute is empty")
        return
    if not r.has_file(opf_path):
        _error(result, "OPF_002", f"OPF file declared in container.xml not found: '{opf_path}'")
        return
    _ok(result, "RSC_002", f"OPF: {opf_path}")


# ---------------------------------------------------------------------------
# OPF — manifest checks
# ---------------------------------------------------------------------------

def _check_manifest(r: EpubReader, result: ValidationResult):
    """OPF_003 / RSC_001 — all manifest hrefs must resolve to existing files."""
    rule = "OPF_003"
    manifest = r.get_manifest()
    if not manifest:
        _error(result, rule, "OPF manifest is empty or could not be parsed")
        return
    missing = [
        f"{item_id} → {item['full_path']}"
        for item_id, item in manifest.items()
        if not r.has_file(item["full_path"])
    ]
    if missing:
        for m in missing[:10]:
            _error(result, rule, f"Manifest item file not found: {m}")
        if len(missing) > 10:
            _error(result, rule, f"… and {len(missing) - 10} more missing files")
    else:
        _ok(result, rule, f"{len(manifest)} manifest items all present")


def _check_manifest_no_opf(r: EpubReader, result: ValidationResult):
    """OPF_099 — manifest must not list the package document itself."""
    rule = "OPF_099"
    opf_path = r.get_opf_path()
    if not opf_path:
        return
    manifest = r.get_manifest()
    listed = [
        item_id for item_id, item in manifest.items()
        if item["full_path"] == opf_path
    ]
    if listed:
        _error(result, rule, f"OPF manifest lists the package document itself (id={listed[0]})")
    else:
        _ok(result, rule)


def _check_manifest_unique_hrefs(r: EpubReader, result: ValidationResult):
    """OPF_074 — each resource must appear only once in the manifest."""
    rule = "OPF_074"
    manifest = r.get_manifest()
    seen: dict[str, list[str]] = {}
    for item_id, item in manifest.items():
        seen.setdefault(item["full_path"], []).append(item_id)
    dups = {path: ids for path, ids in seen.items() if len(ids) > 1}
    if dups:
        for path, ids in list(dups.items())[:5]:
            _error(result, rule, f"Resource '{path}' declared in multiple manifest items: {ids}")
    else:
        _ok(result, rule)


def _check_manifest_href_no_fragment(r: EpubReader, result: ValidationResult):
    """OPF_091 — manifest item href must not contain a fragment identifier."""
    rule = "OPF_091"
    manifest = r.get_manifest()
    bad = [
        f"{item_id}: {item['href']}"
        for item_id, item in manifest.items()
        if '#' in item["href"]
    ]
    if bad:
        for b in bad[:5]:
            _error(result, rule, f"Manifest href contains fragment identifier: {b}")
    else:
        _ok(result, rule)


# ---------------------------------------------------------------------------
# OPF — spine checks
# ---------------------------------------------------------------------------

def _check_spine(r: EpubReader, result: ValidationResult):
    """OPF_033 / OPF_049 — spine must exist, reference valid manifest items."""
    rule = "OPF_049"
    manifest = r.get_manifest()
    idrefs = r.get_spine_idrefs()
    if not idrefs:
        _warn(result, "OPF_033", "Spine is empty or contains no items")
        return
    bad = [ref for ref in idrefs if ref not in manifest]
    if bad:
        for b in bad[:5]:
            _error(result, rule, f"Spine idref not in manifest: '{b}'")
    else:
        _ok(result, rule, f"{len(idrefs)} spine items valid")


def _check_spine_no_duplicates(r: EpubReader, result: ValidationResult):
    """OPF_034 — spine must not reference the same manifest item more than once."""
    rule = "OPF_034"
    idrefs = r.get_spine_idrefs()
    seen: dict[str, int] = {}
    for ref in idrefs:
        seen[ref] = seen.get(ref, 0) + 1
    dups = {ref: n for ref, n in seen.items() if n > 1}
    if dups:
        for ref, n in list(dups.items())[:5]:
            _error(result, rule, f"Spine references manifest item '{ref}' {n} times")
    else:
        _ok(result, rule)


# ---------------------------------------------------------------------------
# OPF — metadata checks
# ---------------------------------------------------------------------------

def _check_unique_identifier(r: EpubReader, result: ValidationResult):
    """OPF_030 / OPF_048 — package must have unique-identifier pointing to a dc:identifier."""
    opf_path = r.get_opf_path()
    if not opf_path or not r.has_file(opf_path):
        return
    try:
        root = ET.fromstring(r.read_file(opf_path))
    except ET.ParseError:
        return

    uid_attr = root.get("unique-identifier")
    if not uid_attr:
        _error(result, "OPF_048", "Package element is missing required 'unique-identifier' attribute")
        return

    # Find the dc:identifier with that id
    ns = {"dc": "http://purl.org/dc/elements/1.1/"}
    found = root.find(f".//*[@id='{uid_attr}']")
    if found is None:
        _error(result, "OPF_030", f"unique-identifier '{uid_attr}' not found as element id in OPF")
        return

    uid_value = (found.text or "").strip()
    if not uid_value:
        _warn(result, "OPF_055", f"Element with id='{uid_attr}' (unique-identifier) is empty")
        return

    # OPF_085 — validate UUID if it looks like one
    if uid_value.lower().startswith("urn:uuid:"):
        uuid_part = uid_value[9:]
        if not _UUID_RE.match(uuid_part):
            _warn(result, "OPF_085", f"dc:identifier UUID '{uuid_part}' is not a valid UUID")
            return

    _ok(result, "OPF_048", f"unique-identifier '{uid_attr}' → '{uid_value[:60]}'")


def _check_metadata_empty(r: EpubReader, result: ValidationResult):
    """OPF_055 / OPF_072 — metadata elements must not be empty."""
    rule = "OPF_055"
    opf_path = r.get_opf_path()
    if not opf_path or not r.has_file(opf_path):
        return
    try:
        root = ET.fromstring(r.read_file(opf_path))
    except ET.ParseError:
        return

    metadata_el = root.find("{http://www.idpf.org/2007/opf}metadata") or root.find("metadata")
    if metadata_el is None:
        return

    empty_tags = []
    for child in metadata_el:
        tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
        text = (child.text or "").strip()
        if not text and not list(child):  # no text and no child elements
            empty_tags.append(tag)

    if empty_tags:
        for tag in empty_tags[:5]:
            _warn(result, rule, f"Metadata element <{tag}> is empty")
    else:
        _ok(result, rule)


def _check_date_format(r: EpubReader, result: ValidationResult):
    """OPF_053 — dc:date must follow W3C datetime format (YYYY[-MM[-DD[T...]]])."""
    rule = "OPF_053"
    metadata = r.get_metadata()
    dates = metadata.get("date", [])
    bad = [d for d in dates if not _W3C_DATE_RE.match(d.strip())]
    if bad:
        for d in bad:
            _warn(result, rule, f"dc:date value '{d}' does not follow W3C datetime format")
    elif dates:
        _ok(result, rule, f"{len(dates)} dc:date value(s) valid")


def _check_language_tags(r: EpubReader, result: ValidationResult):
    """OPF_092 — dc:language must be a well-formed BCP 47 tag."""
    rule = "OPF_092"
    metadata = r.get_metadata()
    langs = metadata.get("language", [])
    bad = [l for l in langs if not _BCP47_RE.match(l.strip())]
    if bad:
        for l in bad:
            _error(result, rule, f"Language tag '{l}' is not well-formed BCP 47")
    elif langs:
        _ok(result, rule, f"Language: {', '.join(langs)}")


# ---------------------------------------------------------------------------
# OPF — orphan files
# ---------------------------------------------------------------------------

def _check_orphans(r: EpubReader, result: ValidationResult):
    """OPF_003b — files in ZIP not declared in manifest."""
    rule = "orphan_files"
    manifest = r.get_manifest()
    declared_paths = {item["full_path"] for item in manifest.values()}
    orphans = [
        f for f in r.list_files()
        if f != "mimetype" and not f.startswith(_META_INF_PREFIX) and f not in declared_paths
    ]
    if orphans:
        for o in orphans[:10]:
            _warn(result, rule, f"File in ZIP not declared in manifest: {o}")
        if len(orphans) > 10:
            _warn(result, rule, f"… and {len(orphans) - 10} more undeclared files")
    else:
        _ok(result, rule, "No orphan files")


# ---------------------------------------------------------------------------
# RSC — resources / references
# ---------------------------------------------------------------------------

def _check_xhtml(r: EpubReader, result: ValidationResult):
    """RSC_005 / HTM_001 — all XHTML content documents must be well-formed XML."""
    rule = "RSC_005"
    manifest = r.get_manifest()
    bad, count = [], 0
    for item in manifest.values():
        if item["media-type"] != "application/xhtml+xml":
            continue
        full_path = item["full_path"]
        if not r.has_file(full_path):
            continue
        count += 1
        try:
            ET.fromstring(r.read_file(full_path))
        except ET.ParseError as exc:
            bad.append(f"{full_path}: {exc}")
    if bad:
        for b in bad[:5]:
            _error(result, rule, f"XHTML not well-formed — {b}")
        if len(bad) > 5:
            _error(result, rule, f"… and {len(bad) - 5} more malformed XHTML files")
    elif count == 0:
        _warn(result, rule, "No XHTML files found in manifest")
    else:
        _ok(result, rule, f"{count} XHTML files well-formed")


def _check_xml_encoding(r: EpubReader, result: ValidationResult):
    """RSC_027 / RSC_028 — XML and XHTML files must be UTF-8, not UTF-16."""
    rule = "RSC_027"
    manifest = r.get_manifest()
    utf16_files = []
    xml_types = {"application/xhtml+xml", "application/x-dtbncx+xml",
                 "application/oebps-package+xml", "application/xml", "text/xml"}
    for item in manifest.values():
        if item["media-type"] not in xml_types:
            continue
        full_path = item["full_path"]
        if not r.has_file(full_path):
            continue
        raw = r.read_file(full_path)
        # UTF-16 BOM: 0xFF 0xFE (LE) or 0xFE 0xFF (BE)
        if raw[:2] in (b'\xff\xfe', b'\xfe\xff'):
            utf16_files.append(full_path)
    if utf16_files:
        for f in utf16_files[:5]:
            _warn(result, rule, f"XML file encoded as UTF-16 (should be UTF-8): {f}")
    else:
        _ok(result, rule, "All XML files use UTF-8 encoding")


def _check_remote_resources(r: EpubReader, result: ValidationResult):
    """RSC_006 — XHTML content must not reference remote (http/https) resources
    unless the manifest item has the 'remote-resources' property."""
    rule = "RSC_006"
    manifest = r.get_manifest()

    # Collect manifest items that declare remote-resources property
    remote_ok_paths = {
        item["full_path"]
        for item in manifest.values()
        if "remote-resources" in item.get("properties", "")
    }

    remote_refs: list[str] = []
    _URL_ATTR_RE = re.compile(r'''(?:href|src|action|data)\s*=\s*["']((https?|ftp)://[^"']+)["']''', re.IGNORECASE)

    for item in manifest.values():
        if item["media-type"] != "application/xhtml+xml":
            continue
        full_path = item["full_path"]
        if full_path in remote_ok_paths or not r.has_file(full_path):
            continue
        content = r.read_file(full_path).decode("utf-8", errors="replace")
        found = _URL_ATTR_RE.findall(content)
        for url, _ in found[:3]:
            remote_refs.append(f"{full_path}: {url}")

    if remote_refs:
        for ref in remote_refs[:10]:
            _warn(result, rule, f"Remote resource reference (may require 'remote-resources' property): {ref}")
    else:
        _ok(result, rule, "No remote resource references found")


# ---------------------------------------------------------------------------
# Image format checks
# ---------------------------------------------------------------------------

def _check_image_formats(r: EpubReader, result: ValidationResult):
    """PKG_022 — image file extension must match actual file format."""
    if not _HAS_PILLOW:
        return
    rule = "PKG_022"
    manifest = r.get_manifest()
    mismatches = []
    for item in manifest.values():
        if not item["media-type"].startswith("image/"):
            continue
        full_path = item["full_path"]
        ext = posixpath.splitext(full_path)[1].lower()
        expected_formats = _IMG_EXT_TO_FORMAT.get(ext)
        if expected_formats is None:  # SVG or unknown, skip
            continue
        if not r.has_file(full_path):
            continue
        try:
            img = _PilImage.open(r.open_image_bytes(full_path))
            actual_format = img.format or ""
            if actual_format not in expected_formats:
                mismatches.append(f"{full_path}: extension='{ext}', actual format='{actual_format}'")
        except Exception:
            pass  # corrupted image caught elsewhere
    if mismatches:
        for m in mismatches[:5]:
            _error(result, rule, f"Image extension does not match actual format: {m}")
    else:
        _ok(result, rule, "All image extensions match their actual format")


# ---------------------------------------------------------------------------
# NCX checks
# ---------------------------------------------------------------------------

def _check_ncx(r: EpubReader, result: ValidationResult):
    """NCX_001 / NCX_004 / NCX_006 — NCX structure, uid match, empty labels."""
    ncx_path = r.get_ncx_path()
    if not ncx_path:
        _warn(result, "ncx_present", "No NCX file declared in manifest")
        return
    if not r.has_file(ncx_path):
        _error(result, "RSC_001", f"NCX file declared but not present: {ncx_path}")
        return
    try:
        root = ET.fromstring(r.read_file(ncx_path))
    except ET.ParseError as exc:
        _error(result, "RSC_005", f"NCX is not valid XML: {exc}")
        return

    ns = {"ncx": "http://www.daisy.org/z3986/2005/ncx/"}

    # navMap presence
    nav_map = root.find("ncx:navMap", ns) or root.find("navMap")
    if nav_map is None:
        _error(result, "ncx_structure", "NCX missing <navMap>")
        return
    nav_points = nav_map.findall("ncx:navPoint", ns) or nav_map.findall("navPoint")
    if not nav_points:
        _warn(result, "ncx_structure", "NCX <navMap> has no <navPoint> children")
        return

    # NCX_001 — dtb:uid must match OPF unique-identifier value
    uid_meta = root.find(".//ncx:meta[@name='dtb:uid']", ns) or root.find(".//meta[@name='dtb:uid']")
    if uid_meta is not None:
        ncx_uid = uid_meta.get("content", "")
        # NCX_004 — dtb:uid must not have leading/trailing whitespace
        if ncx_uid != ncx_uid.strip():
            _warn(result, "NCX_004", f"NCX dtb:uid has leading/trailing whitespace: '{ncx_uid}'")
        opf_ids = r.get_metadata().get("identifier", [])
        if opf_ids and ncx_uid.strip() not in [i.strip() for i in opf_ids]:
            _warn(result, "NCX_001", f"NCX dtb:uid '{ncx_uid.strip()}' does not match any OPF dc:identifier")

    # NCX_006 — navLabel text elements must not be empty
    empty_labels = []
    for nav_point in root.iter():
        tag = nav_point.tag.split("}")[-1] if "}" in nav_point.tag else nav_point.tag
        if tag == "navLabel":
            text_el = nav_point.find("ncx:text", ns) or nav_point.find("text")
            if text_el is None or not (text_el.text or "").strip():
                empty_labels.append("(navLabel)")
    if empty_labels:
        _warn(result, "NCX_006", f"{len(empty_labels)} empty <navLabel> text elements found in NCX")
    else:
        _ok(result, "ncx_structure", f"NCX valid — {len(nav_points)} top-level navPoints")
