"""Text input stage: turns pasted/typed ingredients text into the same
ExtractionResult shape the image pipeline (src/extractors/) produces, so
every downstream stage (resolve, category) works unchanged regardless of
where the extraction came from.
"""
