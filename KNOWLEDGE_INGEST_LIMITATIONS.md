# Knowledge Ingest Limitations

## Current PDF Ingest Scope

The current PDF ingestion path is intentionally shallow.

What it does:

- reads raw PDF bytes
- walks simple PDF page objects and follows their content streams when possible
- extracts simple text streams
- attempts `FlateDecode` decompression when obvious
- preserves page-numbered chunks when page objects are recoverable
- detects short page-leading headings when the text stream is simple enough

What it does not do yet:

- full PDF object graph parsing
- font decoding for all encodings
- layout-faithful reading order
- table extraction
- image/OCR extraction
- reliable handling of scanned PDFs
- reliable recovery when a page uses complex nested content streams, XObjects, or uncommon encodings

Practical implication:

- PDF ingest is suitable for simple text-first validation and prototype rule queries
- page references are now more reliable for simple rulebook-like PDFs, but still not production-grade
- PDF ingest is not yet reliable enough for production-quality rulebook parsing

## Character Sheet Import Scope

Currently supported:

- JSON character sheet import
- CSV character sheet import
- XLSX character sheet import from the first worksheet using a simple tabular `section` / `field` / `value` layout

Not yet supported:

- OCR-based sheet import
- handwritten sheets
- arbitrary workbook layouts, merged cells, formulas, or styled sheet parsing
