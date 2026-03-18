Todo funciona. Resumen de lo creado:

Estructura del proyecto:


compare-epub/
├── epub_comparator.py          ← CLI entry point
├── requirements.txt         ← rich, Pillow
└── epub_compare/
    ├── models.py            ← dataclasses (EpubSource, BookTriplet, ImageInfo, etc.)
    ├── epub_reader.py       ← wrapper ZIP unificado + parsing OPF/NCX/manifest
    ├── discovery.py         ← escanea directorios y hace match por nombre canónico
    ├── validator.py         ← 7 reglas EPUB spec
    ├── differ.py            ← diff completo de ficheros, imágenes, metadata, CSS/XHTML
    └── reporter.py          ← rich tables + export HTML/JSON
Comandos disponibles:


# Listar todos los libros con tamaños de contenedor
python epub_comparator.py list

# Validar un libro o todos
python epub_comparator.py validate "Chandler"
python epub_comparator.py validate --all --errors-only

# Comparar versiones (resumen o detalle completo con diffs CSS/XHTML)
python epub_comparator.py diff "Chandler" --version web
python epub_comparator.py diff "Dicker" --version both --detail full
python epub_comparator.py diff --all --version integrate

# Reporte completo HTML (+ opcional JSON)
python epub_comparator.py report --output report.html --json report.json
Lo que detecta la comparación de imágenes:

Match por stem (sin extensión) → PNG→JPG se muestra como format_changed, no como añadida/eliminada
Formato, dimensiones (vía Pillow), tamaño comprimido, tamaño real y delta