"""
RAG Indexer Library v2.3.0 — Orchestra 8GB
==========================================
Estrae testo da file di vari formati e li suddivide in chunk.
Per i file Python (.py) usa chunking AST che produce un chunk per ogni
funzione/metodo, con metadati (function_name, start_line, end_line, type).
Per i file PDF usa chunking per pagina con metadati (pdf_page, pdf_title,
pdf_author, pdf_total_pages) e supporto opzionale a pdfplumber per tabelle.
Formati supportati: .md .txt .py .sh .json .pdf .docx .xlsx
Dipendenze obbligatorie: nessuna
Dipendenze opzionali: pypdf  pdfplumber  python-docx  openpyxl  pytesseract

CHANGELOG v2.3.0 rispetto a v2.2.0:
  OPT-01  scan_file(file_path, docs_root): processa un singolo file senza
          riscansire tutta la directory. Usato da _run_index_job per passare
          da O(n²) a O(n): prima ogni file triggerava scan_directory() intera.

CHANGELOG v2.2.0 rispetto a v2.1.0:
  PDF-1  Chunking per pagina: ogni pagina PDF diventa 1+ chunk con metadati
         (pdf_page, pdf_total_pages, pdf_title, pdf_author).
         Prima: testo estratto flat → chunking a 500 char → contesto pagina perso.

  PDF-2  Livello 2 — pdfplumber: se installato, sostituisce pypdf per l'estrazione
         di testo e tabelle con layout preservato. Attivazione automatica.

  PDF-3  Livello 3 — OCR stub: se pypdf e pdfplumber restituiscono testo vuoto
         (PDF scansionato), il chunk contiene un placeholder leggibile invece di
         sparire silenziosamente. OCR reale via pytesseract: flag USE_OCR=True
         nella sezione configurazione (richiede tesseract-ocr nel sistema).

  PDF-4  Gestione errori differenziata: PDF protetti da password → messaggio
         specifico; PDF corrotti/troncati → log con tipo eccezione distinto.

CHANGELOG v2.1.0 rispetto a v2.0.0:
  BUG-01/02/03/04/05: vedi changelog precedente.
"""

import ast
import json
import re
import uuid
from pathlib import Path
from typing import Iterator

# ─────────────────────────────────────────────────────────────────────────────
# Import opzionali — il servizio parte anche senza, con avvisi
# ─────────────────────────────────────────────────────────────────────────────
try:
    import pypdf
    _PYPDF_OK = True
except ImportError:
    _PYPDF_OK = False

try:
    import pdfplumber as _pdfplumber
    _PDFPLUMBER_OK = True
except ImportError:
    _PDFPLUMBER_OK = False

# Flag OCR: metti True per abilitare pytesseract su PDF scansionati.
# Richiede: pip install pytesseract pdf2image  +  apt install tesseract-ocr
USE_OCR = False
try:
    import pytesseract as _pytesseract
    from pdf2image import convert_from_path as _pdf2image
    _OCR_OK = True
except ImportError:
    _OCR_OK = False

try:
    import docx as _docx
    _DOCX_OK = True
except ImportError:
    _DOCX_OK = False

try:
    import openpyxl as _openpyxl
    _XLSX_OK = True
except ImportError:
    _XLSX_OK = False

# ─────────────────────────────────────────────────────────────────────────────
# Parametri chunking
# ─────────────────────────────────────────────────────────────────────────────
_CHARS_PER_TOKEN = 4
_CHUNK_TOKENS    = 512
_OVERLAP_TOKENS  = 64
CHUNK_CHARS      = _CHUNK_TOKENS  * _CHARS_PER_TOKEN   # 2048 caratteri
OVERLAP_CHARS    = _OVERLAP_TOKENS * _CHARS_PER_TOKEN  # 256 caratteri

_SUB_CHUNK_LINES = 80   # soglia righe per sub-chunking AST
_SUB_OVERLAP     = 10   # overlap righe tra sotto-chunk

SUPPORTED_EXT = frozenset({
    ".md", ".txt", ".py", ".sh", ".json",
    ".pdf", ".docx", ".xlsx"
})


# =============================================================================
# ESTRAZIONE TESTO
# =============================================================================

