"""
Orchestra Bootstrap Pipeline v1.0.0
====================================
Inietta automaticamente AI_BOOTSTRAP.md + AI_MANIFEST.md + AI_READ_PROTOCOL.md
come system message all'inizio di ogni conversazione.

Richiede mount /app/ai -> $HOME/ai-sessioni:ro nel container pipelines.

Autore: Orchestra AI
"""
from pydantic import BaseModel
from typing import Optional
from pathlib import Path


class Pipeline:
    class Valves(BaseModel):
        enabled: bool = True
        bootstrap_path: str = "/app/ai/AI_BOOTSTRAP.md"
        manifest_path: str = "/app/ai/AI_MANIFEST.md"
        read_protocol_path: str = "/app/ai/AI_READ_PROTOCOL.md"
        max_bytes: int = 20000

    def __init__(self):
        self.name = "Orchestra Bootstrap"
        self.valves = self.Valves()
        self._cached: Optional[str] = None
        self._cache_mtime: float = 0.0

    def _read_file(self, path: str) -> str:
        p = Path(path)
        if not p.exists():
            return ""
        try:
            return p.read_text(encoding="utf-8")
        except Exception:
            return ""

    def _load_context(self) -> str:
        # Cache invalidata se i file cambiano
        try:
            newest_mtime = max(
                Path(self.valves.bootstrap_path).stat().st_mtime,
                Path(self.valves.manifest_path).stat().st_mtime,
                Path(self.valves.read_protocol_path).stat().st_mtime,
            )
        except Exception:
            newest_mtime = 0.0

        if self._cached is not None and newest_mtime <= self._cache_mtime:
            return self._cached

        parts = []
        for label, path in (
            ("AI_BOOTSTRAP.md", self.valves.bootstrap_path),
            ("AI_MANIFEST.md", self.valves.manifest_path),
            ("AI_READ_PROTOCOL.md", self.valves.read_protocol_path),
        ):
            content = self._read_file(path)
            if content:
                parts.append(f"=== {label} ===\n{content}")

        combined = "\n\n".join(parts)
        if len(combined) > self.valves.max_bytes:
            combined = combined[: self.valves.max_bytes] + "\n\n[... troncato ...]"

        self._cached = combined
        self._cache_mtime = newest_mtime
        return combined

    async def inlet(self, body: dict, user: dict) -> dict:
        if not self.valves.enabled:
            return body
        context = self._load_context()
        if not context:
            return body

        messages = body.get("messages", [])
        # Se c'e' gia' un system message, non sovrascrivere
        if messages and messages[0].get("role") == "system":
            return body

        messages.insert(0, {
            "role": "system",
            "content": (
                "Contesto automatico Orchestra AI (bootstrap + manifest + protocollo).\n\n"
                + context
            ),
        })
        body["messages"] = messages
        return body

    async def outlet(self, body: dict, user: dict) -> dict:
        return body

    def pipes(self):
        return [{"id": "orchestra-bootstrap", "name": "Orchestra (bootstrap auto)"}]
