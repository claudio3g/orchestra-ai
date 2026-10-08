"""
orchestra_evolver.py — Auto-evoluzione conservativa — Orchestra
====================================================================
ATTENZIONE: Questo file NON definisce una classe Pipeline.
È un modulo utility importato dinamicamente da orchestra_manifold.py
tramite importlib, esattamente come image_loop.py.

Viene caricato da orchestra_manifold.py quando riceve il comando /evolve.
Non viene interpretato come pipeline autonoma dal framework Pipelines.

PRINCIPI FONDAMENTALI (non violare mai):
  1. MAI modificare codice senza backup automatico
  2. MAI applicare modifiche senza validazione sintattica (ast.parse)
  3. MAI modificare più di un file per ciclo evolutivo
  4. MAI applicare modifiche che superano il 30% del file
  5. Rollback automatico se smoke test fallisce
  6. In caso di dubbio → propone su JSONL, non applica

LIVELLI DI AUTONOMIA:
  0 (default) — propone, non applica mai
  1 (admin)   — può aggiornare Valves runtime
  2 (admin)   — può aggiornare esempi routing in Qdrant
  3 (admin)   — può modificare codice Python (con backup + smoke test)

COMANDI (gestiti dal manifold via /evolve):
  /evolve status           — stato sistema, proposte pendenti, backups
  /evolve analyze          — analisi patterns.jsonl + nuove proposte LLM
  /evolve apply-code N     — applica proposta N (admin, livello 3)
  /evolve rollback         — ripristina ultimo backup (admin)
  /evolve update-routing   — aggiorna esempi Qdrant (admin, livello 2)
  /evolve routing-history  — elenca gli snapshot di routing disponibili
  /evolve routing-restore <file> — ripristina uno snapshot (admin)
  /evolve apply-config     — guida alla modifica Valves via OpenWebUI
"""

from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

import requests

# ── NUOVI IMPORT per update-routing ────────────────────────────────────────
try:
    from qdrant_client import QdrantClient
    from qdrant_client.models import Filter, PointStruct
    _QDRANT_AVAILABLE = True
except ImportError:
    _QDRANT_AVAILABLE = False
    QdrantClient = None
    PointStruct = None

# ── Percorsi ─────────────────────────────────────────────────────────────────
PIPELINES_DIR = Path(os.environ.get("PIPELINES_DIR", "/app/pipelines"))
LOGS_DIR      = Path(os.environ.get("PATTERN_LOG_PATH",
                                    "/app/logs/patterns.jsonl")).parent
PATTERN_LOG   = LOGS_DIR / "patterns.jsonl"
PROPOSALS_LOG = LOGS_DIR / "evolution_proposals.jsonl"
STATE_FILE    = LOGS_DIR / "orchestra_state.json"
BACKUP_DIR    = LOGS_DIR / "backups"

# Whitelist conservativa dei file modificabili
ALLOWED_FILES = {
    "orchestra_manifold.py",
    "rag_filter.py",
    "image_loop.py",
    "embedding_utils.py",
    "pattern_logger.py",
}

# ── NUOVE COSTANTI per update-routing ─────────────────────────────────────
VALID_AGENTS = {
    "linux_admin", "ml_engineer", "comfy_integrator",
    "design_critic", "orchestra_dev", "coordinator"
}
MAX_QUERIES_TO_PROCESS = 50
CLASSIFIER_TIMEOUT_S = 15
MIN_FREQUENCY = 2

QDRANT_URL = "http://ai-qdrant-session:6333"
ROUTING_COLLECTION = "orchestra_routing"
ROUTING_SNAPSHOTS_DIR = Path(os.environ.get("DOCS_ROOT", "/app/document-ai")) / "routing_snapshots"

# NOTA: embed_for_routing viene importato DINAMICAMENTE dentro _cmd_update_routing
#       per evitare problemi di path e dipendenze circolari.

# ── STATO ────────────────────────────────────────────────────────────────────

def _load_state() -> dict:
    if not STATE_FILE.exists():
        return {
            "version": "1.0",
            "created_at": datetime.now().isoformat(),
            "last_updated": datetime.now().isoformat(),
            "evolution_cycles": 0,
            "applied_changes": [],
        }
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {"version": "1.0", "created_at": datetime.now().isoformat(),
                "last_updated": datetime.now().isoformat(),
                "evolution_cycles": 0, "applied_changes": []}

def _save_state(state: dict) -> None:
    state["last_updated"] = datetime.now().isoformat()
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))

# ── BACKUP ───────────────────────────────────────────────────────────────────

def _backup(filepath: Path) -> str:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = BACKUP_DIR / f"{filepath.name}.{ts}.bak"
    shutil.copy2(filepath, dest)
    return str(dest)

def _restore(backup_path: str, target: Path) -> bool:
    src = Path(backup_path)
    if not src.exists():
        return False
    shutil.copy2(src, target)
    return True