def extract_text(file_path: Path) -> str:
    """
    Estrae testo grezzo da un file in base all'estensione.
    Ritorna stringa vuota se il formato non è supportato o se c'è errore.
    """
    ext = file_path.suffix.lower()
    try:
        if ext in (".md", ".txt", ".py", ".sh"):
            return file_path.read_text(encoding="utf-8", errors="replace")

        if ext == ".json":
            raw  = file_path.read_text(encoding="utf-8", errors="replace")
            data = json.loads(raw)
            return json.dumps(data, ensure_ascii=False, indent=2)

        if ext == ".pdf":
            # extract_text() per PDF ritorna solo il testo grezzo concatenato.
            # Per chunking di qualità usa chunk_pdf_file() direttamente.
            pages_text = _extract_pdf_pages_text(file_path)
            return "\n\n".join(t for _, t in pages_text if t.strip())

        if ext == ".docx":
            if not _DOCX_OK:
                return f"[DOCX: python-docx non installato — {file_path.name}]"
            doc  = _docx.Document(str(file_path))
            pars = [p.text for p in doc.paragraphs if p.text.strip()]
            return "\n\n".join(pars)

        if ext == ".xlsx":
            if not _XLSX_OK:
                return f"[XLSX: openpyxl non installato — {file_path.name}]"
            wb     = _openpyxl.load_workbook(
                str(file_path), read_only=True, data_only=True
            )
            sheets = []
            for name in wb.sheetnames:
                ws   = wb[name]
                rows = []
                for row in ws.iter_rows(values_only=True):
                    cells = [str(c) for c in row if c is not None and str(c).strip()]
                    if cells:
                        rows.append(" | ".join(cells))
                if rows:
                    sheets.append(f"[Foglio: {name}]\n" + "\n".join(rows))
            wb.close()
            return "\n\n".join(sheets)

    except Exception as e:
        return f"[Errore estrazione {file_path.name}: {type(e).__name__}: {e}]"

    return ""


# =============================================================================
# CHUNKING STANDARD (per file non-Python)
# =============================================================================

def chunk_text(
    text:   str,
    source: str,
    domain: str,
    path:   str,
) -> list[dict]:
    """
    Divide il testo in chunk con overlap.
    Ogni chunk è un dict pronto per l'upsert in Qdrant.

    BUG-03 FIX: aggiunto `if end >= n: break` dopo aver accodato ogni chunk.
    Impedisce la produzione di un chunk finale spurio contenente solo
    i caratteri di overlap già presenti nel chunk precedente.
    """
    if not text.strip():
        return []

    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{3,}", " ", text)

    chunks: list[dict] = []
    start = 0
    idx   = 0
    n     = len(text)

    while start < n:
        end = min(start + CHUNK_CHARS, n)

        # Cerca punto di taglio naturale (paragrafo → riga → spazio)
        if end < n:
            half = start + CHUNK_CHARS // 2
            bp = text.rfind("\n\n", half, end)
            if bp > half:
                end = bp + 2
            else:
                bp = text.rfind("\n", half, end)
                if bp > half:
                    end = bp + 1
                else:
                    bp = text.rfind(" ", half, end)
                    if bp > half:
                        end = bp + 1

        chunk_content = text[start:end].strip()
        if chunk_content:
            doc_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{path}::{idx}"))
            chunks.append({
                "text":      chunk_content,
                "domain":    domain,
                "source":    source,
                "path":      path,
                "chunk_idx": idx,
                "doc_id":    doc_id,
            })
            idx += 1

        # BUG-03 FIX: interrompi se abbiamo raggiunto la fine del testo.
        # Senza questo check, il loop proseguiva producendo un chunk spurio
        # con i soli caratteri di overlap già presenti nel chunk precedente.
        if end >= n:
            break

        new_start = end - OVERLAP_CHARS
        if new_start <= start:
            break
        start = new_start

    return chunks


# =============================================================================
# AST CHUNKING PER FILE PYTHON
# =============================================================================

