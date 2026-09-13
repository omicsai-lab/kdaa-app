"""Bounded extraction. Binary parsing happens in a separate, time-limited process."""
import hashlib
from importlib.metadata import version as distribution_version
import io
import json
import re
import subprocess
import sys
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import PurePath
from kdaa.domain import NORMALIZATION_VERSION
from kdaa.errors import KDAAError

MAX_BYTES = 5 * 1024 * 1024
MAX_CHARACTERS = 100_000
MAX_PAGES = 100
PARSER_VERSION = "document-extraction-v1"
MEDIA_TYPES = {".txt": "text/plain", ".md": "text/markdown", ".pdf": "application/pdf",
               ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}

@dataclass(frozen=True)
class ParsedDocument:
    text: str
    media_type: str
    warnings: list[str]
    parser_version: str = PARSER_VERSION

def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()

def normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")
    if "\x00" in text:
        raise KDAAError("invalid_text", "The document contains null bytes rather than usable text.")
    text = "\n".join(re.sub(r"[^\S\n]+", " ", line).strip() for line in text.split("\n"))
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        raise KDAAError("empty_document", "No readable text was found. Scanned PDFs need OCR outside this app.")
    if len(text) > MAX_CHARACTERS:
        raise KDAAError("text_too_large", f"Extracted text exceeds {MAX_CHARACTERS:,} characters.", 413)
    if sum(ord(c) < 32 and c not in "\n\t" for c in text):
        raise KDAAError("invalid_text", "The document contains unsupported control characters.")
    return text

def validate_filename(filename: str) -> str:
    if not filename or len(filename) > 180 or any(c in filename for c in '/\\') or any(ord(c) < 32 for c in filename):
        raise KDAAError("invalid_filename", "Use a simple filename without paths or control characters.")
    ext = PurePath(filename).suffix.lower()
    if ext not in MEDIA_TYPES:
        raise KDAAError("unsupported_format", "Supported files: UTF-8 TXT, Markdown, text PDF, and DOCX.", 415)
    return ext

def _binary_extract(content: bytes, ext: str) -> tuple[str, list[str]]:
    if ext == ".pdf":
        from pypdf import PdfReader
        if not content.startswith(b"%PDF-"):
            raise KDAAError("invalid_pdf", "The file does not contain a PDF header.")
        reader = PdfReader(io.BytesIO(content), strict=False)
        if reader.is_encrypted:
            raise KDAAError("encrypted_pdf", "Encrypted PDFs are not supported; use a decrypted copy.")
        if len(reader.pages) > MAX_PAGES:
            raise KDAAError("pdf_too_large", "PDFs are limited to 100 pages.", 413)
        pieces: list[str] = []
        size = 0
        for page in reader.pages:
            part = page.extract_text() or ""
            size += len(part)
            if size > MAX_CHARACTERS:
                raise KDAAError("text_too_large", "Extracted PDF text is too large.", 413)
            pieces.append(part)
        return "\n\n".join(pieces), ["PDF text only; no OCR, layout, or page-coordinate claims."]
    from docx import Document
    from docx.table import Table
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        info = archive.infolist()
        names = set(archive.namelist())
        if len(info) > 2000 or sum(i.file_size for i in info) > 20 * 1024 * 1024:
            raise KDAAError("docx_too_large", "The DOCX archive exceeds safe extraction limits.", 413)
        if not {"[Content_Types].xml", "word/document.xml"} <= names:
            raise KDAAError("invalid_docx", "The ZIP file is not a valid DOCX document.")
        if any("vbaproject" in n.lower() or n.startswith("word/embeddings/") for n in names):
            raise KDAAError("active_docx", "DOCX files with macros or embedded objects are not supported.")
        for item in info:
            if item.file_size > 1024 * 1024 and item.file_size > max(item.compress_size, 1) * 300:
                raise KDAAError("docx_too_large", "The DOCX compression ratio exceeds safe limits.", 413)
    doc = Document(io.BytesIO(content))
    pieces = []
    for block in doc.iter_inner_content():
        if isinstance(block, Table):
            pieces.extend(" | ".join(c.text for c in row.cells) for row in block.rows)
        else:
            pieces.append(block.text)
        if sum(len(p) for p in pieces) > MAX_CHARACTERS:
            raise KDAAError("text_too_large", "Extracted DOCX text is too large.", 413)
    return "\n\n".join(pieces), ["DOCX body paragraphs/tables only; images, comments, headers, and footnotes are not extracted."]

def parse_document(filename: str, content: bytes) -> ParsedDocument:
    ext = validate_filename(filename)
    if not content:
        raise KDAAError("empty_document", "The uploaded file is empty.")
    if len(content) > MAX_BYTES:
        raise KDAAError("file_too_large", "The upload limit is 5 MiB per file.", 413)
    if ext in {".txt", ".md"}:
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise KDAAError("invalid_encoding", "Save text files as UTF-8 before importing.") from exc
        warnings = []
    else:
        try:
            process = subprocess.run([sys.executable, "-m", "kdaa.parsing", ext], input=content,
                                     capture_output=True, timeout=15, check=False)
        except subprocess.TimeoutExpired as exc:
            raise KDAAError("extraction_timeout", "Extraction exceeded 15 seconds; try a smaller document.", 413) from exc
        if process.returncode:
            raise KDAAError("extraction_failed", "The document could not be parsed within resource limits.")
        try:
            result = json.loads(process.stdout)
        except (ValueError, UnicodeError) as exc:
            raise KDAAError("extraction_failed", "The parser did not return valid output.") from exc
        if "error" in result:
            raise KDAAError(**result["error"])
        text, warnings = result["text"], result["warnings"]
    library = {".pdf": "pypdf", ".docx": "python-docx"}.get(ext)
    parser_version = f"{PARSER_VERSION}:{library}=={distribution_version(library)}" if library else f"{PARSER_VERSION}:utf8"
    return ParsedDocument(normalize(text), MEDIA_TYPES[ext], warnings, parser_version=parser_version)

def _worker() -> None:
    # Extra containment on Linux/macOS. Windows still has the subprocess deadline.
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 * 1024, 768 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_CPU, (12, 12))
    except (ImportError, ValueError, OSError):
        pass
    try:
        content = sys.stdin.buffer.read(MAX_BYTES + 1)
        if len(content) > MAX_BYTES:
            raise KDAAError("file_too_large", "The upload limit is 5 MiB.", 413)
        text, warnings = _binary_extract(content, sys.argv[1])
        result = {"text": normalize(text), "warnings": warnings}
    except KDAAError as exc:
        result = {"error": {"code": exc.code, "message": exc.message, "status_code": exc.status_code}}
    except Exception:
        result = {"error": {"code": "invalid_document", "message": "The PDF or DOCX is malformed or unsupported.", "status_code": 400}}
    sys.stdout.write(json.dumps(result, ensure_ascii=True))

if __name__ == "__main__":
    _worker()
