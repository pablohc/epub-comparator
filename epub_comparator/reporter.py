"""Terminal output (rich) and JSON/HTML export."""
from __future__ import annotations
import dataclasses
import json
import textwrap
from pathlib import Path
from typing import Optional

try:
    from rich.console import Console
    from rich.table import Table
    from rich import box
    from rich.text import Text
    from rich.rule import Rule
    from rich.panel import Panel
    _HAS_RICH = True
except ImportError:
    _HAS_RICH = False

from .models import (
    BookTriplet, DiffKind, DiffResult, ImageDiff, ValidationResult,
    ValidationStatus, VersionLabel,
)
from . import discovery as _discovery

_KIND_LABEL = {
    "format_changed": "Conv. formato",
    "changed":        "Optimizada",
    "added":          "Añadida",
    "removed":        "Eliminada",
    "identical":      "Sin cambios",
}

_CONSOLE = None


def _console():
    global _CONSOLE
    if _CONSOLE is None:
        if _HAS_RICH:
            import io, sys
            # Force UTF-8 output so Unicode symbols work on Windows cp1252 consoles
            # width=200 prevents rich from dropping columns on narrow terminals
            _CONSOLE = Console(
                file=io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8"),
                highlight=False,
                width=200,
            )
        else:
            _CONSOLE = None
    return _CONSOLE


def _size_str(n: Optional[int]) -> str:
    if n is None:
        return "—"
    return f"{n / 1024:.1f} KB"


def _delta_str(a: Optional[int], b: Optional[int]) -> str:
    """Show b-a delta with sign and percentage."""
    if a is None or b is None:
        return ""
    delta = b - a
    sign = "+" if delta >= 0 else ""
    pct = (delta / a * 100) if a else 0
    delta_kb = f"{sign}{delta / 1024:.1f} KB"
    return f"{delta_kb} ({sign}{pct:.1f}%)"


# ---------------------------------------------------------------------------
# List command
# ---------------------------------------------------------------------------

_ATTR = {
    VersionLabel.ORIGINAL:            "original",
    VersionLabel.WEB_OPTIMIZED:       "web_optimized",
    VersionLabel.INTEGRATE_OPTIMIZED: "integrate_optimized",
}


def _get_src(tri: BookTriplet, lbl: VersionLabel):
    return getattr(tri, _ATTR[lbl])


def print_list(triplets: list[BookTriplet]):
    versions = _discovery.active_versions(triplets)
    labels   = _discovery.ACTIVE_LABELS          # {VersionLabel: display_name}

    # Optimized versions (all except ORIGINAL) — used for green highlight
    opt_versions = [v for v in versions if v != VersionLabel.ORIGINAL]

    c = _console()
    if _HAS_RICH and c:
        t = Table(title="EPUB Library", box=box.SIMPLE_HEAVY, show_lines=False)
        t.add_column("#", style="dim", justify="right", width=4)
        t.add_column("Book", style="bold", min_width=30, no_wrap=False)
        for v in versions:
            t.add_column(labels.get(v, v.value), justify="center", width=12)
        for v in versions:
            t.add_column(f"{labels.get(v, v.value)} size", justify="right", width=12)

        for i, tri in enumerate(triplets, 1):
            # Determine which optimized version is smallest (for green highlight)
            opt_bytes = {v: _get_src(tri, v).file_size for v in opt_versions if _get_src(tri, v)}
            min_bytes = min(opt_bytes.values()) if opt_bytes else None

            presence_cells = [
                "✓" if _get_src(tri, v) else "[red]✗[/red]"
                for v in versions
            ]

            size_cells = []
            for v in versions:
                src = _get_src(tri, v)
                sz = _size_str(src.file_size if src else None)
                # Paint green if this optimized version is the smallest
                if src and v in opt_bytes and opt_bytes[v] == min_bytes and len(opt_bytes) > 1:
                    sz = f"[green]{sz}[/green]"
                size_cells.append(sz)

            t.add_row(str(i), tri.canonical_name, *presence_cells, *size_cells)

        c.print(t)
        if len(versions) == 3:
            complete = sum(1 for t_ in triplets if t_.is_complete())
            c.print(f"[dim]{complete}/{len(triplets)} complete triplets[/dim]")
    else:
        col_names = [labels.get(v, v.value)[:8] for v in versions]
        header_parts = ["#".rjust(3), f"{'Book':<60}"]
        header_parts += [n.center(10) for n in col_names]
        header_parts += [f"{n+' size':>12}" for n in col_names]
        header = "  ".join(header_parts)
        print(header)
        print("-" * len(header))
        for i, tri in enumerate(triplets, 1):
            presence = ["✓" if _get_src(tri, v) else "✗" for v in versions]
            sizes    = [_size_str(_get_src(tri, v).file_size if _get_src(tri, v) else None)
                        for v in versions]
            row = f"{i:>3}  {tri.canonical_name:<60}  "
            row += "  ".join(p.center(10) for p in presence) + "  "
            row += "  ".join(s.rjust(12) for s in sizes)
            print(row)