def _list_backups() -> list:
    if not BACKUP_DIR.exists():
        return []
    files = sorted(BACKUP_DIR.glob("*.bak"), key=os.path.getmtime, reverse=True)
    return [{"file": f.name, "created": datetime.fromtimestamp(os.path.getmtime(f)).isoformat()} for f in files]

# ── VALIDAZIONE ─────────────────────────────────────────────────────────────

def _validate(original: str, modified: str, filename: str) -> tuple:
    try:
        ast.parse(modified)
    except SyntaxError as e:
        return False, f"SyntaxError: {e}"
    for pattern in ["os.system(", "eval(", "exec(", "__import__("]:
        if pattern in modified and pattern not in original:
            return False, f"Pattern pericoloso introdotto: {pattern}"
    if len(modified) < 0.7 * len(original) or len(modified) > 1.3 * len(original):
        return False, "Modifica troppo invasiva (>30%)"
    return True, "OK"

# ── SMOKE TEST ──────────────────────────────────────────────────────────────

def _smoke_test(filepath: Path) -> tuple:
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("test_module", filepath)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if hasattr(module, "Pipeline"):
            pip = module.Pipeline()
            return True, f"type={getattr(pip, 'type', '?')} name={getattr(pip, 'name', '?')}"
        return True, "modulo valido (nessuna Pipeline)"
    except Exception as e:
        return False, str(e)

# ── PROPOSALS ───────────────────────────────────────────────────────────────

def _load_proposals(status: Optional[str] = None) -> list:
    if not PROPOSALS_LOG.exists():
        return []
    out = []
    for line in PROPOSALS_LOG.read_text().strip().splitlines():
        try:
            p = json.loads(line)
            if status and p.get("status") != status:
                continue
            out.append(p)
        except Exception:
            pass
    return out

def _write_proposal(proposal: dict) -> int:
    proposals = _load_proposals()
    new_id = max([p.get("id", 0) for p in proposals], default=-1) + 1
    proposal["id"] = new_id
    proposal["created_at"] = datetime.now().isoformat()
    proposal["status"] = "pending"
    PROPOSALS_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(PROPOSALS_LOG, "a") as f:
        f.write(json.dumps(proposal) + "\n")
    return new_id

def _mark_proposal(pid: int, status: str, note: str = "") -> None:
    lines = []
    if PROPOSALS_LOG.exists():
        lines = PROPOSALS_LOG.read_text().strip().splitlines()
    new_lines = []
    for line in lines:
        try:
            p = json.loads(line)
            if p.get("id") == pid:
                p["status"] = status
                if note:
                    p["note"] = note
                p["updated_at"] = datetime.now().isoformat()
            new_lines.append(json.dumps(p))
        except Exception:
            new_lines.append(line)
    PROPOSALS_LOG.write_text("\n".join(new_lines) + "\n")

# ── ANALYZE PATTERNS ────────────────────────────────────────────────────────

def _analyze_patterns() -> dict:
    stats = {
        "period_start": None, "period_end": None,
        "total_messages": 0, "ungrounded": 0,
        "command_repeats": 0, "generate_commands": 0,
        "vision_fallbacks": 0, "ungrounded_queries": [],
        "repeated_commands": [],
    }
    if not PATTERN_LOG.exists():
        return stats
    for line in PATTERN_LOG.read_text().strip().splitlines():
        try:
            event = json.loads(line)
            ts    = event.get("timestamp", "")
            etype = event.get("type", "")
            data  = event.get("data", {})
            if not stats["period_start"] or ts < stats["period_start"]:
                stats["period_start"] = ts
            if not stats["period_end"] or ts > stats["period_end"]:
                stats["period_end"] = ts
            if etype == "user_message":
                stats["total_messages"] += 1
            elif etype == "ungrounded_response":
                stats["ungrounded"] += 1
                q = data.get("query", "")
                if q and len(stats["ungrounded_queries"]) < 20:
                    stats["ungrounded_queries"].append(q[:120])
            elif etype == "command_repeat":
                stats["command_repeats"] += 1
                t = data.get("text", "")
                if t and len(stats["repeated_commands"]) < 10:
                    stats["repeated_commands"].append(t[:80])
            elif etype == "generate_command":
                stats["generate_commands"] += 1
            elif etype == "vision_fallback":
                stats["vision_fallbacks"] += 1
        except Exception:
            pass
    if stats["total_messages"] > 0:
        stats["ungrounded_rate"] = round(stats["ungrounded"] / stats["total_messages"], 3)
    else:
        stats["ungrounded_rate"] = 0.0
    return stats

# ── FUNZIONI HELPER per update-routing ────────────────────────────────────

