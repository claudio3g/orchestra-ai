"""
RAG Filter v1.6.0 — Orchestra
==================================
Filtro Pipelines con lazy loading del modello di embedding RAG.

CHANGELOG v1.6.0 rispetto a v1.5.5:
  FIX-01  _get_qdrant(): il client Qdrant non viene più memorizzato se la
          connessione di prova (get_collections) fallisce. Prima: si salvava
          un client "zombie" che impediva i tentativi successivi. Ora: la
          variabile self._qdrant viene impostata solo dopo la verifica OK.

  FIX-02  inlet(): rimosso l'import ridondante di embed_for_rag (importato
          ma mai usato direttamente — la funzione _embed() fa già l'import
          internamente). Dead import eliminato.

  FIX-03  max_context_chars: default corretto da 7500 a 12000 per allinearlo
          alla documentazione e alla Sezione 8 dell'handoff.

  FIX-04  HARDWARE_KEYWORDS: lista ridotta e specializzata. Rimossi termini
          generici ("audio", "monitor", "rgb", "led", "webcam", "case",
          "microfono") che causavano iniezione hardware su query non correlate.
          Tenuti solo termini inequivocabilmente legati all'hardware fisico
          del sistema.

  FIX-05  __init__: il messaggio di log mostra correttamente lo stato del
          flag debug_log invece di hardcodare "debug ON".

  FIX-06  _get_qdrant: aggiunto reset self._qdrant = None nell'except per
          garantire il retry alla prossima richiesta.
"""

from __future__ import annotations

import re
import sys
import traceback
from typing import Optional
from pydantic import BaseModel

# Rende le pipeline importabili anche quando il modulo non è nel path
sys.path.insert(0, "/app/pipelines")

# ── Parole chiave hardware ─────────────────────────────────────────────────────
# FIX-04: lista ridotta a termini inequivocabilmente hardware-specifici.
# Rimossi: audio, monitor, webcam, microfono, rgb, led, case, rumore,
# alimentatore, usb, thunderbolt, bluetooth, wifi (troppo generici).
HARDWARE_KEYWORDS = [
    "ram", "cpu", "gpu", "vram", "nvidia", "geforce", "rtx", "gtx",
    "cuda", "processore", "scheda video", "scheda madre",
    "disco", "ssd", "nvme", "pcie", "sata",
    "memoria", "ddr", "dimm", "slot", "zram",
    "core i", "i9", "i7", "i5", "i3", "intel", "amd", "ryzen",
    "driver", "firmware", "bios", "uefi",
    "clock", "overclock", "boost", "tdp", "watt",
    "temperatura", "dissipatore", "termica", "throttling",
    "chipset", "soc", "npu", "tpu", "tensore",
    "benchmark", "vram usata", "memoria video",
    "quanta ram", "quanta vram", "quanto spazio",
    "nvidia-smi", "gpu-z", "hwinfo",
    "laptop specifiche", "portatile specifiche",
]

try:
    from qdrant_client import QdrantClient
    from qdrant_client.http import models as qdrant_models
    _QDRANT_AVAILABLE = True
except ImportError:
    _QDRANT_AVAILABLE = False