def _sub_chunk(
    all_lines:   list[str],
    start_line0: int,         # 0-based, riga di inizio del nodo nel file
    end_line0:   int,         # 0-based esclusivo
    name:        str,
    node_type:   str,
    path:        str,
    domain:      str,
    source:      str,
    chunks:      list[dict],
    idx_ref:     list[int],   # [idx] mutabile per aggiornamento nonlocal
) -> None:
    """
    Divide un blocco di righe (funzione o metodo) in sotto-chunk da
    _SUB_CHUNK_LINES righe con overlap di _SUB_OVERLAP righe.

    BUG-02 FIX: la condizione di uscita è `if sub_end >= len(node_lines): break`
    (testato DOPO aver accodato il sotto-chunk). Prima: `pos = sub_end - 10`
    con guard `pos >= len-5` non avanzava mai per funzioni 81–N righe,
    causando infinite loop.
    """
    node_lines = all_lines[start_line0:end_line0]
    total      = len(node_lines)
    pos        = 0
    sub_idx    = 0

    while pos < total:
        sub_end = min(pos + _SUB_CHUNK_LINES, total)

        # Cerca un punto di taglio naturale (riga vuota) nell'ultima parte
        if sub_end < total:
            for lookback in range(1, min(20, sub_end - pos)):
                if node_lines[sub_end - lookback].strip() == "":
                    sub_end = sub_end - lookback + 1
                    break

        sub_code = "\n".join(node_lines[pos:sub_end]).strip()
        if sub_code:
            doc_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{path}::{idx_ref[0]}"))
            chunks.append({
                "text":          sub_code,
                "domain":        domain,
                "source":        source,
                "path":          path,
                "chunk_idx":     idx_ref[0],
                "doc_id":        doc_id,
                "function_name": f"{name}__part{sub_idx}" if total > _SUB_CHUNK_LINES else name,
                "start_line":    start_line0 + pos + 1,
                "end_line":      start_line0 + sub_end,
                "type":          node_type,
            })
            idx_ref[0] += 1
            sub_idx    += 1

        # BUG-02 FIX: se abbiamo raggiunto la fine del nodo, usciamo subito.
        # Prima: `pos = sub_end - 10` con guard `pos >= len-5` non terminava
        # mai quando sub_end == total, causando loop infinito.
        if sub_end >= total:
            break

        pos = sub_end - _SUB_OVERLAP
        if pos <= 0:
            pos = sub_end   # edge case: overlap più grande del sotto-chunk


def _extract_node(
    node:       ast.AST,
    all_lines:  list[str],
    path:       str,
    domain:     str,
    source:     str,
    chunks:     list[dict],
    idx_ref:    list[int],
    class_name: str = "",
) -> None:
    """
    Estrae un singolo FunctionDef / AsyncFunctionDef come chunk (o sotto-chunk
    se >_SUB_CHUNK_LINES righe).
    """
    start_line0 = node.lineno - 1       # 0-based
    end_line0   = node.end_lineno       # 0-based esclusivo
    n_lines     = end_line0 - start_line0

    full_name = f"{class_name}.{node.name}" if class_name else node.name
    node_type = (
        "async_method"   if class_name and isinstance(node, ast.AsyncFunctionDef) else
        "method"         if class_name else
        "async_function" if isinstance(node, ast.AsyncFunctionDef) else
        "function"
    )

    if n_lines > _SUB_CHUNK_LINES:
        _sub_chunk(
            all_lines, start_line0, end_line0,
            full_name, node_type,
            path, domain, source, chunks, idx_ref,
        )
    else:
        code = "\n".join(all_lines[start_line0:end_line0]).strip()
        if code:
            doc_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{path}::{idx_ref[0]}"))
            chunks.append({
                "text":          code,
                "domain":        domain,
                "source":        source,
                "path":          path,
                "chunk_idx":     idx_ref[0],
                "doc_id":        doc_id,
                "function_name": full_name,
                "start_line":    node.lineno,
                "end_line":      node.end_lineno,
                "type":          node_type,
            })
            idx_ref[0] += 1