def _load_ungrounded_queries(min_frequency: int = MIN_FREQUENCY,
                             max_queries: int = MAX_QUERIES_TO_PROCESS) -> list:
    """
    Legge patterns.jsonl e restituisce le query non coperte più frequenti.
    Ritorna una lista di dict: [{"query": str, "count": int}, ...]
    Ordinata per conteggio decrescente, limitata a max_queries.
    """
    if not PATTERN_LOG.exists():
        return []
    
    query_counts: dict = {}
    for line in PATTERN_LOG.read_text().strip().splitlines():
        try:
            event = json.loads(line)
            if event.get("type") != "ungrounded_response":
                continue
            q = event.get("data", {}).get("query", "").strip()
            if q:
                query_counts[q] = query_counts.get(q, 0) + 1
        except Exception:
            pass
    
    filtered = [(q, c) for q, c in query_counts.items() if c >= min_frequency]
    filtered.sort(key=lambda x: x[1], reverse=True)
    filtered = filtered[:max_queries]
    
    return [{"query": q, "count": c} for q, c in filtered]


def _classify_query(query: str, ollama_url: str, timeout: int = CLASSIFIER_TIMEOUT_S) -> Optional[str]:
    """
    Classifica una query in uno degli agenti validi usando LLM on-demand.
    Tenta prima con qwen3.5:9b, poi con llama3.2:3b in caso di timeout/errore.
    Restituisce il nome dell'agente validato o None se la classificazione fallisce.
    """
    models_to_try = [
        ("qwen3.5:9b", {"num_ctx": 512, "num_predict": 20, "keep_alive": 0}),
        ("llama3.2:3b", {"num_ctx": 512, "num_predict": 20, "keep_alive": 600}),
    ]
    
    prompt = (
        "Classify this query into exactly one of these agents: linux_admin, "
        "ml_engineer, comfy_integrator, design_critic, orchestra_dev, coordinator.\n"
        "Reply ONLY with the agent name, no other text.\n"
        f"Query: '{query}'"
    )
    
    for model, opts in models_to_try:
        try:
            resp = requests.post(
                f"{ollama_url}/api/generate",
                json={
                    "model": model,
                    "prompt": prompt,
                    "stream": False,
                    "options": opts,
                },
                timeout=timeout,
            )
            resp.raise_for_status()
            result = resp.json().get("response", "").strip().lower()
            result = result.strip('"\'. \t\n')
            if result in VALID_AGENTS:
                return result
        except Exception as e:
            print(f"[EVOLVER] Classificazione fallita con {model}: {e}", flush=True)
            continue
    
    return None


def _snapshot_routing(qdrant_url: str = QDRANT_URL,
                      collection: str = ROUTING_COLLECTION,
                      snapshots_dir: Path = ROUTING_SNAPSHOTS_DIR) -> Optional[Path]:
    """
    Salva uno snapshot completo della collection di routing su disco.
    Restituisce il Path del file creato, o None in caso di errore.
    """
    if not _QDRANT_AVAILABLE:
        print("[EVOLVER] Qdrant client non disponibile per snapshot", flush=True)
        return None
    
    try:
        client = QdrantClient(url=qdrant_url, timeout=10)
        points, next_offset = client.scroll(
            collection_name=collection,
            with_vectors=True,
            with_payload=True,
            limit=1000,
        )
        all_points = list(points)
        while next_offset:
            points, next_offset = client.scroll(
                collection_name=collection,
                with_vectors=True,
                with_payload=True,
                limit=1000,
                offset=next_offset,
            )
            all_points.extend(points)
        
        snapshots_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        snapshot_path = snapshots_dir / f"routing_{timestamp}.json"
        
        data = {
            "timestamp": timestamp,
            "collection": collection,
            "count": len(all_points),
            "points": [
                {
                    "id": p.id,
                    "payload": p.payload,
                    "vector": p.vector,
                }
                for p in all_points
            ],
        }
        snapshot_path.write_text(json.dumps(data, indent=2))
        print(f"[EVOLVER] Snapshot salvato: {snapshot_path}", flush=True)
        return snapshot_path
    except Exception as e:
        print(f"[EVOLVER] Errore snapshot: {e}", flush=True)
        return None


def _restore_routing_from_backup(backup_path: Path,
                                 qdrant_url: str = QDRANT_URL,
                                 collection: str = ROUTING_COLLECTION) -> bool:
    """
    Ripristina la collection di routing da un file di snapshot JSON.
    Cancella tutti i punti esistenti e reinserisce quelli del backup.
    Restituisce True se il ripristino è riuscito.
    """
    if not _QDRANT_AVAILABLE:
        print("[EVOLVER] Qdrant client non disponibile per ripristino", flush=True)
        return False
    if not backup_path.exists():
        print(f"[EVOLVER] File backup non trovato: {backup_path}", flush=True)
        return False
    
    try:
        data = json.loads(backup_path.read_text())
        points_raw = data.get("points", [])
        if not points_raw:
            print("[EVOLVER] Backup vuoto, annullato", flush=True)
            return False
        
        client = QdrantClient(url=qdrant_url, timeout=10)
        # Svuota la collection
        client.delete(collection_name=collection, points_selector=Filter())
        
        original_points = [
            PointStruct(
                id=p["id"],
                vector=p["vector"],
                payload=p["payload"],
            )
            for p in points_raw
        ]
        client.upsert(collection_name=collection, points=original_points)
        print(f"[EVOLVER] Ripristino completato: {len(original_points)} punti da {backup_path.name}", flush=True)
        return True
    except Exception as e:
        print(f"[EVOLVER] Errore ripristino: {e}", flush=True)
        return False


