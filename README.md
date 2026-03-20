# epub-comparator

CLI tool to compare and validate EPUB files across multiple versions.
Detects image conversions, metadata changes, and structural differences.

## Features

- **List** all books with per-version `.epub` file sizes
- **Validate** EPUB spec compliance (mimetype, container, OPF manifest/spine, NCX, XHTML well-formedness, and more)
- **Diff** files, images, metadata, CSS, and XHTML text across versions
- **Image comparison** by stem — PNG→JPG detected as a format conversion, not add/remove
- **Report** — full HTML report with color-coded issues and image size totals
- **Configurable** — directory names and display labels set via `epub_dirs.json`
- **Adaptive columns** — versions with no books are hidden automatically

## Requirements

```
pip install rich Pillow
```

## Project Structure

```
epub-comparator/
├── epub_comparator.py       # CLI entry point
├── epub_dirs.json           # directory and label configuration
├── requirements.txt
├── epubs/                   # place your EPUB version folders here
│   ├── original/            #   original .epub files
│   └── optimized/           #   optimized .epub files (configurable)
└── epub_comparator/
    ├── models.py            # dataclasses: EpubSource, BookTriplet, ImageInfo, etc.
    ├── epub_reader.py       # unified ZIP wrapper + OPF/NCX/manifest parsing
    ├── discovery.py         # scans directories, matches books by canonical name
    ├── validator.py         # EPUB spec validation rules
    ├── differ.py            # diff engine: files, images, metadata, CSS/XHTML
    └── reporter.py          # rich terminal output + HTML/JSON export
```

## Configuration

Create `epub_dirs.json` in the project root to customize directories and labels:

```json
{
  "library_dir": "epubs",
  "original":            { "dir": "original",      "label": "Original" },
  "web":                 { "dir": "web",  "label": "Web" },
  "optimized": { "dir": "optimized", "label": "Optimized" }
}
```

- `library_dir` — subdirectory containing all version folders (default: `epubs`)
- Omit `web_optimized` or `optimized` to compare only two versions

## Usage

```bash
# List all books with .epub sizes
python epub_comparator.py list

# Validate one book or all
python epub_comparator.py validate "Chandler"
python epub_comparator.py validate --all --errors-only

# Compare versions (summary or full diff with CSS/XHTML)
python epub_comparator.py diff "Chandler" --version web
python epub_comparator.py diff "Dicker" --version both --detail full
python epub_comparator.py diff --all --version optimized

# Generate full HTML report (+ optional JSON)
python epub_comparator.py report --output report.html --json report.json
```

## Image Comparison

Images are matched by stem (filename without extension), so a PNG→JPG conversion
is shown as a format change rather than a separate add/remove pair. Each image row
shows format, dimensions, uncompressed size, and delta. A totals row summarizes
aggregate size and savings across all images.