def chunk_python_file(
    text:   str,
    source: str,
    domain: str,
    path:   str,
) -> list[dict]:
    """
    Suddivide un file Python in chunk per funzione/metodo usando AST.

    Strategia:
      L1 — funzioni top-level  → 1 chunk per funzione (sub-chunking se >80 righe)
      L2 — ClassDef            → 1 chunk per metodo della classe (BUG-01 FIX)
                                  la classe stessa NON diventa un chunk monolitico
      L3 — codice modulo-level → 1 chunk per blocco contiguo (import, costanti, ecc.)

    BUG-01 FIX: prima l'intera ClassDef diventava 1 chunk (es. class Pipeline
    di orchestra_manifold.py: 839 righe, 32 metodi ignorati → 4 chunk totali).
    Ora ogni metodo è un chunk separato → 35+ chunk su manifold.py.
    """
    if not text.strip():
        return []

    try:
        tree = ast.parse(text)
    except SyntaxError:
        # Fallback al chunking standard se il file ha errori di sintassi
        return chunk_text(text, source, domain, path)

    all_lines   = text.splitlines()
    total_lines = len(all_lines)
    chunks:     list[dict] = []
    idx_ref:    list[int]  = [0]

    # Righe occupate da nodi top-level (0-based, end esclusivo)
    # Usato per estrarre il codice a livello modulo in seguito.
    occupied: list[tuple[int, int]] = []

    for node in ast.iter_child_nodes(tree):

        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # ── Funzione top-level ────────────────────────────────────────
            _extract_node(
                node, all_lines, path, domain, source, chunks, idx_ref,
                class_name="",
            )
            occupied.append((node.lineno - 1, node.end_lineno))

        elif isinstance(node, ast.ClassDef):
            # ── BUG-01 FIX: ricorri nei metodi della classe ───────────────
            # Prima: chunk monolitico dell'intera classe.
            # Ora: 1 chunk per metodo, con sub-chunking se necessario.
            # La firma della classe (decoratori + nome + eredità) viene
            # inclusa nel primo metodo per dare contesto al RAG.
            class_header_line = node.lineno - 1  # 0-based

            # Separa metodi da attributi di classe (Assign, AnnAssign)
            methods = [
                child for child in ast.iter_child_nodes(node)
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]

            if not methods:
                # Classe senza metodi (solo attributi): chunk monolitico
                start_l = node.lineno - 1
                end_l   = node.end_lineno
                code    = "\n".join(all_lines[start_l:end_l]).strip()
                if code:
                    doc_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{path}::{idx_ref[0]}"))
                    chunks.append({
                        "text":          code,
                        "domain":        domain,
                        "source":        source,
                        "path":          path,
                        "chunk_idx":     idx_ref[0],
                        "doc_id":        doc_id,
                        "function_name": node.name,
                        "start_line":    node.lineno,
                        "end_line":      node.end_lineno,
                        "type":          "class",
                    })
                    idx_ref[0] += 1
            else:
                # Chunk degli attributi di classe + intestazione (righe prima
                # del primo metodo)
                first_method_line = methods[0].lineno - 1
                header_block = "\n".join(
                    all_lines[class_header_line:first_method_line]
                ).strip()
                if header_block:
                    doc_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{path}::{idx_ref[0]}"))
                    chunks.append({
                        "text":          header_block,
                        "domain":        domain,
                        "source":        source,
                        "path":          path,
                        "chunk_idx":     idx_ref[0],
                        "doc_id":        doc_id,
                        "function_name": f"{node.name}.__class_header__",
                        "start_line":    node.lineno,
                        "end_line":      methods[0].lineno - 1,
                        "type":          "class_header",
                    })
                    idx_ref[0] += 1

                # 1 chunk per metodo
                for method in methods:
                    _extract_node(
                        method, all_lines, path, domain, source, chunks, idx_ref,
                        class_name=node.name,
                    )

            occupied.append((node.lineno - 1, node.end_lineno))

    # ── Codice modulo-level (righe non occupate da funzioni/classi) ───────────
    # Raccoglie blocchi contigui di righe non coperte da nodi già estratti.
    # La ricerca è O(n) con set di righe occupate invece di O(n×m) con lista.
    occupied_lines: set[int] = set()
    for start_occ, end_occ in occupied:
        occupied_lines.update(range(start_occ, end_occ))

    module_parts: list[str] = []
    block_start: int | None = None

    for i in range(total_lines):
        in_occ = i in occupied_lines
        if not in_occ and block_start is None:
            block_start = i
        elif in_occ and block_start is not None:
            block = "\n".join(all_lines[block_start:i]).strip()
            if block:
                module_parts.append(block)
            block_start = None

    # Ultimo blocco (se il file termina con codice modulo-level)
    if block_start is not None:
        block = "\n".join(all_lines[block_start:total_lines]).strip()
        if block:
            module_parts.append(block)

    if module_parts:
        module_text = "\n\n".join(module_parts).strip()
        if module_text:
            doc_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{path}::{idx_ref[0]}"))
            chunks.append({
                "text":          module_text,
                "domain":        domain,
                "source":        source,
                "path":          path,
                "chunk_idx":     idx_ref[0],
                "doc_id":        doc_id,
                "function_name": "(module_level)",
                "start_line":    1,
                "end_line":      total_lines,
                "type":          "module_level",
            })
            idx_ref[0] += 1

    return chunks