# ---------------------------------------------------------------------------
# Validation command
# ---------------------------------------------------------------------------

def print_validation(
    grouped: list[list[ValidationResult]],
    errors_only: bool = False,
):
    """Print validation results grouped by book.

    *grouped* is a list of per-book result lists:
        [[orig_result, web_result, intg_result], ...]
    Each inner list may have 1–3 entries depending on which versions exist.
    """
    c = _console()

    for book_results in grouped:
        # Determine the canonical book name from the original (first) result
        book_name = book_results[0].source.path.stem if book_results else ""

        # Filter and check if there is anything to show
        visible = []
        for vr in book_results:
            issues = vr.issues
            if errors_only:
                issues = [i for i in issues if i.status != ValidationStatus.OK]
            visible.append((vr, issues))

        if errors_only and all(not issues for _, issues in visible):
            continue

        if _HAS_RICH and c:
            # ── Book separator ──────────────────────────────────────────────
            c.print()
            c.rule(f"[bold white]{book_name}[/bold white]", style="bright_black")

            for vr, issues in visible:
                if not issues:
                    continue
                is_original = vr.source.label == VersionLabel.ORIGINAL
                disp = _discovery.ACTIVE_LABELS.get(vr.source.label, vr.source.label.value)
                status_color = {"ok": "green", "warning": "yellow", "error": "red"}[vr.status.value]

                # Header line: plain for original, bold+cyan for optimized versions
                if is_original:
                    header = f"[{status_color}]{disp}[/{status_color}]"
                else:
                    header = f"[bold cyan]{disp}[/bold cyan]  [{status_color}]({vr.status.value.upper()})[/{status_color}]"

                c.print(f"  {header}")

                t = Table(box=box.MINIMAL, show_header=False, padding=(0, 1))
                t.add_column("Rule", style="dim", width=26)
                t.add_column("Status", width=9)
                t.add_column("Message")
                for issue in issues:
                    col = {"ok": "green", "warning": "yellow", "error": "red"}[issue.status.value]
                    msg = issue.message
                    if not is_original:
                        msg = f"[bold]{msg}[/bold]"
                    t.add_row(
                        issue.rule,
                        f"[{col}]{issue.status.value.upper()}[/{col}]",
                        msg,
                    )
                c.print(t)
        else:
            print(f"\n{'═' * 70}")
            print(f"  {book_name}")
            print(f"{'═' * 70}")
            for vr, issues in visible:
                if not issues:
                    continue
                is_original = vr.source.label == VersionLabel.ORIGINAL
                disp = _discovery.ACTIVE_LABELS.get(vr.source.label, vr.source.label.value)
                marker = "" if is_original else "*** "
                print(f"\n  {marker}{disp} [{vr.status.value.upper()}]")
                for issue in issues:
                    print(f"    {issue.status.value.upper():8}  {issue.rule:25}  {issue.message}")


# ---------------------------------------------------------------------------
# Diff command
# ---------------------------------------------------------------------------

