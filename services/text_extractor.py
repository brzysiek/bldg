def extract_text(file_path: str, mime_type: str) -> str:
    """
    Wyodrębnia tekst z pliku. Zwraca string.
    Jeśli nie można wyodrębnić — rzuca ValueError z opisem.
    """
    ext = file_path.lower().rsplit(".", 1)[-1]

    if ext == "pdf":
        import fitz  # PyMuPDF
        doc = fitz.open(file_path)
        text = "\n".join(page.get_text() for page in doc)
        doc.close()
        return text

    elif ext in ("docx",):
        from docx import Document
        doc = Document(file_path)
        return "\n".join(p.text for p in doc.paragraphs if p.text.strip())

    elif ext in ("xlsx", "xls"):
        import openpyxl
        wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
        rows = []
        for sheet in wb.worksheets:
            rows.append(f"=== Arkusz: {sheet.title} ===")
            for row in sheet.iter_rows(values_only=True):
                row_text = "\t".join(str(c) if c is not None else "" for c in row)
                if row_text.strip():
                    rows.append(row_text)
        return "\n".join(rows)

    elif ext in ("txt", "csv", "md"):
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()

    else:
        raise ValueError(f"Ekstrakcja tekstu nie jest obsługiwana dla plików .{ext}")