# =============================================================================
# PDF: ESTRAZIONE PER PAGINA + METADATA + CHUNKING (PDF-1/2/3/4)
# =============================================================================

def _extract_pdf_metadata(file_path: Path) -> dict:
    """
    Estrae i metadati del PDF (title, author, subject, num_pages).
    Ritorna dict con valori stringa/int; stringa vuota se non disponibile.
    PDF-4: gestione separata per PDF protetti vs corrotti.
    """
    meta = {"pdf_title": "", "pdf_author": "", "pdf_subject": "", "pdf_total_pages": 0}
    if not _PYPDF_OK:
        return meta
    try:
        reader = pypdf.PdfReader(str(file_path), strict=False)
        meta["pdf_total_pages"] = len(reader.pages)
        if reader.metadata:
            meta["pdf_title"]   = str(reader.metadata.get("/Title",   "") or "").strip()
            meta["pdf_author"]  = str(reader.metadata.get("/Author",  "") or "").strip()
            meta["pdf_subject"] = str(reader.metadata.get("/Subject", "") or "").strip()
    except pypdf.errors.FileNotDecryptedError:
        meta["pdf_title"] = "[PDF protetto da password]"
    except Exception:
        pass
    return meta


def _extract_pdf_pages_text(file_path: Path) -> list[tuple[int, str]]:
    """
    Estrae il testo di ogni pagina PDF come lista di (numero_pagina_1based, testo).

    Strategia a tre livelli (PDF-1/2/3):
      L1 pypdf       — sempre disponibile, testo digitale
      L2 pdfplumber  — se installato, migliore per tabelle e layout
      L3 OCR         — se USE_OCR=True e testo vuoto (PDF scansionato)

    PDF-4: gestione differenziata per PDF protetti da password vs corrotti.
    """
    if not _PYPDF_OK and not _PDFPLUMBER_OK:
        return [(1, "[PDF: pypdf e pdfplumber non installati]")]

    pages: list[tuple[int, str]] = []

    # ── Livello 2: pdfplumber (preferito se disponibile) ─────────────────────
    if _PDFPLUMBER_OK:
        try:
            with _pdfplumber.open(str(file_path)) as pdf:
                for i, page in enumerate(pdf.pages, 1):
                    parts: list[str] = []
                    text = page.extract_text() or ""
                    if text.strip():
                        parts.append(text.strip())
                    # Tabelle: converti in testo tabulare leggibile
                    try:
                        tables = page.extract_tables() or []
                        for table in tables:
                            rows = [
                                " | ".join(str(cell or "").strip() for cell in row)
                                for row in table if any(cell for cell in row)
                            ]
                            if rows:
                                parts.append("\n".join(rows))
                    except Exception:
                        pass
                    page_text = "\n\n".join(parts)
                    pages.append((i, page_text))
            if any(t.strip() for _, t in pages):
                return pages   # pdfplumber ha estratto testo → usa questo
        except Exception as e:
            err_type = type(e).__name__
            print(f"[RAG_INDEXER] pdfplumber fallito su {file_path.name}: {err_type}: {e}", flush=True)
        pages = []  # reset — prova con pypdf

    # ── Livello 1: pypdf ──────────────────────────────────────────────────────
    if _PYPDF_OK:
        try:
            reader = pypdf.PdfReader(str(file_path), strict=False)
            # PDF-4: PDF protetto da password
            if reader.is_encrypted:
                return [(1, f"[PDF protetto da password — impossibile estrarre testo: {file_path.name}]")]
            for i, page in enumerate(reader.pages, 1):
                try:
                    text = page.extract_text() or ""
                    pages.append((i, text.strip()))
                except Exception as pe:
                    pages.append((i, f"[Errore pagina {i}: {type(pe).__name__}]"))
        except pypdf.errors.PdfStreamError as e:
            return [(1, f"[PDF corrotto o troncato: {file_path.name} — {e}]")]
        except pypdf.errors.PdfReadError as e:
            return [(1, f"[PDF non leggibile: {file_path.name} — {e}]")]
        except Exception as e:
            return [(1, f"[Errore lettura PDF {file_path.name}: {type(e).__name__}: {e}]")]

    # ── Livello 3: OCR per PDF scansionati (testo vuoto dopo L1/L2) ──────────
    if USE_OCR and _OCR_OK and not any(t.strip() for _, t in pages):
        try:
            print(f"[RAG_INDEXER] OCR su {file_path.name}...", flush=True)
            images = _pdf2image(str(file_path))
            pages = []
            for i, img in enumerate(images, 1):
                ocr_text = _pytesseract.image_to_string(img, lang="ita+eng").strip()
                pages.append((i, ocr_text))
            print(f"[RAG_INDEXER] OCR completato: {len(pages)} pagine", flush=True)
        except Exception as e:
            print(f"[RAG_INDEXER] OCR fallito su {file_path.name}: {e}", flush=True)

    # PDF-3: stub per PDF scansionati senza OCR
    if not any(t.strip() for _, t in pages):
        total = len(pages) if pages else 1
        return [
            (i, f"[Pagina {i}/{total} — PDF scansionato: testo non estraibile. "
                 f"Abilita USE_OCR=True in rag_indexer_lib.py per l'OCR automatico.]")
            for i in range(1, total + 1)
        ]

    return pages