# ─────────────────────────────────────────────────────────────────────────────
# CLASSE PRINCIPALE (no Pipeline, importata come modulo)
# ─────────────────────────────────────────────────────────────────────────────

class OrchestraEvolver:
    """
    Classe di auto-evoluzione conservativa.
    Istanziata da orchestra_manifold.py via importlib.
    """

    def __init__(self):
        self.ollama_url    = os.environ.get("OLLAMA_URL", "http://ai-ollama-session:11434")
        self.model_analyst = "qwen2.5-coder:14b-instruct-q4_K_M"
        self.autonomy      = 0   # default: solo proposta, mai applicazione
        self._state        = _load_state()

    # ── Entry point dal manifold ──────────────────────────────────────────

    def handle_evolve(self, text: str, role: str = "user") -> Iterator[str]:
        parts   = text.strip().split(None, 2)
        command = parts[1].lower() if len(parts) >= 2 else "status"
        arg     = parts[2].strip() if len(parts) >= 3 else ""

        ADMIN_ONLY = {"apply-code", "rollback", "update-routing", "apply-config", "routing-restore"}
        if command in ADMIN_ONLY and role != "admin":
            yield f"🔒 **`/evolve {command}` riservato agli amministratori.**\n"
            return

        dispatch = {
            "status":           self._cmd_status,
            "analyze":          self._cmd_analyze,
            "apply-code":       lambda: self._cmd_apply_code(arg),
            "rollback":         self._cmd_rollback,
            "update-routing":   self._cmd_update_routing,
            "routing-history":  self._cmd_routing_history,
            "routing-restore":  lambda: self._cmd_routing_restore(arg),
            "apply-config":     self._cmd_apply_config,
        }
        fn = dispatch.get(command)
        if fn:
            yield from fn()
        else:
            yield self._help()

    # ── STATUS ────────────────────────────────────────────────────────────

    def _cmd_status(self) -> Iterator[str]:
        yield "🔄 **Orchestra Evolver — Stato sistema**\n\n"
        s = self._state
        yield f"- Cicli evolutivi: **{s['evolution_cycles']}**\n"
        yield f"- Livello autonomia: **{self.autonomy}** (0=solo proposte)\n"
        yield f"- Ultimo aggiornamento: {s['last_updated'][:19]}\n\n"

        pending = _load_proposals(status="pending")
        yield f"**Proposte pendenti:** {len(pending)}\n\n"
        if pending:
            yield "| ID | File | Rischio | Descrizione |\n"
            yield "|----|------|---------|-------------|\n"
            for p in pending[-8:]:
                risk_e = {"basso": "🟢", "medio": "🟡", "alto": "🔴"}.get(p.get("risk", ""), "⚪")
                yield (
                    f"| {p['id']} | `{p.get('file', '?')}` | "
                    f"{risk_e} {p.get('risk', '?')} | "
                    f"{p.get('description', '')[:45]} |\n"
                )
            yield "\n"

        backups = _list_backups()
        if backups:
            yield f"**Backup disponibili:** {len(backups)}\n"
            yield f"Più recente: `{backups[0]['file']}` — {backups[0]['created']}\n\n"
        else:
            yield "**Backup:** nessuno ancora.\n\n"

        yield self._help()

    # ── ANALYZE ───────────────────────────────────────────────────────────

    def _cmd_analyze(self) -> Iterator[str]:
        yield "🔬 **Analisi sistema e generazione proposte**\n\n"
        stats = _analyze_patterns()

        if stats["period_start"]:
            yield f"**Periodo:** {stats['period_start'][:10]} → {stats['period_end'][:10]}\n"
        else:
            yield "⚠️ Nessun evento nel log. Inizia ad usare Orchestra per raccogliere dati.\n\n"
            return

        yield f"- Messaggi: {stats['total_messages']}\n"
        yield f"- Risposte senza RAG: {stats['ungrounded']} ({stats.get('ungrounded_rate', 0):.1%})\n"
        yield f"- /generate: {stats['generate_commands']}\n"
        yield f"- Vision fallback: {stats['vision_fallbacks']}\n"
        yield f"- Comandi ripetuti: {stats['command_repeats']}\n\n"

        if stats["ungrounded_queries"]:
            yield "**Esempi query senza RAG:**\n"
            for q in stats["ungrounded_queries"][-3:]:
                yield f"  - `{q}`\n"
            yield "\n"

        yield "🤖 *Generazione proposte con ORCHESTRA_DEV...*\n\n"
        yield from self._generate_proposals_llm(stats)

    def _generate_proposals_llm(self, stats: dict) -> Iterator[str]:
        prompt = f"""Sei ORCHESTRA_DEV. Analizza le metriche di Orchestra e genera proposte CONSERVATIVE.

Metriche:
- Risposte senza RAG: {stats['ungrounded']} ({stats.get('ungrounded_rate', 0):.1%})
- Vision fallback: {stats['vision_fallbacks']}
- Comandi ripetuti: {stats['command_repeats']}
- Query tecniche senza RAG (esempi): {stats['ungrounded_queries'][-3:]}

REGOLE ASSOLUTE per le proposte:
1. Ogni proposta modifica UNA SOLA funzione in UN SOLO file
2. La modifica deve essere < 15 righe di codice
3. Nessuna modifica alle interfacce pubbliche (pipe, inlet, outlet)
4. Nessuna aggiunta di dipendenze esterne
5. Rischio ALTO = non proporre (troppo rischioso per applicazione automatica)

File modificabili: rag_filter.py, orchestra_manifold.py, image_loop.py, embedding_utils.py, pattern_logger.py

Rispondi SOLO con JSON array (niente markdown, niente spiegazioni):
[
  {{
    "file": "nome_file.py",
    "function": "nome_funzione",
    "description": "descrizione breve (max 60 char)",
    "problem": "problema specifico osservato",
    "solution": "soluzione concisa",
    "risk": "basso",
    "code_diff": "snippet Python della modifica (max 15 righe)"
  }}
]"""

        try:
            resp = requests.post(
                f"{self.ollama_url}/api/generate",
                json={
                    "model":   self.model_analyst,
                    "prompt":  prompt,
                    "stream":  False,
                    "options": {"num_ctx": 4096, "num_predict": 2048, "keep_alive": 0},
                },
                timeout=300,
            )
            raw = resp.json().get("response", "[]").strip()

            # Pulizia JSON
            if "```" in raw:
                for part in raw.split("```"):
                    if part.startswith("json"):
                        raw = part[4:].strip()
                        break
                    elif "[" in part:
                        raw = part.strip()
                        break
            start = raw.find("[")
            end   = raw.rfind("]") + 1
            if start >= 0 and end > start:
                raw = raw[start:end]

            proposals = json.loads(raw)
            saved_ids = []
            for p in proposals:
                if isinstance(p, dict):
                    if p.get("file") not in ALLOWED_FILES:
                        continue
                    if p.get("risk", "alto") == "alto":
                        continue   # rifiuta proposte ad alto rischio
                    pid = _write_proposal(p)
                    saved_ids.append(pid)

            yield f"✅ **{len(saved_ids)} proposte salvate** (ID: {saved_ids})\n\n"
            for p in proposals:
                if p.get("file") not in ALLOWED_FILES:
                    continue
                risk_e = {"basso": "🟢", "medio": "🟡"}.get(p.get("risk", ""), "⚪")
                yield f"{risk_e} **`{p.get('file')}`** — {p.get('description', '')}\n"
                yield f"   📍 Funzione: `{p.get('function', '?')}`\n"
                yield f"   ⚠️ Problema: {p.get('problem', '')}\n"
                yield f"   💡 Soluzione: {p.get('solution', '')}\n\n"

            if self.autonomy < 3:
                yield (
                    "> 💡 Per applicare una proposta: `/evolve apply-code <ID>` (admin)\n"
                    "> Il sistema eseguirà backup automatico e smoke test prima di applicare.\n"
                )

        except json.JSONDecodeError as e:
            yield f"⚠️ Parsing JSON fallito: {e}\n"
            yield "Il LLM non ha risposto in formato JSON. Riprova con `/evolve analyze`.\n"
        except Exception as e:
            yield f"❌ Errore generazione proposte: {e}\n"

    # ── APPLY-CODE ────────────────────────────────────────────────────────

    def _cmd_apply_code(self, arg: str) -> Iterator[str]:
        # Parsea l'ID
        try:
            pid = int(arg.strip())
        except (ValueError, AttributeError):
            yield "❌ Specifica l'ID: `/evolve apply-code <N>`\n"
            return

        yield f"🔧 **Applicazione proposta #{pid}**\n\n"

        # Carica proposta
        proposals = _load_proposals()
        proposal  = next((p for p in proposals if p.get("id") == pid), None)
        if not proposal:
            yield f"❌ Proposta #{pid} non trovata.\n"
            return
        if proposal.get("status") != "pending":
            yield f"⚠️ Proposta #{pid} in stato `{proposal['status']}` — non applicabile.\n"
            return

        filename = proposal.get("file", "")
        if filename not in ALLOWED_FILES:
            yield f"❌ `{filename}` non nella whitelist dei file modificabili.\n"
            _mark_proposal(pid, "rejected", "file non in whitelist")
            return

        filepath = PIPELINES_DIR / filename
        if not filepath.exists():
            yield f"❌ File non trovato: `{filepath}`\n"
            return

        original_code = filepath.read_text()

        # Genera codice modificato via LLM
        yield "📝 Generazione modifica via LLM...\n"
        modified_code = self._generate_modified_code(original_code, proposal)
        if not modified_code:
            yield "❌ Generazione modifica fallita.\n"
            _mark_proposal(pid, "failed", "LLM non ha prodotto codice valido")
            return

        # Validazione
        yield "🔍 Validazione...\n"
        ok, reason = _validate(original_code, modified_code, filename)
        if not ok:
            yield f"❌ Validazione fallita: {reason}\n"
            _mark_proposal(pid, "rejected", reason)
            return
        yield "✅ Validazione: OK\n"

        # Backup
        backup_path = _backup(filepath)
        yield f"💾 Backup: `{Path(backup_path).name}`\n"

        # Scrivi modifica
        filepath.write_text(modified_code)
        yield "📄 Modifica applicata.\n"

        # Smoke test
        yield "🧪 Smoke test...\n"
        ok, msg = _smoke_test(filepath)
        if not ok:
            yield f"❌ Smoke test fallito: {msg}\n"
            yield "🔄 Rollback automatico...\n"
            _restore(backup_path, filepath)
            yield "✅ Rollback completato. File ripristinato.\n"
            _mark_proposal(pid, "rolled_back", msg)
            return

        yield f"✅ Smoke test: {msg}\n\n"

        # Registra nel state
        self._state["applied_changes"].append({
            "timestamp":   datetime.now().isoformat(),
            "proposal_id": pid,
            "description": proposal.get("description", ""),
            "file":        filename,
            "backup":      backup_path,
            "cycle":       self._state["evolution_cycles"],
        })
        self._state["evolution_cycles"] += 1
        _save_state(self._state)
        _mark_proposal(pid, "applied", "smoke test OK")

        yield f"🎉 **Proposta #{pid} applicata con successo!**\n\n"
        yield "> **Importante:** riavviare il container per attivare le modifiche:\n"
        yield "> ```bash\n> docker restart ai-pipelines-session\n> ```\n"

    def _generate_modified_code(self, original: str, proposal: dict) -> Optional[str]:
        prompt = (
            f"Modifica il file Python applicando SOLO questa correzione specifica.\n\n"
            f"File: {proposal.get('file')}\n"
            f"Funzione da modificare: {proposal.get('function')}\n"
            f"Problema: {proposal.get('problem')}\n"
            f"Soluzione: {proposal.get('solution')}\n"
            f"Snippet suggerito:\n{proposal.get('code_diff', '')}\n\n"
            f"REGOLE ASSOLUTE:\n"
            f"1. Rispondi SOLO con il file Python completo\n"
            f"2. NON modificare nulla al di fuori della funzione specificata\n"
            f"3. NON aggiungere import non presenti nell'originale\n"
            f"4. Mantieni tutti i docstring e commenti\n"
            f"5. Niente spiegazioni, solo il codice\n\n"
            f"CODICE ORIGINALE:\n```python\n{original}\n```\n\n"
            f"CODICE MODIFICATO (solo il file completo):"
        )
        try:
            resp = requests.post(
                f"{self.ollama_url}/api/generate",
                json={
                    "model":   self.model_analyst,
                    "prompt":  prompt,
                    "stream":  False,
                    "options": {"num_ctx": 16384, "num_predict": 8192, "keep_alive": 0},
                },
                timeout=300,
            )
            raw = resp.json().get("response", "").strip()
            # Estrai solo il Python
            if "```python" in raw:
                raw = raw.split("```python")[1].split("```")[0].strip()
            elif "```" in raw:
                raw = raw.split("```")[1].split("```")[0].strip()
            return raw if len(raw) > 200 else None
        except Exception as e:
            print(f"[EVOLVER] Errore generazione codice: {e}", flush=True)
            return None

    # ── ROLLBACK ──────────────────────────────────────────────────────────

    def _cmd_rollback(self) -> Iterator[str]:
        yield "🔄 **Rollback — ultimo backup registrato**\n\n"
        changes = self._state.get("applied_changes", [])
        if not changes:
            yield "⚠️ Nessuna modifica registrata. Nulla da ripristinare.\n"
            return
        last = changes[-1]
        fp   = PIPELINES_DIR / last["file"]
        ok   = _restore(last["backup"], fp)
        if ok:
            yield f"✅ Ripristinato: `{last['file']}` da `{Path(last['backup']).name}`\n"
            yield f"   Modifica annullata: {last.get('description', '?')}\n\n"
            yield "> Riavviare: `docker restart ai-pipelines-session`\n"
        else:
            yield f"❌ Rollback fallito. Verifica manualmente il backup:\n"
            yield f"   `{last['backup']}`\n"

    # ── UPDATE-ROUTING (nuova versione completa) ──────────────────────────

    def _cmd_update_routing(self) -> Iterator[str]:
        """Aggiorna gli esempi di routing in Qdrant con backup e rollback automatico."""
        yield "🔀 **Aggiornamento esempi routing**\n\n"

        # Pre-checks
        if not _QDRANT_AVAILABLE:
            yield "❌ Libreria `qdrant_client` non installata nel container.\n"
            return

        # Import dinamico di embed_for_routing (risolve il problema di path)
        sys.path.insert(0, "/app/pipelines")
        try:
            from embedding_utils import embed_for_routing
        except ImportError:
            yield "❌ Funzione `embed_for_routing` non disponibile (modulo non importabile).\n"
            return

        # Snapshot pre-operazione
        yield "💾 Creazione snapshot di sicurezza...\n"
        snapshot_path = _snapshot_routing()
        if snapshot_path is None:
            yield "❌ Impossibile creare lo snapshot. Operazione annullata.\n"
            return
        yield f"✅ Snapshot salvato: `{snapshot_path.name}`\n\n"

        # Carica query non coperte
        yield "📊 Analisi query senza RAG...\n"
        queries = _load_ungrounded_queries()
        if not queries:
            yield "✅ Nessuna query senza RAG con frequenza sufficiente. Routing già ben calibrato.\n"
            return
        yield f"Trovate **{len(queries)}** query candidate.\n\n"

        # Classificazione
        yield "🤖 Classificazione query in corso...\n\n"
        classified = []  # lista di (query, agent)
        for i, entry in enumerate(queries, 1):
            q = entry["query"]
            yield f"  `{i}/{len(queries)}` classificazione: `{q[:60]}{'...' if len(q)>60 else ''}` → "
            agent = _classify_query(q, self.ollama_url)
            if agent:
                yield f"**{agent}**\n"
                classified.append((q, agent))
            else:
                yield f"⚠️ saltata (classificazione fallita)\n"

        if not classified:
            yield "\n❌ Nessuna query classificata con successo.\n"
            return

        yield f"\n✅ **{len(classified)}** query classificate.\n\n"

        # Preparazione nuovi punti
        yield "🧮 Calcolo embedding e preparazione punti...\n"
        client = QdrantClient(url=QDRANT_URL, timeout=10)

        # Trova il massimo ID esistente
        existing_ids = []
        try:
            existing, _ = client.scroll(
                collection_name=ROUTING_COLLECTION,
                with_vectors=False,
                with_payload=False,
                limit=1000,
            )
            existing_ids = [p.id for p in existing]
        except Exception as e:
            yield f"⚠️ Errore lettura ID esistenti: {e}. Uso ID da 1000.\n"

        next_id = max(existing_ids) + 1 if existing_ids else 1000
        new_points = []
        skipped = 0

        for q, agent in classified:
            vec = embed_for_routing(q)
            if vec is None:
                skipped += 1
                continue
            new_points.append(PointStruct(
                id=next_id,
                vector=vec,
                payload={"agent": agent, "source": "auto_routing"},
            ))
            next_id += 1

        if not new_points:
            yield "❌ Nessun embedding calcolato. Operazione annullata.\n"
            return

        yield f"✅ {len(new_points)} punti preparati (ID {new_points[0].id}–{new_points[-1].id}).\n"
        if skipped:
            yield f"⚠️ {skipped} query scartate (embedding fallito).\n"
        yield "\n"

        # Inserimento atomico
        yield "📥 Inserimento in Qdrant...\n"
        try:
            client.upsert(collection_name=ROUTING_COLLECTION, points=new_points)
            yield "✅ Upsert completato.\n"
        except Exception as e:
            yield f"❌ Errore upsert: {e}\n"
            yield "🔄 Ripristino snapshot...\n"
            if _restore_routing_from_backup(snapshot_path):
                yield "✅ Collection ripristinata allo stato precedente.\n"
            else:
                yield "❌ Anche il ripristino è fallito! Contatta un amministratore.\n"
            return

        # Verifica post-inserimento
        yield "🔍 Verifica consistenza...\n"
        try:
            info = client.get_collection(ROUTING_COLLECTION)
            new_count = info.points_count
            old_count = len(existing_ids)
            expected = old_count + len(new_points)
            if new_count != expected:
                yield f"⚠️ Conteggio errato: attesi {expected}, trovati {new_count}. Ripristino...\n"
                _restore_routing_from_backup(snapshot_path)
                yield "✅ Collection ripristinata.\n"
                return
            yield f"✅ Conteggio OK: {old_count} → {new_count} punti.\n"
        except Exception as e:
            yield f"⚠️ Verifica fallita: {e}. Ripristino...\n"
            _restore_routing_from_backup(snapshot_path)
            yield "✅ Collection ripristinata.\n"
            return

        # Report finale
        yield "\n📊 **Riepilogo aggiornamento**\n\n"
        yield "| Agente | Nuovi esempi |\n"
        yield "|--------|-------------|\n"
        agent_counts = {}
        for p in new_points:
            a = p.payload["agent"]
            agent_counts[a] = agent_counts.get(a, 0) + 1
        for agent in sorted(agent_counts):
            yield f"| {agent} | {agent_counts[agent]} |\n"

        yield f"\n💾 Snapshot pre-operazione: `{snapshot_path.name}`\n"
        yield f"📁 Directory snapshot: `{ROUTING_SNAPSHOTS_DIR}`\n\n"
        yield (
            "> **Importante:** per attivare i nuovi esempi, riavvia il container:\n"
            "> `docker restart ai-pipelines-session`\n"
        )

    # ── ROUTING-HISTORY ────────────────────────────────────────────────────

    def _cmd_routing_history(self) -> Iterator[str]:
        """Elenca gli snapshot di routing disponibili."""
        yield "📜 **Snapshot di routing disponibili**\n\n"
        if not ROUTING_SNAPSHOTS_DIR.exists():
            yield "Nessuno snapshot trovato.\n"
            return
        snapshots = sorted(ROUTING_SNAPSHOTS_DIR.glob("routing_*.json"), reverse=True)
        if not snapshots:
            yield "Nessuno snapshot trovato.\n"
            return
        yield f"Directory: `{ROUTING_SNAPSHOTS_DIR}`\n\n"
        for s in snapshots[:10]:  # ultimi 10
            try:
                data = json.loads(s.read_text())
                count = data.get("count", "?")
                ts = data.get("timestamp", s.stem)
                yield f"- `{s.name}` — {count} punti, {ts}\n"
            except Exception:
                yield f"- `{s.name}` — file non valido\n"
        yield "\nPer ripristinare: `/evolve routing-restore <nome_file>`\n"

    # ── ROUTING-RESTORE ────────────────────────────────────────────────────

    def _cmd_routing_restore(self, filename: str) -> Iterator[str]:
        """Ripristina uno snapshot di routing specifico."""
        if not filename:
            yield "❌ Specifica il nome del file snapshot.\n"
            yield "Esempio: `/evolve routing-restore routing_20260504_153000.json`\n"
            return

        backup_path = ROUTING_SNAPSHOTS_DIR / filename
        if not backup_path.exists():
            yield f"❌ File non trovato: `{backup_path}`\n"
            yield "Usa `/evolve routing-history` per vedere gli snapshot disponibili.\n"
            return

        yield f"🔄 **Ripristino da `{filename}`**\n\n"
        if _restore_routing_from_backup(backup_path):
            yield "✅ Collection ripristinata con successo.\n"
        else:
            yield "❌ Ripristino fallito.\n"

    # ── APPLY-CONFIG ──────────────────────────────────────────────────────

    def _cmd_apply_config(self) -> Iterator[str]:
        yield (
            "⚙️ **Modifica configurazione Valves**\n\n"
            "Per modificare i Valves di una pipeline, usa l'interfaccia OpenWebUI:\n"
            "1. Clicca sull'icona ⚙️ accanto al modello **🎼 Orchestra**\n"
            "2. Modifica i valori desiderati\n"
            "3. Salva\n\n"
            "Le modifiche ai Valves sono immediate e non richiedono riavvio.\n"
        )

    # ── HELP ──────────────────────────────────────────────────────────────

    def _help(self) -> str:
        return (
            "**Comandi disponibili:**\n\n"
            "| Comando | Descrizione | Ruolo |\n"
            "|---------|-------------|-------|\n"
            "| `/evolve status` | Stato, proposte, backup | Tutti |\n"
            "| `/evolve analyze` | Analisi pattern + proposte LLM | Tutti |\n"
            "| `/evolve apply-code N` | Applica proposta N (backup+test) | Admin |\n"
            "| `/evolve rollback` | Ripristina ultimo backup | Admin |\n"
            "| `/evolve update-routing` | Aggiorna esempi Qdrant | Admin |\n"
            "| `/evolve routing-history` | Elenca snapshot routing | Tutti |\n"
            "| `/evolve routing-restore <file>` | Ripristina snapshot routing | Admin |\n"
            "| `/evolve apply-config` | Modifica Valves OpenWebUI | Admin |\n"
        )


# ── Istanza globale (importata da manifold) ───────────────────────────────
class Pipeline(OrchestraEvolver):
    """Alias per compatibilità con il loader generico del manifold."""
    pass
