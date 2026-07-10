from pathlib import Path

from friday.memory.index import FileIndex

# A minimal but valid single-page PDF whose page shows the words
# "apartment lease agreement" via a Tj text operator.
_PDF_STREAM = b"BT /F1 12 Tf 72 720 Td (apartment lease agreement) Tj ET"


def _write_pdf(path: Path) -> None:
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(_PDF_STREAM), _PDF_STREAM),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (i, body)
    xref_at = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref_at,
    )
    path.write_bytes(bytes(out))


def test_pdf_content_is_searchable(tmp_path: Path):
    root = tmp_path / "docs"
    root.mkdir()
    _write_pdf(root / "lease_2026.pdf")

    index = FileIndex(tmp_path / "friday.db", roots=[root], denied=[])
    index.refresh()
    hits = index.search("apartment lease")
    assert len(hits) == 1
    assert hits[0].path.endswith("lease_2026.pdf")


def test_docx_content_is_searchable(tmp_path: Path):
    import docx

    root = tmp_path / "docs"
    root.mkdir()
    document = docx.Document()
    document.add_paragraph("Quarterly budget review: marketing spend up nine percent.")
    document.save(str(root / "budget.docx"))

    index = FileIndex(tmp_path / "friday.db", roots=[root], denied=[])
    index.refresh()
    hits = index.search("marketing spend")
    assert len(hits) == 1
    assert hits[0].path.endswith("budget.docx")


def test_corrupt_document_is_skipped_not_fatal(tmp_path: Path):
    root = tmp_path / "docs"
    root.mkdir()
    (root / "broken.pdf").write_bytes(b"not really a pdf")
    (root / "fine.txt").write_text("healthy file")

    index = FileIndex(tmp_path / "friday.db", roots=[root], denied=[])
    index.refresh()  # must not raise
    assert len(index.search("healthy")) == 1