class Pipeline:

    class Valves(BaseModel):
        qdrant_url:        str   = "http://ai-qdrant-session:6333"
        collection_name:   str   = "orchestra"
        top_k:             int   = 6
        min_score:         float = 0.45
        max_context_chars: int   = 12000   # FIX-03: allineato alla documentazione (era 7500)
        enabled:           bool  = True
        debug_log:         bool  = False
        pipelines:         list  = []

    _FILENAME_PATTERN = re.compile(
        r'\b(\w+\.(?:py|sh|md|json|yaml|yml))\b', re.IGNORECASE
    )

    def __init__(self) -> None:
        self.type      = "filter"
        self.name      = "RAG Filter"
        self.valves    = self.Valves()
        self.pipelines = ["*"]
        self._qdrant: Optional[QdrantClient] = None
        self._cache: dict[str, list[float]] = {}
        self._skip_prefixes = (
            "### task:", "### instruction:",
            "create a concise", "generate a title",
            "generate 3 follow", "generate 1-3 broad",
        )
        self._skip_commands = ("/generate", "/rag", "/evolve", "/review")
        # FIX-05: mostra lo stato reale del debug_log
        debug_state = "ON" if self.valves.debug_log else "OFF"
        print(f"[RAG_FILTER] Inizializzato v1.6.0 (hardware query support, debug {debug_state}).", flush=True)

    def _get_model(self):
        from embedding_utils import get_rag_embedding_model
        return get_rag_embedding_model()

    def _get_qdrant(self) -> Optional[QdrantClient]:
        """
        Restituisce un client Qdrant funzionante o None.

        FIX-01: il client viene salvato in self._qdrant SOLO dopo che la
        connessione di prova (get_collections) ha avuto successo. Se la prova
        fallisce, self._qdrant rimane None così il prossimo request ritenta.
        FIX-06: in caso di eccezione, self._qdrant viene esplicitamente
        reimpostato a None per garantire il retry.
        """
        if self._qdrant is not None:
            return self._qdrant
        if not _QDRANT_AVAILABLE:
            return None
        try:
            client = QdrantClient(url=self.valves.qdrant_url, timeout=5)
            client.get_collections()   # probe di connessione
            self._qdrant = client      # assegnato SOLO se il probe ha successo
        except Exception as e:
            print(f"[RAG_FILTER] Errore connessione Qdrant: {e}", flush=True)
            self._qdrant = None        # FIX-06: reset esplicito per retry futuro
        return self._qdrant

    def _embed(self, text: str) -> Optional[list[float]]:
        if text in self._cache:
            return self._cache[text]
        from embedding_utils import embed_for_rag
        vec = embed_for_rag(text)
        if vec is None:
            return None
        if len(self._cache) >= 256:
            del self._cache[next(iter(self._cache))]
        self._cache[text] = vec
        return vec

    def _search(self, query_vec: list[float]) -> list[dict]:
        qdrant = self._get_qdrant()
        if qdrant is None:
            return []
        try:
            results = qdrant.query_points(
                collection_name=self.valves.collection_name,
                query=query_vec,
                limit=self.valves.top_k,
                score_threshold=self.valves.min_score,
                with_payload=True,
            )
            hits = results.points if hasattr(results, 'points') else []
            return [
                {"text": r.payload.get("text", "") if r.payload else "",
                 "domain": r.payload.get("domain", "") if r.payload else "",
                 "source": r.payload.get("source", "") if r.payload else "",
                 "score": round(r.score, 3)}
                for r in hits if r.payload
            ]
        except Exception as e:
            # Reset del client su errore di search — al prossimo request viene
            # ricreato e testato con il probe
            self._qdrant = None
            print(f"[RAG_FILTER] Errore search Qdrant: {e}", flush=True)
            return []

    def _search_by_source(self, source_name: str, limit: int = 100) -> list[dict]:
        qdrant = self._get_qdrant()
        if qdrant is None:
            return []
        try:
            results, _ = qdrant.scroll(
                collection_name=self.valves.collection_name,
                scroll_filter=qdrant_models.Filter(
                    must=[qdrant_models.FieldCondition(
                        key="source", match=qdrant_models.MatchValue(value=source_name)
                    )]
                ),
                limit=limit,
                with_payload=True,
                with_vectors=False,
            )
            return [
                {"text": r.payload.get("text", "") if r.payload else "",
                 "domain": r.payload.get("domain", "") if r.payload else "",
                 "source": r.payload.get("source", "") if r.payload else "",
                 "score": 1.0}
                for r in results if r.payload
            ]
        except Exception as e:
            print(f"[RAG_FILTER] Errore search by source '{source_name}': {e}", flush=True)
            return []

    @classmethod
    def _filter_relevant_chunks(cls, query: str, chunks: list[dict], filename: str) -> list[dict]:
        """
        Filtra i chunk cercando definizioni di funzione menzionate nella query.
        Cerca identificatori multi-parola con underscore (es. embed_for_rag).
        """
        words = re.findall(r'\b([a-zA-Z_][a-zA-Z0-9_]*(?:_[a-zA-Z0-9_]+)+)\b', query)
        if not words:
            return []
        relevant = []
        for chunk in chunks:
            text = chunk.get("text", "")
            for word in words:
                if f'def {word}' in text or f'async def {word}' in text:
                    relevant.append(chunk)
                    break
        return relevant

    @classmethod
    def _detect_filenames(cls, text: str) -> list[str]:
        return [m.group(1) for m in cls._FILENAME_PATTERN.finditer(text)]

    def _should_skip(self, text: str) -> bool:
        if not self.valves.enabled:
            return True
        # Eccezione: le query hardware vengono sempre processate, anche se corte
        if self._is_hardware_query(text):
            return False
        if len(text.strip()) < 20:
            return True
        lower = text.lower().strip()
        if any(lower.startswith(cmd) for cmd in self._skip_commands):
            return True
        return any(lower.startswith(p) for p in self._skip_prefixes)

    @staticmethod
    def _is_hardware_query(query: str) -> bool:
        """Verifica se la query riguarda l'hardware fisico del sistema."""
        query_lower = query.lower()
        for kw in HARDWARE_KEYWORDS:
            if kw in query_lower:
                return True
        return False

    @staticmethod
    def _filter_hardware_chunks(query: str, chunks: list[dict]) -> list[dict]:
        """
        Restituisce solo i chunk hardware pertinenti alla domanda specifica.
        Mappa le keyword a categorie e seleziona i chunk che le contengono.
        """
        query_lower = query.lower()
        keyword_map = {
            "ram":         ["memoria", "ram", "ddr", "gigabyte", "zram", "dimm"],
            "cpu":         ["processore", "cpu", "core", "thread", "i9", "intel", "ryzen"],
            "gpu":         ["nvidia", "geforce", "rtx", "gpu", "cuda", "vram", "scheda video"],
            "disco":       ["ssd", "nvme", "storage", "disco", "micron", "pcie"],
            "temperatura": ["temperatura", "termico", "celsius", "°c", "throttling", "dissipatore"],
        }
        areas = []
        for area, keywords in keyword_map.items():
            for kw in keywords:
                if kw in query_lower:
                    areas.append(area)
                    break
        if not areas:
            return []   # nessuna area specifica, il chiamante userà il fallback
        relevant = []
        for chunk in chunks:
            text_lower = chunk.get("text", "").lower()
            for area in areas:
                for kw in keyword_map[area]:
                    if kw in text_lower:
                        relevant.append(chunk)
                        break
                else:
                    continue
                break
        return relevant

    def _format_context(self, chunks: list[dict]) -> str:
        exact    = [c for c in chunks if c["score"] >= 1.0]
        semantic = [c for c in chunks if c["score"] < 1.0]
        lines, total = [], 0
        for c in exact:
            entry = f"[{c['domain']}/{c['source']} | codice sorgente]\n{c['text']}"
            if total + len(entry) > self.valves.max_context_chars:
                room = self.valves.max_context_chars - total
                if room > 120:
                    lines.append(entry[:room] + "…")
                break
            lines.append(entry)
            total += len(entry) + 2
        for c in semantic:
            entry = f"[{c['domain']}/{c['source']} | similarità: {c['score']}]\n{c['text']}"
            if total + len(entry) > self.valves.max_context_chars:
                room = self.valves.max_context_chars - total
                if room > 120:
                    lines.append(entry[:room] + "…")
                break
            lines.append(entry)
            total += len(entry) + 2
        return "\n\n".join(lines)

    async def inlet(self, body: dict, user: dict | None = None) -> dict:
        try:
            messages = body.get("messages", [])
            if not messages:
                return body
            last_user = next((m for m in reversed(messages) if m.get("role") == "user"), None)
            if not last_user:
                return body
            content = last_user.get("content", "")
            query = (
                content if isinstance(content, str)
                else " ".join(i.get("text", "") for i in content if i.get("type") == "text")
                if isinstance(content, list)
                else str(content)
            ).strip()

            if self.valves.debug_log:
                print(
                    f"[RAG_FILTER] Query: '{query[:80]}' | "
                    f"Hardware: {self._is_hardware_query(query)} | "
                    f"Skip: {self._should_skip(query)}",
                    flush=True
                )

            if self._should_skip(query):
                return body

            # FIX-02: rimosso import ridondante di embed_for_rag (non usato
            # direttamente qui — self._embed() lo importa internamente)
            vec = self._embed(query)
            if vec is None:
                if self.valves.debug_log:
                    print("[RAG_FILTER] Embedding fallito, esco.", flush=True)
                return body

            chunks = self._search(vec)
            if self.valves.debug_log:
                print(f"[RAG_FILTER] Chunk semantici: {len(chunks)}", flush=True)

            # Ricerca supplementare per nome file menzionato nella query
            filenames = self._detect_filenames(query)
            if filenames:
                existing_texts = {c["text"] for c in chunks}
                for fname in filenames:
                    if self.valves.debug_log:
                        print(f"[RAG_FILTER] Ricerca file: {fname}", flush=True)
                    all_file_chunks = self._search_by_source(fname, limit=100)
                    filtered  = self._filter_relevant_chunks(query, all_file_chunks, fname)
                    selected  = filtered[:10] if filtered else all_file_chunks[:5]
                    for sc in selected:
                        if sc["text"] not in existing_texts:
                            chunks.append(sc)
                            existing_texts.add(sc["text"])

            # Iniezione forzata del report hardware con filtro intelligente
            if self._is_hardware_query(query):
                hw_chunks_all = self._search_by_source("hardware-report.md", limit=20)
                if self.valves.debug_log:
                    print(f"[RAG_FILTER] Hardware chunk totali: {len(hw_chunks_all)}", flush=True)
                if hw_chunks_all:
                    relevant_hw = self._filter_hardware_chunks(query, hw_chunks_all)
                    if not relevant_hw:
                        # Fallback: domanda generica → mix essenziale
                        essential_kw = ["ram", "cpu", "gpu", "nvidia", "disco", "ssd",
                                        "memoria", "processore", "vram"]
                        for hc in hw_chunks_all:
                            text_lower = hc["text"].lower()
                            if any(kw in text_lower for kw in essential_kw):
                                relevant_hw.append(hc)
                        if not relevant_hw:
                            relevant_hw = hw_chunks_all[:6]

                    existing_texts = {c["text"] for c in chunks}
                    added = 0
                    for hc in relevant_hw:
                        if hc["text"] not in existing_texts:
                            chunks.append(hc)
                            existing_texts.add(hc["text"])
                            added += 1
                    if self.valves.debug_log:
                        print(
                            f"[RAG_FILTER] Aggiunti {added} chunk hardware (filtro intelligente)",
                            flush=True
                        )

            if not chunks:
                return body

            ctx   = self._format_context(chunks)
            block = f"\n\n---\n📚 CONTESTO DALLA KNOWLEDGE BASE\n{ctx}\n---"

            sys_idx = next((i for i, m in enumerate(messages) if m.get("role") == "system"), None)
            if sys_idx is not None:
                messages[sys_idx]["content"] += block
            else:
                messages.insert(0, {"role": "system", "content": f"Sei un assistente AI.{block}"})
            body["messages"] = messages

            if self.valves.debug_log:
                print("[RAG_FILTER] Contesto iniettato nel system message.", flush=True)
            return body

        except Exception as e:
            print(f"[RAG_FILTER] FATAL ERROR: {e}", flush=True)
            traceback.print_exc()
            return body

    async def outlet(self, body: dict, user: dict | None = None) -> dict:
        return body