def chunk_pdf_file(
    file_path: Path,
    source:    str,
    domain:    str,
    path:      str,
) -> list[dict]:
    """
    Chunking per pagina di un file PDF con metadati completi.

    Ogni pagina PDF → 1 chunk (se ≤ CHUNK_CHARS) oppure N chunk con overlap
    (se la pagina è molto lunga, es. PDF con testo denso).

    Metadati nel payload Qdrant:
      pdf_page, pdf_total_pages, pdf_title, pdf_author, pdf_subject

    Questo permette query RAG tipo:
      "cosa dice pagina 5 del manuale?"
      "documenti scritti da [autore]"
      "trova il capitolo 3 di [titolo]"
    """
    meta   = _extract_pdf_metadata(file_path)
    pages  = _extract_pdf_pages_text(file_path)
    chunks: list[dict] = []
    idx    = 0

    for page_num, page_text in pages:
        if not page_text.strip():
            continue

        # Normalizza il testo della pagina
        page_text = re.sub(r"\n{3,}", "\n\n", page_text)
        page_text = re.sub(r"[ \t]{3,}", " ", page_text)

        # Prefisso di contesto: sempre visibile al modello
        prefix = f"[Pagina {page_num}/{meta['pdf_total_pages']} — {source}]\n"
        if meta["pdf_title"]:
            prefix = f"[{meta['pdf_title']} — Pagina {page_num}/{meta['pdf_total_pages']}]\n"

        full_text = prefix + page_text

        if len(full_text) <= CHUNK_CHARS:
            # Pagina intera in un chunk unico
            doc_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{path}::pdf::{page_num}"))
            chunks.append({
                "text":            full_text,
                "domain":          domain,
                "source":          source,
                "path":            path,
                "chunk_idx":       idx,
                "doc_id":          doc_id,
                "pdf_page":        page_num,
                "pdf_total_pages": meta["pdf_total_pages"],
                "pdf_title":       meta["pdf_title"],
                "pdf_author":      meta["pdf_author"],
                "pdf_subject":     meta["pdf_subject"],
                "type":            "pdf_page",
            })
            idx += 1
        else:
            # Pagina lunga: sub-chunking con overlap, preservando il prefisso
            start = 0
            sub_idx = 0
            n = len(page_text)
            while start < n:
                end = min(start + CHUNK_CHARS - len(prefix), n)
                if end < n:
                    half = start + (CHUNK_CHARS - len(prefix)) // 2
                    for sep in ("\n\n", "\n", " "):
                        bp = page_text.rfind(sep, half, end)
                        if bp > half:
                            end = bp + len(sep)
                            break
                chunk_content = (prefix + page_text[start:end]).strip()
                if chunk_content:
                    sub_label = f"::pdf::{page_num}::{sub_idx}"
                    doc_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{path}{sub_label}"))
                    chunks.append({
                        "text":            chunk_content,
                        "domain":          domain,
                        "source":          source,
                        "path":            path,
                        "chunk_idx":       idx,
                        "doc_id":          doc_id,
                        "pdf_page":        page_num,
                        "pdf_total_pages": meta["pdf_total_pages"],
                        "pdf_title":       meta["pdf_title"],
                        "pdf_author":      meta["pdf_author"],
                        "pdf_subject":     meta["pdf_subject"],
                        "type":            "pdf_page",
                    })
                    idx += 1
                    sub_idx += 1
                if end >= n:
                    break
                new_start = end - OVERLAP_CHARS
                if new_start <= start:
                    break
                start = new_start

    return chunks


