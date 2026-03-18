"""Alias for epub_comparator.py — kept for backwards compatibility."""
import runpy, sys
sys.argv[0] = "epub_comparator.py"
runpy.run_path("epub_comparator.py", run_name="__main__")