def print_diff(result: DiffResult, detail: str = "summary"):
    c = _console()
    name  = result.triplet.canonical_name
    orig_lbl = _discovery.ACTIVE_LABELS.get(VersionLabel.ORIGINAL, "Original")
    cmp_lbl  = _discovery.ACTIVE_LABELS.get(result.compared_label, result.compared_label.value)

    if _HAS_RICH and c:
        c.rule(f"[bold]{name}[/bold]  ·  {orig_lbl} vs [cyan]{cmp_lbl}[/cyan]")
    else:
        print(f"\n{'='*70}")
        print(f"  {name}  |  {orig_lbl} vs {cmp_lbl}")
        print(f"{'='*70}")

    _print_container_sizes(result)
    _print_file_diffs(result, detail)
    _print_image_diffs(result, detail)
    _print_metadata_diffs(result)
    if detail == "full":
        _print_text_diffs(result.css_diffs, "CSS diffs")
        _print_text_diffs(result.xhtml_diffs, "XHTML text diffs")


def _print_container_sizes(result: DiffResult):
    c = _console()
    sizes    = result.container_sizes
    orig_size = sizes.get(VersionLabel.ORIGINAL.value)
    cmp_size  = sizes.get(result.compared_label.value)
    delta     = _delta_str(orig_size, cmp_size)
    orig_lbl  = _discovery.ACTIVE_LABELS.get(VersionLabel.ORIGINAL, "Original")
    cmp_lbl   = _discovery.ACTIVE_LABELS.get(result.compared_label, result.compared_label.value)

    if _HAS_RICH and c:
        t = Table(title="Container size", box=box.SIMPLE, show_header=True)
        t.add_column("Version")
        t.add_column("Size", justify="right")
        t.add_column(f"Delta vs {orig_lbl}", justify="right")
        t.add_row(orig_lbl, _size_str(orig_size), "")
        t.add_row(cmp_lbl, _size_str(cmp_size), delta)
        c.print(t)
    else:
        print(f"\nContainer size:")
        print(f"  {orig_lbl} : {_size_str(orig_size)}")
        print(f"  {cmp_lbl:<20}: {_size_str(cmp_size)}  {delta}")


def _print_file_diffs(result: DiffResult, detail: str):
    if not result.file_diffs:
        return
    c = _console()
    diffs = result.file_diffs
    kind_color = {
        DiffKind.ADDED: "green", DiffKind.REMOVED: "red",
        DiffKind.FORMAT_CHANGED: "yellow", DiffKind.CHANGED: "cyan",
    }
    if _HAS_RICH and c:
        t = Table(title=f"File differences ({len(diffs)})", box=box.SIMPLE)
        t.add_column("Change", width=16)
        t.add_column("Original path")
        t.add_column("Compared path")
        for d in diffs:
            col = kind_color.get(d.kind, "white")
            t.add_row(
                f"[{col}]{d.kind.value}[/{col}]",
                d.original_path or d.path,
                d.optimized_path or ("" if d.kind != DiffKind.ADDED else d.path),
            )
        c.print(t)
    else:
        print(f"\nFile differences ({len(diffs)}):")
        for d in diffs:
            print(f"  {d.kind.value:<16}  {d.original_path or d.path}")
            if d.optimized_path:
                print(f"  {'':16}  → {d.optimized_path}")


