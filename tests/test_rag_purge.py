"""Test B35 (rag_service v1.5.2): pulizia dei pezzi orfani e dei pezzi di coda. Solo libreria standard.

Estrae da rag_service.py SOLO il blocco B35 (il modulo intero richiede fastembed/qdrant/flask) e lo
prova contro un Qdrant finto che applica davvero i selettori (lista di id, filtro path + must_not id)."""
import os, sys, tempfile
from pathlib import Path
from types import SimpleNamespace
HERE = Path(__file__).resolve().parent; REPO = HERE.parent
sys.path.insert(0, str(HERE / "helpers"))
from tiny import check, summary

src = (REPO / "rag" / "rag_service.py").read_text(encoding="utf-8")
a = src.index("# ── B35 (v1.5.2): pezzi orfani"); b = src.index("# ── fine B35")


class PointIdsList:
    def __init__(self, points): self.points = points
class FieldCondition:
    def __init__(self, key, match): self.key, self.match = key, match
class MatchValue:
    def __init__(self, value): self.value = value
class HasIdCondition:
    def __init__(self, has_id): self.has_id = has_id
class Filter:
    def __init__(self, must=None, must_not=None): self.must, self.must_not = must or [], must_not or []
class FilterSelector:
    def __init__(self, filter): self.filter = filter


class FakeQdrant:
    def __init__(self, points): self.points = dict(points); self.deletes = 0; self.scroll_limits = []
    def scroll(self, collection_name, limit, offset, with_payload, with_vectors):
        assert collection_name == "orchestra" and with_vectors is False and with_payload == ["path"]
        self.scroll_limits.append(limit)
        ids = sorted(self.points); start = offset or 0
        page = [SimpleNamespace(id=i, payload={"path": self.points[i]} if self.points[i] is not None else {})
                for i in ids[start:start + limit]]
        nxt = start + limit if start + limit < len(ids) else None
        return page, nxt
    def delete(self, collection_name, points_selector, wait):
        assert collection_name == "orchestra" and wait is True
        self.deletes += 1
        sel = points_selector
        if isinstance(sel, PointIdsList):
            assert len(sel.points) <= 256, "lotti da al massimo 256 id"
            for i in sel.points: self.points.pop(i, None)
            return
        f = sel.filter
        def match(i, p):
            ok = all(c.key == "path" and p == c.match.value for c in f.must if isinstance(c, FieldCondition))
            ex = any(isinstance(c, HasIdCondition) and i in c.has_id for c in f.must_not)
            return ok and not ex
        for i in [i for i, p in self.points.items() if match(i, p)]: self.points.pop(i)


ns = dict(os=os, COLLECTION_NAME="orchestra", PointIdsList=PointIdsList, FilterSelector=FilterSelector,
          Filter=Filter, FieldCondition=FieldCondition, MatchValue=MatchValue, HasIdCondition=HasIdCondition)
exec(compile(src[a:b], "rag_service_b35", "exec"), ns)
purge, leftovers = ns["_purge_orphan_points"], ns["_purge_file_leftovers"]

with tempfile.TemporaryDirectory() as t:
    root = Path(t) / "document-ai"; (root / "knowledge").mkdir(parents=True)
    vivo = root / "knowledge" / "vivo.md"; vivo.write_text("x")
    presente_non_scansionato = root / "knowledge" / ".nascosto.md"; presente_non_scansionato.write_text("x")
    morto = str(root / "knowledge" / "morto.md"); morto2 = str(root / "system" / "vecchio.py")
    fuori = str(Path(t) / "altrove" / "sparito.md"); simile = str(root) + "-copia/sparito.md"
    pts = {1: str(vivo), 2: str(vivo), 3: morto, 4: morto, 5: morto2, 6: fuori, 7: None,
           8: str(presente_non_scansionato), 9: simile}
    q = FakeQdrant(pts)
    n, f = purge(q, [str(vivo)], root)
    check("orfani: tolti i 3 pezzi di 2 file spariti sotto la radice", (n, f) == (3, 2), str((n, f)))
    check("orfani: restano i pezzi del file vivo", 1 in q.points and 2 in q.points)
    check("orfani: fuori dalla radice non si tocca", 6 in q.points)
    check("orfani: radice con nome simile (document-ai-copia) non si tocca", 9 in q.points)
    check("orfani: pezzo senza path non si tocca", 7 in q.points)
    check("orfani: file presente sul disco ma non scansionato non si tocca", 8 in q.points)

    q = FakeQdrant(pts)
    check("prudenza: scansione vuota (cartella smontata) non cancella nulla",
          purge(q, [], root) == (0, 0) and len(q.points) == len(pts) and q.deletes == 0)
    q = FakeQdrant(pts)
    check("radice con barra finale: stesso risultato", purge(q, {str(vivo)}, str(root) + "/") == (3, 2))

    big = {i: (str(vivo) if i < 10 else morto) for i in range(1300)}
    q = FakeQdrant(big)
    n, f = purge(q, [str(vivo)], root)
    check("tante pagine e tanti lotti: 1290 pezzi tolti, nessuno vivo perso",
          n == 1290 and f == 1 and sorted(q.points) == list(range(10)), str((n, len(q.points))))
    check("scroll a pagine (piu di una chiamata)", len(q.scroll_limits) >= 3)

    q = FakeQdrant({"a0": str(vivo), "a1": str(vivo), "a2": str(vivo), "a3": str(vivo), "b0": morto})
    leftovers(q, vivo, ["a0", "a1"])
    check("coda: file accorciato da 4 a 2 pezzi, tolti a2 e a3", sorted(q.points) == ["a0", "a1", "b0"], str(sorted(q.points)))
    q = FakeQdrant({"a0": str(vivo)}); leftovers(q, vivo, [])
    check("coda: lista id vuota non cancella nulla", q.points == {"a0": str(vivo)} and q.deletes == 0)

# Collegamenti nel servizio: le due funzioni sono chiamate dove servono, protette da try.
job = src[src.index("def _run_index_job"):src.index("_watcher_thread = None")]
sync = src[src.index('@app.route("/index", methods=["POST"])'):src.index('@app.route("/index/async"')]
check("job asincrono: pulizia di coda dopo upsert senza errori",
      "if file_errors == 0 and file_processed > 0:\n                    try:\n                        _purge_file_leftovers(" in job)
check("job asincrono: pulizia orfani con l elenco dei file scansionati",
      "_purge_orphan_points(qdrant, [str(f) for f in all_files], DOCS_ROOT)" in job)
check("/index: coda solo senza errori, poi orfani",
      "if not errors:" in sync and "_purge_file_leftovers(qdrant, _p, _ids)" in sync
      and "_purge_orphan_points(qdrant, files_seen, DOCS_ROOT)" in sync)
check("import dei selettori Qdrant", "PointIdsList, FilterSelector, Filter, FieldCondition" in src)
check("versione v1.5.2 nell intestazione", src.startswith('"""\nRAG Service v1.5.2') or "RAG Service v1.5.2 — Orchestra" in src[:200])
summary("RAG-PURGE")
