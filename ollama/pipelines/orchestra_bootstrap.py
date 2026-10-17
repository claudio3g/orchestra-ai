"""
Orchestra Bootstrap Filter v1.1.0
=================================
Inietta le regole del progetto (AI_BOOTSTRAP.md + AI_READ_PROTOCOL.md) come system
message all'inizio di ogni conversazione.

Richiede mount /app/ai -> $HOME/ai-sessioni:ro nel container pipelines.

CHANGELOG v1.1.0 rispetto a v1.0.0:
  FIX-1  E' un FILTRO a tutti gli effetti: self.type = "filter" + valves `pipelines`
         e `priority`. Il framework Pipelines applica inlet()/outlet() solo ai moduli
         con type == "filter" e legge valves.pipelines / valves.priority; senza
         type la v1.0.0 veniva registrata come "pipe" semplice (inlet ignorato) e
         pipes() non fa parte dell'API di Pipelines (e' di Open WebUI Functions).
  OPT-1  Contesto COMPATTO: v1.0.0 iniettava ~11 KB (≈3-4 mila token) in OGNI messaggio,
         contro un context_length di 8192 del manifold. Ora di default solo le sezioni
         essenziali (regole, percorsi, divieti, hardware) + protocollo di lettura, tetto
         3500 caratteri. Elenco file e manifest si abilitano con include_file_list /
         include_manifest quando servono davvero.
  OPT-2  Se esiste gia' un system message viene ARRICCHITO (invece di saltare tutto):
         altrimenti con un prompt di sistema del modello le regole non arrivavano mai.
         Iniezione idempotente (marker) e cache invalidata anche se cambiano i valve.
"""
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel

MARKER = "Contesto automatico Orchestra AI"


class Pipeline:
    class Valves(BaseModel):
        # Richiesti dal framework Pipelines per i filtri.
        pipelines: List[str] = ["*"]
        priority: int = 0

        enabled: bool = True
        bootstrap_path: str = "/app/ai/AI_BOOTSTRAP.md"
        manifest_path: str = "/app/ai/AI_MANIFEST.md"
        read_protocol_path: str = "/app/ai/AI_READ_PROTOCOL.md"
        # Sezioni di AI_BOOTSTRAP.md da iniettare (titolo che CONTIENE la voce, CSV).
        sections: str = "REGOLE FONDAMENTALI,Percorsi reali,Cose da NON fare,Contesto hardware"
        include_file_list: bool = False     # sezione "File tracciati" (~3 KB)
        include_manifest: bool = False      # AI_MANIFEST.md (~4 KB)
        max_bytes: int = 3500               # tetto del contesto iniettato (caratteri)

    def __init__(self):
        self.type = "filter"
        self.id = "orchestra_bootstrap"
        self.name = "Orchestra Bootstrap"
        self.valves = self.Valves()
        self._cached: Optional[str] = None
        self._cache_key: Optional[tuple] = None

    # ── lettura ──────────────────────────────────────────────────────────────
    @staticmethod
    def _read_file(path: str) -> str:
        p = Path(path)
        try:
            return p.read_text(encoding="utf-8") if p.exists() else ""
        except Exception:
            return ""

    @staticmethod
    def _mtime(path: str) -> float:
        try:
            return Path(path).stat().st_mtime
        except Exception:
            return 0.0

    def _wanted_sections(self) -> List[str]:
        wanted = [s.strip() for s in self.valves.sections.split(",") if s.strip()]
        if self.valves.include_file_list:
            wanted.append("File tracciati")
        return wanted

    def _select_sections(self, text: str) -> str:
        """Tiene l'intestazione del documento e solo le sezioni '## ' richieste."""
        wanted = self._wanted_sections()
        chunks, current = [], []
        for line in text.splitlines():
            if line.startswith("## ") and current:
                chunks.append("\n".join(current)); current = []
            current.append(line)
        if current:
            chunks.append("\n".join(current))
        kept = []
        for i, c in enumerate(chunks):
            head = c.splitlines()[0] if c else ""
            if i == 0 and not head.startswith("## "):
                kept.append(head)                    # titolo "# AI Bootstrap"
            elif any(w.lower() in head.lower() for w in wanted):
                kept.append(c.strip())
        return "\n\n".join(kept)

    def _load_context(self) -> str:
        v = self.valves
        paths = [v.bootstrap_path, v.read_protocol_path] + ([v.manifest_path] if v.include_manifest else [])
        key = (tuple(self._mtime(p) for p in paths), v.sections, v.include_file_list,
               v.include_manifest, v.max_bytes)
        if self._cached is not None and key == self._cache_key:
            return self._cached

        parts = []
        boot = self._read_file(v.bootstrap_path)
        if boot:
            parts.append("=== AI_BOOTSTRAP.md (estratto) ===\n" + self._select_sections(boot))
        proto = self._read_file(v.read_protocol_path)
        if proto:
            parts.append("=== AI_READ_PROTOCOL.md ===\n" + proto.strip())
        if v.include_manifest:
            manifest = self._read_file(v.manifest_path)
            if manifest:
                parts.append("=== AI_MANIFEST.md ===\n" + manifest.strip())

        combined = "\n\n".join(p for p in parts if p.strip())
        if len(combined) > v.max_bytes:
            combined = combined[: v.max_bytes].rstrip() + "\n\n[... troncato ...]"
        self._cached, self._cache_key = combined, key
        return combined

    # ── filtro ───────────────────────────────────────────────────────────────
    async def inlet(self, body: dict, user: Optional[dict] = None) -> dict:
        if not self.valves.enabled:
            return body
        context = self._load_context()
        if not context:
            return body

        messages = body.get("messages", [])
        header = f"{MARKER} (regole del progetto).\n\n"
        if messages and messages[0].get("role") == "system":
            existing = messages[0].get("content") or ""
            if isinstance(existing, str) and MARKER not in existing:
                messages[0] = {**messages[0], "content": existing.rstrip() + "\n\n" + header + context}
        else:
            messages.insert(0, {"role": "system", "content": header + context})
        body["messages"] = messages
        return body

    async def outlet(self, body: dict, user: Optional[dict] = None) -> dict:
        return body