def _print_image_diffs(result: DiffResult, detail: str):
    if not result.image_diffs:
        return
    c = _console()
    diffs = result.image_diffs

    # Totals
    total_orig = sum(d.original.uncompressed_size for d in diffs if d.original)
    total_cmp  = sum(d.compared.uncompressed_size for d in diffs if d.compared)

    if _HAS_RICH and c:
        t = Table(title=f"Imágenes ({len(diffs)})", box=box.SIMPLE)
        t.add_column("Imagen")
        t.add_column("Transformación", width=16)
        t.add_column("Fmt orig")
        t.add_column("Fmt opt")
        t.add_column("Dims orig")
        t.add_column("Dims opt")
        t.add_column("Peso orig", justify="right")
        t.add_column("Peso opt", justify="right")
        t.add_column("Delta", justify="right")
        for d in diffs:
            orig_fmt  = (d.original.extension if d.original else "—").lstrip(".")
            cmp_fmt   = (d.compared.extension  if d.compared  else "—").lstrip(".")
            orig_dims = d.original.dimensions  if d.original  else "—"
            cmp_dims  = d.compared.dimensions  if d.compared  else "—"
            orig_sz   = _size_str(d.original.uncompressed_size if d.original else None)
            cmp_sz    = _size_str(d.compared.uncompressed_size if d.compared else None)
            delta     = _delta_str(
                d.original.uncompressed_size if d.original else None,
                d.compared.uncompressed_size if d.compared else None,
            )
            kind_col = {
                DiffKind.FORMAT_CHANGED: "yellow",
                DiffKind.REMOVED:        "red",
                DiffKind.ADDED:          "green",
                DiffKind.CHANGED:        "cyan",
                DiffKind.IDENTICAL:      "dim",
            }.get(d.kind, "white")
            kind_lbl = _KIND_LABEL.get(d.kind.value, d.kind.value)
            t.add_row(
                d.stem,
                f"[{kind_col}]{kind_lbl}[/{kind_col}]",
                orig_fmt, cmp_fmt,
                orig_dims or "—", cmp_dims or "—",
                orig_sz, cmp_sz, delta,
            )
        # Totals row
        t.add_section()
        t.add_row(
            "[bold]TOTAL[/bold]", "", "", "", "", "",
            f"[bold]{_size_str(total_orig)}[/bold]",
            f"[bold]{_size_str(total_cmp)}[/bold]",
            f"[bold]{_delta_str(total_orig, total_cmp)}[/bold]",
        )
        c.print(t)
    else:
        print(f"\nImágenes ({len(diffs)}):")
        for d in diffs:
            orig_info = f"{d.original.extension.lstrip('.')}  {d.original.dimensions or '?'}  {_size_str(d.original.uncompressed_size)}" if d.original else "—"
            cmp_info  = f"{d.compared.extension.lstrip('.')}  {d.compared.dimensions or '?'}  {_size_str(d.compared.uncompressed_size)}" if d.compared else "—"
            kind_lbl  = _KIND_LABEL.get(d.kind.value, d.kind.value)
            delta = _delta_str(
                d.original.uncompressed_size if d.original else None,
                d.compared.uncompressed_size if d.compared else None,
            )
            print(f"  [{kind_lbl:<16}] {d.stem}")
            print(f"    original : {orig_info}")
            print(f"    optimized: {cmp_info}  {delta}")
        print(f"  {'TOTAL':<18}  orig={_size_str(total_orig)}  opt={_size_str(total_cmp)}  {_delta_str(total_orig, total_cmp)}")


def _print_metadata_diffs(result: DiffResult):
    if not result.metadata_diffs:
        return
    c = _console()
    if _HAS_RICH and c:
        t = Table(title="Metadata differences", box=box.SIMPLE)
        t.add_column("Field", style="dim")
        t.add_column("Original value")
        t.add_column("Compared value")
        for d in result.metadata_diffs:
            t.add_row(d.field, d.original_value or "[dim]—[/dim]", d.compared_value or "[dim]—[/dim]")
        c.print(t)
    else:
        print(f"\nMetadata differences ({len(result.metadata_diffs)}):")
        for d in result.metadata_diffs:
            print(f"  {d.field}")
            print(f"    original : {d.original_value or '—'}")
            print(f"    compared : {d.compared_value or '—'}")


def _print_text_diffs(diffs, title: str):
    if not diffs:
        return
    c = _console()
    if _HAS_RICH and c:
        c.print(Rule(title))
    else:
        print(f"\n{title}")
        print("-" * len(title))
    for td in diffs:
        header = f"--- {td.file_path}"
        if _HAS_RICH and c:
            c.print(f"[bold]{header}[/bold]")
        else:
            print(f"\n{header}")
        for line in td.unified_diff[:80]:  # cap at 80 lines per file
            line = line.rstrip("\n")
            if _HAS_RICH and c:
                if line.startswith("+"):
                    c.print(f"[green]{line}[/green]")
                elif line.startswith("-"):
                    c.print(f"[red]{line}[/red]")
                elif line.startswith("@@"):
                    c.print(f"[cyan]{line}[/cyan]")
                else:
                    c.print(line)
            else:
                print(line)
        if len(td.unified_diff) > 80:
            remaining = len(td.unified_diff) - 80
            msg = f"… ({remaining} more lines)"
            if _HAS_RICH and c:
                c.print(f"[dim]{msg}[/dim]")
            else:
                print(msg)