# =============================================================================
# SCANSIONE DIRECTORY
# =============================================================================

def scan_directory(docs_root: Path) -> Iterator[dict]:
    """
    Scansiona ricorsivamente docs_root e restituisce chunk via generator.
    Per i file Python usa chunking AST; per tutti gli altri usa chunking
    testuale standard con overlap.

    BUG-04 FIX: ogni file è wrappato in try/except. Un file problematico
    logga l'errore e continua la scansione invece di bloccarla.
    """
    for file_path in sorted(docs_root.rglob("*")):
        if not file_path.is_file():
            continue
        if file_path.suffix.lower() not in SUPPORTED_EXT:
            continue
        if file_path.name.startswith("."):
            continue

        try:
            rel    = file_path.relative_to(docs_root)
            domain = rel.parts[0] if len(rel.parts) > 1 else "general"
        except ValueError:
            domain = "general"

        try:
            text = extract_text(file_path)
            if not text.strip():
                continue

            if file_path.suffix.lower() == ".py":
                yield from chunk_python_file(
                    text   = text,
                    source = file_path.name,
                    domain = domain,
                    path   = str(file_path),
                )
            elif file_path.suffix.lower() == ".pdf":
                # PDF-1/2/3/4: chunking per pagina con metadati
                yield from chunk_pdf_file(
                    file_path = file_path,
                    source    = file_path.name,
                    domain    = domain,
                    path      = str(file_path),
                )
            else:
                yield from chunk_text(
                    text   = text,
                    source = file_path.name,
                    domain = domain,
                    path   = str(file_path),
                )

        except Exception as e:
            # BUG-04 FIX: log per file problematico, scansione continua.
            print(
                f"[RAG_INDEXER] Errore su {file_path.name}: "
                f"{type(e).__name__}: {e}",
                flush=True,
            )
            continue


def scan_file(file_path: Path, docs_root: Path) -> list[dict]:
    """
    OPT-01: processa un singolo file e ritorna la sua lista di chunk.

    Equivalente a [c for c in scan_directory(docs_root) if c["path"] == str(file_path)]
    ma senza riscansire tutta la directory. Riduce il costo da O(n_files²) a O(1)
    per file durante l'indicizzazione file-per-file in _run_index_job.

    Ritorna lista vuota se il file non è supportato, è nascosto, o dà errore.
    """
    if not file_path.is_file():
        return []
    if file_path.suffix.lower() not in SUPPORTED_EXT:
        return []
    if file_path.name.startswith("."):
        return []

    try:
        rel    = file_path.relative_to(docs_root)
        domain = rel.parts[0] if len(rel.parts) > 1 else "general"
    except ValueError:
        domain = "general"

    try:
        text = extract_text(file_path)
        if not text.strip():
            return []

        ext = file_path.suffix.lower()
        if ext == ".py":
            return chunk_python_file(
                text=text, source=file_path.name,
                domain=domain, path=str(file_path),
            )
        elif ext == ".pdf":
            return chunk_pdf_file(
                file_path=file_path, source=file_path.name,
                domain=domain, path=str(file_path),
            )
        else:
            return chunk_text(
                text=text, source=file_path.name,
                domain=domain, path=str(file_path),
            )
    except Exception as e:
        print(
            f"[RAG_INDEXER] Errore su {file_path.name}: "
            f"{type(e).__name__}: {e}",
            flush=True,
        )
        return []


# =============================================================================
# UTILITÀ
# =============================================================================

def check_dependencies() -> dict[str, bool]:
    """Verifica disponibilità dipendenze opzionali."""
    return {
        "pypdf":       _PYPDF_OK,
        "pdfplumber":  _PDFPLUMBER_OK,
        "ocr":         USE_OCR and _OCR_OK,
        "python-docx": _DOCX_OK,
        "openpyxl":    _XLSX_OK,
    }
