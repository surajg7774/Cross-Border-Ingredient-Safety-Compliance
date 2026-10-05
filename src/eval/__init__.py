"""Eval stage: scoring functions for the pipeline's other stages, read from
their already-written output files. No API calls, no file I/O in here --
the scripts under scripts/ own reading golden/output files and printing.
"""