# ---------------------------------------------------------------------------
# JSON export
# ---------------------------------------------------------------------------

class _EnhancedEncoder(json.JSONEncoder):
    def default(self, obj):
        if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
            return dataclasses.asdict(obj)
        if hasattr(obj, "value"):  # Enum
            return obj.value
        if hasattr(obj, "__fspath__"):  # Path
            return str(obj)
        return super().default(obj)


def export_json(data: object, output_path: Path):
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, cls=_EnhancedEncoder, ensure_ascii=False, indent=2)
    print(f"JSON exported to {output_path}")


# ---------------------------------------------------------------------------
# PR summary JSON
# ---------------------------------------------------------------------------

def build_pr_summary(
    triplets: list,
    val_grouped: list[list],
    diff_results: list,
) -> dict:
    """Build a compact, agent-friendly JSON for PR description generation.

    Structure integrity logic:
      - Collect all (rule, message) pairs present in the original version.
      - For each optimized version, any issue NOT in that set is "new damage".
      - If no new issues → structure_integrity = "preserved"
      - Otherwise      → structure_integrity = "issues_introduced"
    """
    from datetime import datetime, timezone

    # Index diffs by (canonical_name, compared_label.value)
    diff_index: dict[tuple, object] = {
        (dr.triplet.canonical_name, dr.compared_label.value): dr
        for dr in diff_results
    }

    books = []
    total_saved: dict[str, float] = {}   # label → cumulative saved bytes

    for tri, book_val in zip(triplets, val_grouped):
        # Build original issue fingerprints
        orig_fingerprints: set[tuple[str, str]] = set()
        for vr in book_val:
            if vr.source.label == VersionLabel.ORIGINAL:
                orig_fingerprints = {(i.rule, i.message) for i in vr.issues}
                break

        versions_out = {}
        for vr in book_val:
            lbl = vr.source.label
            if lbl == VersionLabel.ORIGINAL:
                continue

            dr = diff_index.get((tri.canonical_name, lbl.value))
            orig_bytes = dr.container_sizes.get(VersionLabel.ORIGINAL.value) if dr else None
            cmp_bytes  = dr.container_sizes.get(lbl.value) if dr else None

            size_delta_bytes = (cmp_bytes - orig_bytes) if (orig_bytes and cmp_bytes) else None
            size_delta_pct   = round(size_delta_bytes / orig_bytes * 100, 1) if (orig_bytes and size_delta_bytes is not None) else None

            # Images summary
            images_changed = images_format_changed = images_added = images_removed = 0
            images_detail = []
            if dr:
                for d in dr.image_diffs:
                    if d.kind.value == "format_changed":
                        images_format_changed += 1
                    elif d.kind.value == "changed":
                        images_changed += 1
                    elif d.kind.value == "added":
                        images_added += 1
                    elif d.kind.value == "removed":
                        images_removed += 1

                    orig_kb  = round(d.original.uncompressed_size / 1024, 1) if d.original else None
                    cmp_kb   = round(d.compared.uncompressed_size / 1024, 1) if d.compared else None
                    delta_kb = round((cmp_kb - orig_kb), 1) if (orig_kb is not None and cmp_kb is not None) else None
                    images_detail.append({
                        "stem": d.stem,
                        "change": d.kind.value,
                        "original_format":    d.original.extension.lstrip(".") if d.original else None,
                        "optimized_format":   d.compared.extension.lstrip(".")  if d.compared else None,
                        "original_dims":      d.original.dimensions if d.original else None,
                        "optimized_dims":     d.compared.dimensions  if d.compared else None,
                        "original_size_kb":   orig_kb,
                        "optimized_size_kb":  cmp_kb,
                        "size_delta_kb":      delta_kb,
                    })

            # Structure integrity
            new_issues = [
                {"rule": i.rule, "status": i.status.value, "message": i.message}
                for i in vr.issues
                if (i.rule, i.message) not in orig_fingerprints
            ]
            integrity = "preserved" if not new_issues else "issues_introduced"

            entry = {
                "size_original_kb": round(orig_bytes / 1024, 1) if orig_bytes else None,
                "size_optimized_kb": round(cmp_bytes / 1024, 1) if cmp_bytes else None,
                "size_delta_kb": round(size_delta_bytes / 1024, 1) if size_delta_bytes is not None else None,
                "size_delta_pct": size_delta_pct,
                "images_changed": images_changed,
                "images_format_changed": images_format_changed,
                "images_added": images_added,
                "images_removed": images_removed,
                "images_detail": images_detail,
                "structure_integrity": integrity,
                "new_validation_issues": new_issues,
            }
            versions_out[lbl.value] = entry

            # Accumulate totals (only when size data is available)
            if size_delta_bytes is not None:
                total_saved[lbl.value] = total_saved.get(lbl.value, 0) + (-size_delta_bytes)

        books.append({"name": tri.canonical_name, "versions": versions_out})

    # Build totals per label
    totals = {}
    for lbl_val, saved_bytes in total_saved.items():
        orig_total = sum(
            dr.container_sizes.get(VersionLabel.ORIGINAL.value, 0)
            for dr in diff_results
            if dr.compared_label.value == lbl_val
        )
        totals[lbl_val] = {
            "total_size_saved_kb": round(saved_bytes / 1024, 1),
            "total_size_saved_pct": round(saved_bytes / orig_total * 100, 1) if orig_total else None,
            "books_with_integrity_preserved": sum(
                1 for b in books
                if b["versions"].get(lbl_val, {}).get("structure_integrity") == "preserved"
            ),
            "books_with_new_issues": sum(
                1 for b in books
                if b["versions"].get(lbl_val, {}).get("structure_integrity") == "issues_introduced"
            ),
        }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "books_total": len(books),
        "totals_by_version": totals,
        "books": books,
    }


def export_pr_summary(
    triplets: list,
    val_grouped: list[list],
    diff_results: list,
    output_path: Path,
):
    data = build_pr_summary(triplets, val_grouped, diff_results)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"PR summary JSON exported to {output_path}")


# ---------------------------------------------------------------------------
# HTML export
# ---------------------------------------------------------------------------

def export_html(
    triplets: list[BookTriplet],
    validation_grouped: list[list[ValidationResult]],
    diff_results: list[DiffResult],
    output_path: Path,
):
    labels   = _discovery.ACTIVE_LABELS
    versions = _discovery.active_versions(triplets)
    sections: list[str] = []

    # ----- summary table -----
    th_presence = "".join(f"<th>{_esc(labels.get(v, v.value))}</th>" for v in versions)
    th_sizes    = "".join(f"<th>{_esc(labels.get(v, v.value))} size</th>" for v in versions)

    rows = []
    for tri in triplets:
        opt_bytes = {v: _get_src(tri, v).file_size for v in versions
                     if v != VersionLabel.ORIGINAL and _get_src(tri, v)}
        min_bytes = min(opt_bytes.values()) if opt_bytes else None

        presence_cells = "".join(
            f"<td class=\"center\">{'✓' if _get_src(tri, v) else '✗'}</td>"
            for v in versions
        )
        size_cells = ""
        for v in versions:
            src = _get_src(tri, v)
            sz  = _size_str(src.file_size if src else None)
            cls = " class=\"num best\"" if (src and v in opt_bytes and opt_bytes[v] == min_bytes and len(opt_bytes) > 1) else " class=\"num\""
            size_cells += f"<td{cls}>{sz}</td>"

        rows.append(f"<tr><td>{_esc(tri.canonical_name)}</td>{presence_cells}{size_cells}</tr>")

    sections.append(
        "<h2>Library overview</h2>"
        f"<table><thead><tr><th>Book</th>{th_presence}{th_sizes}</tr></thead>"
        "<tbody>" + "\n".join(rows) + "</tbody></table>"
    )

    # ----- validation (grouped by book) -----
    has_issues = any(
        issue.status != ValidationStatus.OK
        for book_results in validation_grouped
        for vr in book_results
        for issue in vr.issues
    )
    if has_issues:
        val_html = ["<h2>Validation issues</h2>"]
        for book_results in validation_grouped:
            # Skip books with no non-OK issues
            book_issues = [
                (vr, [i for i in vr.issues if i.status != ValidationStatus.OK])
                for vr in book_results
            ]
            if not any(issues for _, issues in book_issues):
                continue

            book_name = book_results[0].source.path.stem if book_results else ""
            val_html.append(f"<h3 class=\"book-sep\">{_esc(book_name)}</h3>")
            val_html.append("<table><thead><tr><th>Version</th><th>Rule</th><th>Status</th><th>Message</th></tr></thead><tbody>")

            # Build set of (rule, message) pairs present in Original
            orig_issue_keys = {
                (i.rule, i.message)
                for vr, issues in book_issues
                if vr.source.label == VersionLabel.ORIGINAL
                for i in issues
            }

            for vr, issues in book_issues:
                if not issues:
                    continue
                is_original = vr.source.label == VersionLabel.ORIGINAL
                disp = _esc(vr.source.path.parent.name)
                for issue in issues:
                    iclass = issue.status.value
                    # Bold only if this issue does NOT appear in Original
                    is_new = not is_original and (issue.rule, issue.message) not in orig_issue_keys
                    b, eb = ("<strong>", "</strong>") if is_new else ("", "")
                    val_html.append(
                        f"<tr class=\"{iclass}{'' if is_original else ' opt-row'}\">"
                        f"<td>{b}{disp}{eb}</td>"
                        f"<td>{b}{_esc(issue.rule)}{eb}</td>"
                        f"<td class=\"{iclass}\">{b}{issue.status.value.upper()}{eb}</td>"
                        f"<td>{b}{_esc(issue.message)}{eb}</td></tr>"
                    )
            val_html.append("</tbody></table>")

        sections.append("\n".join(val_html))

    # ----- diffs -----
    for dr in diff_results:
        name = dr.triplet.canonical_name
        label = dr.compared_label.value
        orig_sz  = dr.container_sizes.get(VersionLabel.ORIGINAL.value)
        cmp_sz   = dr.container_sizes.get(dr.compared_label.value)
        delta    = _delta_str(orig_sz, cmp_sz)
        orig_lbl = _discovery.ACTIVE_LABELS.get(VersionLabel.ORIGINAL, "Original")
        cmp_lbl  = _discovery.ACTIVE_LABELS.get(dr.compared_label, dr.compared_label.value)

        parts = [
            f"<h3>{_esc(name)} — {_esc(orig_lbl)} vs {_esc(cmp_lbl)}</h3>",
            f"<p>.EPUB size: {_esc(orig_lbl)} {_esc(_size_str(orig_sz))} → "
            f"{_esc(cmp_lbl)} {_esc(_size_str(cmp_sz))} ({_esc(delta)})</p>",
        ]
        # images
        if dr.image_diffs:
            irows = []
            total_orig_img = sum(d.original.uncompressed_size for d in dr.image_diffs if d.original)
            total_cmp_img  = sum(d.compared.uncompressed_size for d in dr.image_diffs if d.compared)
            for d in dr.image_diffs:
                orig_fmt  = (d.original.extension  if d.original  else "—").lstrip(".")
                cmp_fmt   = (d.compared.extension   if d.compared  else "—").lstrip(".")
                orig_dims = d.original.dimensions  if d.original  else "—"
                cmp_dims  = d.compared.dimensions  if d.compared  else "—"
                orig_sz2  = _size_str(d.original.uncompressed_size  if d.original  else None)
                cmp_sz2   = _size_str(d.compared.uncompressed_size  if d.compared  else None)
                img_delta = _delta_str(
                    d.original.uncompressed_size  if d.original  else None,
                    d.compared.uncompressed_size if d.compared else None,
                )
                klass = {"format_changed": "warning", "removed": "error",
                         "added": "ok", "changed": "info"}.get(d.kind.value, "")
                kind_lbl = _KIND_LABEL.get(d.kind.value, d.kind.value)
                irows.append(
                    f"<tr class=\"{klass}\"><td>{_esc(d.stem)}</td>"
                    f"<td>{_esc(kind_lbl)}</td>"
                    f"<td>{orig_fmt}</td><td>{cmp_fmt}</td>"
                    f"<td>{orig_dims or '—'}</td><td>{cmp_dims or '—'}</td>"
                    f"<td class=\"num\">{orig_sz2}</td>"
                    f"<td class=\"num\"><b>{cmp_sz2}</b></td>"
                    f"<td class=\"num\"><b>{img_delta}</b></td></tr>"
                )
            tfoot = (
                f"<tfoot><tr><td><b>TOTAL</b></td><td></td><td></td><td></td><td></td><td></td>"
                f"<td class=\"num\">{_size_str(total_orig_img)}</td>"
                f"<td class=\"num\"><b>{_size_str(total_cmp_img)}</b></td>"
                f"<td class=\"num\"><b>{_delta_str(total_orig_img, total_cmp_img)}</b></td>"
                f"</tr></tfoot>"
            )
            parts.append(
                f"<table><thead><tr><th>Stem</th><th>Change</th>"
                f"<th>Fmt {_esc(orig_lbl)}</th><th>Fmt {_esc(cmp_lbl)}</th>"
                f"<th>Dims {_esc(orig_lbl)}</th><th>Dims {_esc(cmp_lbl)}</th>"
                f"<th>Size {_esc(orig_lbl)}</th><th>Size {_esc(cmp_lbl)}</th><th>Delta</th></tr></thead>"
                f"<tbody>" + "\n".join(irows) + f"</tbody>{tfoot}</table>"
            )
        # metadata
        if dr.metadata_diffs:
            mrows = []
            for d in dr.metadata_diffs:
                mrows.append(
                    f"<tr><td>{_esc(d.field)}</td>"
                    f"<td>{_esc(d.original_value or '—')}</td>"
                    f"<td>{_esc(d.compared_value or '—')}</td></tr>"
                )
            parts.append(
                "<details><summary>Metadata differences</summary>"
                "<table><thead><tr><th>Field</th><th>Original</th><th>Compared</th>"
                "</tr></thead><tbody>" + "\n".join(mrows) + "</tbody></table></details>"
            )
        sections.append("\n".join(parts))

    html = _HTML_TEMPLATE.format(
        title="epub-comparator report",
        body="\n<hr>\n".join(sections),
    )
    output_path.write_text(html, encoding="utf-8")
    print(f"HTML report exported to {output_path}")


def _esc(s: str) -> str:
    return (s
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))


_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
  body {{ font-family: system-ui, sans-serif; max-width: 1400px; margin: 2rem auto; padding: 0 1rem; }}
  h2 {{ color: #333; border-bottom: 2px solid #ccc; padding-bottom: .3em; }}
  h3 {{ color: #444; margin-top: 1.5em; }}
  table {{ border-collapse: collapse; width: 100%; margin: 1em 0; font-size: .9em; }}
  th, td {{ text-align: left; padding: .35em .7em; border: 1px solid #ddd; }}
  th {{ background: #f0f0f0; }}
  tr.error          td {{ background: #ffe8e8; }}
  tr.warning        td {{ background: #fff8e1; }}
  tr.ok             td {{ background: #e8f5e9; }}
  tr.error.opt-row  td {{ background: #ffb3b3; }}
  tr.warning.opt-row td {{ background: #ffe082; }}
  td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  td.num.best {{ color: #2e7d32; font-weight: bold; }}
  td.center {{ text-align: center; }}
  h3.book-sep {{ margin: 1.6em 0 .3em; padding: .4em .6em; background: #f5f5f5;
                 border-left: 4px solid #555; font-size: 1em; }}
  details {{ margin: .5em 0; }}
  summary {{ cursor: pointer; font-weight: bold; }}
  hr {{ border: none; border-top: 1px solid #eee; margin: 2em 0; }}
</style>
</head>
<body>
<h1>{title}</h1>
{body}
</body>
</html>
"""
