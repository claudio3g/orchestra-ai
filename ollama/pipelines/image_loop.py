"""
Image Generator Loop v2.8.2 — Orchestra dual-GPU
Pipeline di generazione immagini SDXL con LCM-LoRA e loop di raffinamento.

CHANGELOG v2.8.2 rispetto a v2.8.1 (27/11/2026, prova reale con la 3090 quasi piena):
  B27     Vision: la risposta di llava arrivava tra ``` e spesso troncata, quella di moondream non
          era JSON ("Risposta non in formato JSON", voto fisso 5/10). Ora la richiesta usa lo
          schema JSON di Ollama (format) e la lettura accetta solo un oggetto (prima un JSON lista
          o stringa dava "'str' object has no attribute 'get'"). moondream non sa dare un voto
          (con lo schema restituisce "found": "found" e voti a caso): fa solo la descrizione
          dell'immagine e il voto lo da un modello di testo (judge_model, llama3.2:3b) con lo schema.
  B28     Raffinamento: con poca VRAM sul main il fallback chiedeva sempre num_gpu=12 (2,4 GB) e Ollama
          rispondeva 500 (out of memory): il prompt non cambiava mai. Ora num_gpu dipende dalla VRAM
          libera (0 = CPU), con VRAM scarsa si usa il modello dell'aux (refine_aux_model) se ha spazio, e
          dopo un errore 5xx si riprova una volta su CPU. L'errore compare nella chat invece di un finto
          "Nuovo prompt" uguale al precedente.
  B28     Ciclo: se il voto non migliora il prompt non torna semplicemente al migliore (stesso prompt,
          seme nuovo): si raffina il migliore con le mancanze appena trovate. Dopo l'ultimo draft non si
          raffina piu' (il risultato non verrebbe usato).
  B29     Raffinamento: llama3.2:3b (usato sull'aux con la 3090 piena) rispondeva "I can't help you with
          that. I can, however, provide..." e il preambolo diventava il prompt. Ora anche il raffinamento
          usa uno schema JSON ({"prompt": ...}) e le risposte con frasi di rifiuto o preambolo vengono
          scartate (si usa il suggerimento della vision, con avviso in chat).
  B30     Vision: con llava gia' caricato sulla 4060 la VRAM libera risultava bassa e si ripiegava su
          moondream; ora un modello gia' in memoria (/api/ps) si usa direttamente.
  B31     Deriva del prompt: la vision giudicava ogni draft rispetto al prompt corrente e il raffinamento
          vedeva solo quello, cosi' a ogni passaggio si perdeva un pezzo della richiesta (prova reale:
          "a starship like an ancient galleon over saturn ring" → "A digital art piece of a starship...",
          galeone sparito). Ora la vision giudica sempre la richiesta dell'utente e il raffinamento la
          riceve con l'obbligo di mantenerne tutti gli elementi.
  B26     VRAM della 4060: senza lo snapshot per ruolo (rag_service irraggiungibile) si leggeva la VRAM
          della 3090 anche per l'aux e si sceglieva sempre moondream. Ora c'e' un ripiego nvidia-smi sulla
          GPU ORCHESTRA_GPU_AUX (la causa, host.docker.internal non risolto, e' corretta nel launcher).

CHANGELOG v2.8.1 rispetto a v2.8.0 (23-24/11/2026):
  IMG-INLINE L'immagine finale viene incorporata nel messaggio (data URI) invece del link
          https://orchestra.tregambe.com/comfyui/view, che da fuori casa non si apriva
          (il browser doveva raggiungere ComfyUI direttamente). Convertita in JPEG (Pillow,
          valvole final_inline_format / final_inline_quality); se la conversione non
          riesce o non riduce resta PNG. Il file originale resta nell'output di ComfyUI (nome nel messaggio).
  IMG-INLINE Il data URI viene inviato a pezzi (final_inline_chunk_chars, 32768): Open WebUI legge
          lo stream delle pipeline a righe di al massimo 131072 byte e con un pezzo unico
          rispondeva "400 Got more than 131072 bytes when reading" (prova reale del 23/11).

CHANGELOG v2.8.0 rispetto a v2.7.0 (RTX 3090 "main" + RTX 4060 "aux"):
  EGPU-05 Backend per ruolo: vision (llava/moondream) sull'Ollama aux (4060) se
          configurato (ollama_url_aux / aux_models), refine sul main; failover sul
          main se l'aux non risponde. Con ollama_url_aux vuoto nulla cambia.
  EGPU-05 La VRAM per scegliere vision/refine si legge dalla GPU su cui il modello
          gira davvero (get_gpu_free_mb("aux"|"main")).
  EGPU-05 ComfyUI viene svuotato (/free) solo se condivide la GPU col modello che sta
          per girare E la VRAM libera non basta (comfy_role). Con ComfyUI e LLM su
          GPU diverse, o con 24 GB liberi, SDXL resta caricato tra un'iterazione e
          l'altra: niente ricaricamenti. Con GPU piccole il comportamento 8 GB resta
          quello di prima (la VRAM libera e' sotto soglia, quindi si svuota).
  EGPU-05 Pre-caricamento in parallelo ai draft di vision (aux) e refine (main) quando
          c'e' VRAM abbondante: nasconde il tempo di caricamento sul link Thunderbolt.
  EGPU-05 A fine generazione ComfyUI viene svuotato (comfy_free_on_finish) sulle GPU grandi: SDXL restava in VRAM
          (circa 7 GB) e costringeva un LLM grande su CPU. Durante il loop SDXL resta caricato.
  EGPU-05 BUG: keep_alive era dentro options (Ollama lo ignora li': e' un parametro di
          primo livello); funzionava solo grazie a OLLAMA_KEEP_ALIVE=0 dell'istanza.
          Ora e' un parametro corretto: 0 se il modello condivide la GPU con ComfyUI
          e la VRAM e' scarsa, altrimenti loop_keep_alive_s; a fine generazione i
          modelli tenuti in memoria vengono scaricati.
  EGPU-05 Fallback subprocess VRAM: prima riga / GPU main (con 2 GPU int() falliva).

CHANGELOG v2.7.0 rispetto a v2.6.0:
  EVO-01  vram_free_mb(): usa get_vram_free_mb() da embedding_utils invece di
          invocare subprocess direttamente. Il thread daemon aggiornato ogni 5s
          riduce la latenza da ~100ms per chiamata a 0ms (lettura variabile globale).
          Fallback al subprocess originale se embedding_utils non è importabile
          (backward compatibility garantita).

CHANGELOG v2.6.0 rispetto a v2.5.3:
  FIX-01  pipe()/generate(): draft failure nelle iterazioni 2+ non abortisce
          più il loop con return. Si usa break per uscire dal loop e procedere
          alla generazione finale con il miglior draft accumulato.

  FIX-02  _extract_json_from_response(): la ricerca del JSON nelle fence
          Markdown ora privilegia sempre il blocco "```json".

  FIX-03  _select_vision_params() e _select_refine_params(): aggiunto guard
          contro divisione per zero quando le soglie VRAM sono uguali.

  FIX-04  generate(): messaggio di errore chiaro quando tutti i draft falliscono.

  FIX-05  pipe(): log del fallback quando final_image è None ma best_bytes disponibile.
"""

import base64
import copy
import json
import os
import re
import subprocess
import threading
import time
import uuid
from typing import Iterator, Optional, Tuple, Union

import requests
from pydantic import BaseModel

try:
    from pattern_logger import log_event
except ImportError:
    def log_event(*args, **kwargs): pass

# EVO-01: usa il VRAM daemon centralizzato di embedding_utils.
# Fallback al subprocess locale se embedding_utils non è disponibile.
try:
    from embedding_utils import get_vram_free_mb as _daemon_vram_free_mb
    _VRAM_DAEMON_AVAILABLE = True
except ImportError:
    _VRAM_DAEMON_AVAILABLE = False

try:
    # EGPU-05: VRAM per ruolo (main/aux). Assente con un embedding_utils vecchio.
    from embedding_utils import get_gpu_free_mb as _daemon_gpu_free_mb
    from embedding_utils import get_gpu_snapshot as _daemon_gpu_snapshot
except ImportError:
    _daemon_gpu_free_mb = None


# ── Workflow SDXL+LCM-LoRA ───────────────────────────────────────────────────
SDXL_WORKFLOW_TEMPLATE = {
    "4": {
        "class_type": "CheckpointLoaderSimple",
        "inputs": {"ckpt_name": "sd_xl_base_1.0.safetensors"}
    },
    "10": {
        "class_type": "LoraLoader",
        "inputs": {
            "model":          ["4", 0],
            "clip":           ["4", 1],
            "lora_name":      "lcm-lora-sdxl.safetensors",
            "strength_model": 1.0,
            "strength_clip":  1.0,
        }
    },
    "6": {
        "class_type": "CLIPTextEncode",
        "inputs": {"clip": ["10", 1], "text": "__POSITIVE_PROMPT__"}
    },
    "7": {
        "class_type": "CLIPTextEncode",
        "inputs": {
            "clip": ["10", 1],
            "text": ("blurry, low quality, watermark, text, signature, "
                     "ugly, deformed, bad anatomy, worst quality")
        }
    },
    "13": {
        "class_type": "EmptyLatentImage",
        "inputs": {"width": "__WIDTH__", "height": "__HEIGHT__", "batch_size": 1}
    },
    "14": {
        "class_type": "KSamplerAdvanced",
        "inputs": {
            "model":                      ["10", 0],
            "positive":                   ["6", 0],
            "negative":                   ["7", 0],
            "latent_image":               ["13", 0],
            "sampler_name":               "lcm",
            "scheduler":                  "sgm_uniform",
            "steps":                      "__STEPS__",
            "cfg":                        1.5,
            "noise_seed":                 "__SEED__",
            "start_at_step":              0,
            "end_at_step":                "__STEPS__",
            "add_noise":                  "enable",
            "return_with_leftover_noise": "disable",
        }
    },
    "8": {
        "class_type": "VAEDecode",
        "inputs": {"samples": ["14", 0], "vae": ["4", 2]}
    },
    "16": {
        "class_type": "SaveImage",
        "inputs": {"images": ["8", 0], "filename_prefix": "__FILENAME_PREFIX__"}
    },
}


def _build_workflow(
    prompt: str, width: int = 512, height: int = 512,
    steps: int = 4, seed: Optional[int] = None,
    filename_prefix: str = "orchestra_draft"
) -> Tuple[dict, int]:
    """Compila il workflow SDXL e restituisce (workflow, seed_usato)."""
    import random
    wf = copy.deepcopy(SDXL_WORKFLOW_TEMPLATE)
    if seed is None:
        seed = random.randint(0, 2**32 - 1)
    wf["6"]["inputs"]["text"]             = prompt
    wf["13"]["inputs"]["width"]           = width
    wf["13"]["inputs"]["height"]          = height
    wf["14"]["inputs"]["steps"]           = steps
    wf["14"]["inputs"]["end_at_step"]     = steps
    wf["14"]["inputs"]["noise_seed"]      = seed
    wf["16"]["inputs"]["filename_prefix"] = filename_prefix
    return wf, seed


def _immagine_incorporata(png_bytes: bytes, formato: str = "jpeg", qualita: int = 90) -> Tuple[str, bytes]:
    """Restituisce (mime, byte) per il data URI. Converte con Pillow; se non riesce o non riduce, PNG originale."""
    formato = (formato or "png").lower()
    if formato in ("jpeg", "jpg", "webp"):
        try:
            import io
            from PIL import Image
            with Image.open(io.BytesIO(png_bytes)) as im:
                if formato in ("jpeg", "jpg"):
                    im = im.convert("RGB")      # JPEG non ha trasparenza
                buf = io.BytesIO()
                pil_fmt = "WEBP" if formato == "webp" else "JPEG"
                im.save(buf, format=pil_fmt, quality=max(1, min(int(qualita), 100)))
            dati = buf.getvalue()
            if len(dati) < len(png_bytes):      # converte solo se conviene
                return ("image/webp" if pil_fmt == "WEBP" else "image/jpeg"), dati
        except Exception as e:
            print(f"[image_loop] conversione immagine non riuscita, uso PNG: {e}")
    return "image/png", png_bytes


# B29: anche il raffinamento risponde con uno schema; frasi di rifiuto o di preambolo scartate.
REFINE_SCHEMA = {"type": "object", "properties": {"prompt": {"type": "string"}}, "required": ["prompt"]}
_REFUSAL_RE = re.compile(
    r"\b(i can'?t|i cannot|i can not|i'?m sorry|i am sorry|as an ai|i won'?t|i'?m unable|i am unable|"
    r"here is|here's)\b", re.I)

# B27: schema JSON passato a Ollama ("format"): la risposta del modello e' sempre un oggetto valido.
ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "score":       {"type": "integer", "minimum": 1, "maximum": 10},
        "found":       {"type": "string"},
        "missing":     {"type": "string"},
        "issues":      {"type": "string"},
        "next_prompt": {"type": "string"},
    },
    "required": ["score", "found", "missing", "issues", "next_prompt"],
}


class Pipeline:

    class Valves(BaseModel):
        model_config = {"protected_namespaces": ()}

        comfyui_url:              str  = "http://172.19.0.1:8188"
        ollama_url:               str  = "http://ai-ollama-session:11434"
        model_vision:             str  = "llava:7b"
        model_vision_fallback:    str  = "moondream:v2"
        model_refine:             str  = "qwen3.5:9b"
        model_refine_fallback:    str  = "qwen2.5-coder:14b-instruct-q4_K_M"
        refine_fallback_num_gpu:  int  = 12      # massimo: il valore vero dipende dalla VRAM libera (B28)
        refine_mb_per_layer:      int  = 200     # VRAM per layer del modello di fallback (14b q4: ~2,4 GB per 12)
        refine_aux_model:         str  = "llama3.2:3b"   # B28: raffinamento sull'aux se il main non ha VRAM
        refine_aux_min_free_mb:   int  = 2500
        judge_model:              str  = "llama3.2:3b"   # B27: voto dalla descrizione quando la vision e' moondream

        draft_width:              int  = 512
        draft_height:             int  = 512
        draft_steps:              int  = 4
        draft_max_iter:           int  = 3
        final_width:              int  = 1024
        final_height:             int  = 1024
        final_steps:              int  = 6
        final_high_quality_steps: int  = 12
        early_stop_score:         int  = 9

        vram_vision_full_mb:      int  = 5000
        vram_vision_partial_mb:   int  = 3000
        vram_refine_full_mb:      int  = 6000

        # EGPU-05: ruoli GPU. ollama_url_aux vuoto = Ollama aux disattivato.
        ollama_url_aux:           str  = os.environ.get("OLLAMA_AUX_URL", "")
        aux_models:               str  = "llama3.2:3b,moondream:v2,llava:7b"   # CSV, come nel manifold
        aux_health_ttl_s:         int  = 20
        # GPU su cui gira ComfyUI: "main" (3090) o "aux" (4060). Impostata dal launcher.
        comfy_role:               str  = os.environ.get("ORCHESTRA_COMFY_ROLE", "main")
        loop_keep_alive_s:        int  = 300     # modelli LLM tenuti caricati durante il loop
        warmup_enabled:           bool = True
        warmup_main_min_free_mb:  int  = 16000   # pre-carica il refine sul main solo con tanta VRAM
        final_min_free_mb:        int  = 6000    # VRAM minima della GPU di ComfyUI per il render finale
        # Se un LLM grande (es. qwen2.5-coder:32b) occupa la GPU di ComfyUI, scaricalo per fare
        # spazio a SDXL invece di attendere e fallire.
        evict_llms_for_comfy:     bool = True
        # A fine generazione svuota ComfyUI (/free) se la GPU e grande. Misurato sull hardware: dopo l uso SDXL
        # lascia circa 7 GB occupati sulla 3090 a riposo, e un LLM da 18 GB (27B) finisce al 20% su CPU
        # (7025 MiB gia presi prima del caricamento). Con GPU piccole resta il comportamento storico.
        comfy_free_on_finish:     bool = True
        comfy_free_min_total_mb:  int  = 16000

        preflight_enabled:        bool = False
        # Immagine finale incorporata nel messaggio: "jpeg" (consigliato), "webp" o "png" (nessuna conversione).
        final_inline_format:      str  = "jpeg"
        final_inline_quality:     int  = 90
        # Ogni evento dello stream deve stare sotto i 131072 byte letti da Open WebUI: il base64 va a pezzi.
        final_inline_chunk_chars: int  = 32768
        comfyui_timeout_s:        int  = 120
        vision_timeout_s:         int  = 120
        refine_timeout_s:         int  = 120

    def __init__(self):
        self.type      = "pipe"
        self.name      = "Image Loop v2.8.2"
        self.id        = "image_loop"
        self.valves    = self.Valves()
        # self.pipelines è richiesto dal framework Pipelines per i pipe autonomi.
        # "*" significa che questo pipe è disponibile per tutte le pipeline.
        self.pipelines = ["*"]
        self._aux_healthy       = False   # EGPU-05: cache del controllo di salute dell'aux
        self._aux_checked_until = 0.0
        self._kept: dict        = {}      # modello -> URL Ollama dove l'abbiamo tenuto caricato
        self.last_refine_error  = ""      # B28: motivo dell'ultimo raffinamento fallito (mostrato in chat)

    # =========================================================================
    # UTILITÀ SISTEMA
    # =========================================================================

    def vram_free_mb(self, role: str = "main") -> int:
        """
        Restituisce la VRAM libera in MB della GPU `role` ("main" = 3090, "aux" = 4060).
        EVO-01: legge dal VRAM daemon di embedding_utils (0ms di latenza).
        EGPU-05: per "aux" usa get_gpu_free_mb("aux"); se non disponibile ricade sul main.
        Fallback: subprocess nvidia-smi (GPU main, prima riga); fallback finale 2000 MB.
        """
        if role == "aux" and _daemon_gpu_free_mb is not None:
            v = _daemon_gpu_free_mb("aux")
            if v > 0:
                print(f"[IMAGE_LOOP] VRAM libera aux (daemon): {v} MB", flush=True)
                return v
        if role == "aux":
            # B26: senza snapshot per ruolo non leggere la VRAM della 3090 al posto della 4060.
            v = self._nvidia_smi_free_mb(os.environ.get("ORCHESTRA_GPU_AUX", "").strip())
            if v is not None:
                print(f"[IMAGE_LOOP] VRAM libera aux (nvidia-smi): {v} MB", flush=True)
                return v
        if _VRAM_DAEMON_AVAILABLE:
            free = _daemon_vram_free_mb()
            print(f"[IMAGE_LOOP] VRAM libera (daemon): {free} MB", flush=True)
            return free
        # fallback subprocess — solo se embedding_utils non e' importabile
        try:
            gpu = (os.environ.get("ORCHESTRA_GPU_MAIN")
                   or os.environ.get("ORCHESTRA_GPU_ID") or "").strip()
            cmd = ["nvidia-smi"] + (["-i", gpu] if gpu else []) + [
                "--query-gpu=memory.free", "--format=csv,noheader,nounits"]
            out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, timeout=3)
            free = int(out.decode().strip().splitlines()[0])   # EGPU-05: prima riga
            print(f"[IMAGE_LOOP] VRAM libera (subprocess): {free} MB", flush=True)
            return free
        except Exception as e:
            print(
                f"[IMAGE_LOOP] nvidia-smi fallito ({e}), uso fallback conservativo 2000MB",
                flush=True
            )
            return 2000

    def _is_loaded(self, model: str) -> bool:
        """True se l'Ollama che serve `model` lo ha gia' in memoria (/api/ps)."""
        try:
            r = requests.get(self._url_for(model).rstrip("/") + "/api/ps", timeout=3)
            names = [m.get("name") or m.get("model") for m in r.json().get("models", [])]
            return model in names
        except Exception:
            return False

    @staticmethod
    def _nvidia_smi_free_mb(gpu: str) -> Optional[int]:
        """VRAM libera di una GPU precisa (UUID), None se non disponibile."""
        if not gpu:
            return None
        try:
            out = subprocess.check_output(
                ["nvidia-smi", "-i", gpu, "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                stderr=subprocess.DEVNULL, timeout=3)
            return int(out.decode().strip().splitlines()[0])
        except Exception:
            return None

    # =========================================================================
    # EGPU-05 — BACKEND PER RUOLO, KEEP-ALIVE E PRE-CARICAMENTO
    # =========================================================================

    def _aux_enabled(self) -> bool:
        return bool(self.valves.ollama_url_aux.strip())

    def _aux_model_set(self) -> set:
        return {m.strip() for m in self.valves.aux_models.split(",") if m.strip()}

    def _aux_ok(self) -> bool:
        """True se l'Ollama aux e' configurato e risponde (esito in cache)."""
        if not self._aux_enabled():
            return False
        now = time.monotonic()
        if now < self._aux_checked_until:
            return self._aux_healthy
        try:
            ok = requests.get(self.valves.ollama_url_aux.rstrip("/") + "/", timeout=1.5).status_code == 200
        except Exception:
            ok = False
        self._aux_healthy       = ok
        self._aux_checked_until = now + self.valves.aux_health_ttl_s
        return ok

    def _mark_aux_down(self) -> None:
        self._aux_healthy       = False
        self._aux_checked_until = time.monotonic() + self.valves.aux_health_ttl_s

    def _role_of(self, model: str) -> str:
        """GPU su cui girera' il modello: 'aux' se servito dall'aux raggiungibile, altrimenti 'main'."""
        return "aux" if (model in self._aux_model_set() and self._aux_ok()) else "main"

    def _url_for(self, model: str) -> str:
        if self._role_of(model) == "aux":
            return self.valves.ollama_url_aux.rstrip("/")
        return self.valves.ollama_url

    def _post_generate(self, model: str, payload: dict, timeout: int):
        """
        POST /api/generate sul backend del modello, con failover aux→main se l'aux e' caduto
        prima di rispondere. Restituisce (risposta, url_usato).
        """
        url = self._url_for(model)
        try:
            return requests.post(f"{url}/api/generate", json=payload, timeout=timeout), url
        except requests.ConnectionError:
            if url != self.valves.ollama_url:
                self._mark_aux_down()
                print(f"[IMAGE_LOOP] Ollama aux non raggiungibile per {model}: failover sul main", flush=True)
                main = self.valves.ollama_url
                return requests.post(f"{main}/api/generate", json=payload, timeout=timeout), main
            raise

    def _keep_alive_for(self, model: str) -> int:
        """
        keep_alive (secondi) per le chiamate del loop. 0 = scarica subito (storico).
        Se il modello gira su una GPU diversa da ComfyUI, o la sua GPU ha VRAM abbondante,
        lo teniamo caricato per le iterazioni successive; il rilascio avviene a fine loop.
        """
        role = self._role_of(model)
        if role != self.valves.comfy_role:
            return self.valves.loop_keep_alive_s
        if self.vram_free_mb(role) >= self.valves.warmup_main_min_free_mb:
            return self.valves.loop_keep_alive_s
        return 0

    def _free_comfy_if_needed(self, role: str, need_mb: int) -> bool:
        """
        Svuota ComfyUI (/free) solo se condivide la GPU `role` col modello che sta per girare
        e la VRAM libera non basta. Con GPU diverse o con VRAM abbondante lascia SDXL caricato
        (il draft successivo parte subito). Restituisce True se ha liberato.
        """
        if self.valves.comfy_role != role:
            print(f"[IMAGE_LOOP] ComfyUI su '{self.valves.comfy_role}', modello su '{role}': nessun /free", flush=True)
            return False
        free = self.vram_free_mb(role)
        if free >= need_mb:
            print(f"[IMAGE_LOOP] VRAM {role} libera {free} MB >= {need_mb} MB: nessun /free", flush=True)
            return False
        self.free_comfyui_vram()
        return True

    def _release_kept(self, only_role: Optional[str] = None) -> None:
        """Scarica (keep_alive=0) i modelli tenuti in memoria dal loop, per liberare la VRAM."""
        for model, url in list(self._kept.items()):
            if only_role is not None and (url == self.valves.ollama_url) != (only_role == "main"):
                continue
            try:
                requests.post(f"{url}/api/generate", json={"model": model, "keep_alive": 0}, timeout=10)
                print(f"[IMAGE_LOOP] scaricato {model} da {url}", flush=True)
            except Exception as e:
                print(f"[IMAGE_LOOP] scarico {model} fallito: {e}", flush=True)
            self._kept.pop(model, None)

    def _gpu_total_mb(self, role: str) -> int:
        """VRAM totale (MB) della GPU del ruolo secondo il daemon; 0 se non disponibile."""
        if _daemon_gpu_snapshot is None:
            return 0
        try:
            return int(_daemon_gpu_snapshot().get(role, {}).get("total_mb", 0))
        except Exception:
            return 0

    def _free_comfy_on_finish(self) -> bool:
        """
        Rilascia la VRAM di ComfyUI a fine generazione (SDXL resta caricato solo DURANTE il loop). Solo su GPU
        grandi: con 8 GB il comportamento resta quello storico. Restituisce True se ha liberato.
        """
        if not self.valves.comfy_free_on_finish:
            return False
        if self._gpu_total_mb(self.valves.comfy_role) < self.valves.comfy_free_min_total_mb:
            return False
        self.free_comfyui_vram()
        return True

    def _evict_llms(self, role: str) -> int:
        """
        Scarica TUTTI i modelli LLM caricati sull'Ollama del ruolo (elenco da /api/ps) per fare
        spazio a SDXL. Restituisce quanti ne ha scaricati.
        """
        url = (self.valves.ollama_url_aux.rstrip("/")
               if (role == "aux" and self._aux_enabled()) else self.valves.ollama_url)
        try:
            models = [m.get("name") or m.get("model")
                      for m in requests.get(f"{url}/api/ps", timeout=5).json().get("models", [])]
        except Exception as e:
            print(f"[IMAGE_LOOP] /api/ps su {url} fallito: {e}", flush=True)
            return 0
        n = 0
        for m in filter(None, models):
            try:
                requests.post(f"{url}/api/generate", json={"model": m, "keep_alive": 0}, timeout=10)
                self._kept.pop(m, None)
                n += 1
                print(f"[IMAGE_LOOP] scaricato {m} da {url} (spazio per SDXL)", flush=True)
            except Exception as e:
                print(f"[IMAGE_LOOP] scarico {m} fallito: {e}", flush=True)
        if n:
            time.sleep(1.5)
        return n

    def _plan_warmup(self) -> list:
        """
        Elenca (modello, url) da pre-caricare mentre ComfyUI genera il draft.
        Sicurezza: se il modello condivide la GPU con ComfyUI serve molta VRAM libera
        (warmup_main_min_free_mb), altrimenti il pre-caricamento ruberebbe memoria a SDXL
        (caso GPU singola da 8 GB: nessun pre-caricamento). Se la GPU e' diversa basta che
        il modello ci stia per intero (soglie full di vision/refine).
        """
        plan = []
        for model, full_mb in ((self.valves.model_vision, self.valves.vram_vision_full_mb),
                               (self.valves.model_refine, self.valves.vram_refine_full_mb)):
            role = self._role_of(model)
            need = self.valves.warmup_main_min_free_mb if role == self.valves.comfy_role else full_mb
            if self.vram_free_mb(role) >= need:
                plan.append((model, self._url_for(model)))
        return plan

    def _warm_up(self, plan: list) -> None:
        """Carica i modelli (prompt vuoto) mentre ComfyUI genera il draft. Errori ignorati."""
        for model, url in plan:
            try:
                requests.post(
                    f"{url}/api/generate",
                    json={"model": model, "prompt": "", "keep_alive": self.valves.loop_keep_alive_s},
                    timeout=180,
                )
                self._kept[model] = url
                print(f"[IMAGE_LOOP] pre-caricato {model} su {url}", flush=True)
            except Exception as e:
                print(f"[IMAGE_LOOP] pre-caricamento {model} fallito (ignorato): {e}", flush=True)

    # =========================================================================
    # SELEZIONE ADATTIVA MODELLI
    # =========================================================================

    def _select_vision_params(self, vram_mb: int) -> Tuple[str, Optional[int]]:
        """
        Seleziona modello vision e num_gpu in base alla VRAM disponibile.
        FIX-03: guard contro divisione per zero se le soglie sono uguali.
        """
        full_th    = self.valves.vram_vision_full_mb
        partial_th = self.valves.vram_vision_partial_mb

        # B30: se il modello vision e' gia' caricato sulla sua GPU la VRAM "libera" non conta (e' quella
        # che occupa lui): prima si ripiegava su moondream proprio quando llava era pronto.
        if self._is_loaded(self.valves.model_vision):
            print(f"[IMAGE_LOOP] {self.valves.model_vision} gia' caricato → uso quello", flush=True)
            return self.valves.model_vision, None

        if vram_mb >= full_th:
            print(f"[IMAGE_LOOP] VRAM={vram_mb}MB → {self.valves.model_vision} full GPU", flush=True)
            return self.valves.model_vision, None

        elif vram_mb >= partial_th and full_th > partial_th:
            # FIX-03: calcola ratio solo se il range è > 0
            total_layers = 32
            ratio   = (vram_mb - partial_th) / (full_th - partial_th)
            num_gpu = max(4, int(ratio * total_layers))
            num_gpu = min(num_gpu, total_layers - 1)
            print(
                f"[IMAGE_LOOP] VRAM={vram_mb}MB → {self.valves.model_vision} num_gpu={num_gpu}",
                flush=True
            )
            return self.valves.model_vision, num_gpu

        else:
            print(
                f"[IMAGE_LOOP] VRAM={vram_mb}MB → fallback {self.valves.model_vision_fallback}",
                flush=True
            )
            return self.valves.model_vision_fallback, None

    def _select_refine_params(self, vram_mb: int) -> Tuple[str, Optional[int]]:
        if vram_mb >= self.valves.vram_refine_full_mb:
            print(f"[IMAGE_LOOP] VRAM={vram_mb}MB → {self.valves.model_refine} full GPU", flush=True)
            return self.valves.model_refine, None
        # B28: con poca VRAM sul main, prima il modello piccolo dell'aux se ha spazio.
        aux_model = self.valves.refine_aux_model.strip()
        if (aux_model and self._role_of(aux_model) == "aux"
                and self.vram_free_mb("aux") >= self.valves.refine_aux_min_free_mb):
            print(f"[IMAGE_LOOP] VRAM main={vram_mb}MB → {aux_model} sull'aux", flush=True)
            return aux_model, None
        # B28: layer in GPU secondo la VRAM davvero libera (prima 12 fissi: out of memory → 500).
        per_layer = max(1, self.valves.refine_mb_per_layer)
        num_gpu = max(0, min(self.valves.refine_fallback_num_gpu, (vram_mb - 500) // per_layer))
        print(f"[IMAGE_LOOP] VRAM={vram_mb}MB → {self.valves.model_refine_fallback} num_gpu={num_gpu}", flush=True)
        return self.valves.model_refine_fallback, num_gpu

    # =========================================================================
    # COMFYUI
    # =========================================================================

    def submit_workflow(self, workflow: dict, client_id: str) -> Optional[str]:
        try:
            resp = requests.post(
                f"{self.valves.comfyui_url}/prompt",
                json={"prompt": workflow, "client_id": client_id},
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json().get("prompt_id")
        except Exception as e:
            print(f"[IMAGE_LOOP] Errore submit workflow: {e}", flush=True)
            log_event("comfyui_submit_error", {"error": str(e)})
            return None

    @staticmethod
    def _check_comfyui_error(history_entry: dict) -> Optional[str]:
        status     = history_entry.get("status", {})
        status_str = status.get("status_str", "")
        if status_str == "error":
            for msg in status.get("messages", []):
                if isinstance(msg, (list, tuple)) and len(msg) >= 2:
                    if msg[0] == "execution_error":
                        return str(msg[1])
            return "ComfyUI execution error (no detail)"
        return None

    def wait_for_result(
        self, prompt_id: str, client_id: str
    ) -> Tuple[Optional[bytes], Optional[dict]]:
        deadline = time.time() + self.valves.comfyui_timeout_s
        while time.time() < deadline:
            try:
                resp = requests.get(
                    f"{self.valves.comfyui_url}/history/{prompt_id}", timeout=10
                )
                resp.raise_for_status()
                history = resp.json()
                if prompt_id in history:
                    err = self._check_comfyui_error(history[prompt_id])
                    if err is not None:
                        print(f"[IMAGE_LOOP] ComfyUI error per {prompt_id}: {err}", flush=True)
                        log_event("comfyui_error", {"prompt_id": prompt_id, "error": err})
                        return None, None
                    outputs = history[prompt_id].get("outputs", {})
                    for node_out in outputs.values():
                        images = node_out.get("images", [])
                        if images:
                            img_info = images[0]
                            img_resp = requests.get(
                                f"{self.valves.comfyui_url}/view",
                                params={
                                    "filename": img_info["filename"],
                                    "subfolder": img_info.get("subfolder", ""),
                                    "type":      img_info.get("type", "output"),
                                },
                                timeout=30,
                            )
                            img_resp.raise_for_status()
                            return img_resp.content, {
                                "filename": img_info["filename"],
                                "subfolder": img_info.get("subfolder", ""),
                                "type":      img_info.get("type", "output"),
                            }
            except Exception as e:
                print(f"[IMAGE_LOOP] Polling error: {e}", flush=True)
            time.sleep(2)
        print(f"[IMAGE_LOOP] Timeout polling ({self.valves.comfyui_timeout_s}s)", flush=True)
        log_event("comfyui_timeout", {"prompt_id": prompt_id})
        return None, None

    def free_comfyui_vram(self) -> None:
        try:
            requests.post(
                f"{self.valves.comfyui_url}/free",
                json={"unload_models": True, "free_memory": True},
                timeout=10,
            )
            time.sleep(1.5)
            print("[IMAGE_LOOP] VRAM ComfyUI liberata.", flush=True)
        except Exception as e:
            print(f"[IMAGE_LOOP] Errore free VRAM: {e}", flush=True)

    def generate_image(
        self, prompt: str, width: int = 512, height: int = 512,
        steps: int = 4, seed: Optional[int] = None,
        filename_prefix: str = "orchestra_draft"
    ) -> Tuple[Optional[bytes], int, Optional[dict]]:
        """Genera un'immagine. Restituisce (bytes, seed_usato, file_info)."""
        client_id = str(uuid.uuid4())
        workflow, used_seed = _build_workflow(
            prompt=prompt, width=width, height=height,
            steps=steps, seed=seed, filename_prefix=filename_prefix,
        )
        prompt_id = self.submit_workflow(workflow, client_id)
        if not prompt_id:
            return None, used_seed, None
        img_bytes, file_info = self.wait_for_result(prompt_id, client_id)
        return img_bytes, used_seed, file_info

    # =========================================================================
    # VISION
    # =========================================================================

    @staticmethod
    def _extract_json_from_response(raw: str) -> Optional[dict]:
        """
        Estrae il primo JSON valido dalla risposta del modello.
        FIX-02: priorità al blocco ```json rispetto a qualsiasi parte con "{".
        Questo evita che testo pre-fence con "{" venga erroneamente selezionato.
        """
        if "```" in raw:
            parts = raw.split("```")
            # Prima priorità: blocco esplicitamente marcato come json
            for part in parts:
                if part.startswith("json"):
                    raw = part[4:].strip()
                    break
            else:
                # Seconda priorità: primo blocco che contiene JSON
                for part in parts:
                    if "{" in part and not part.startswith("json"):
                        raw = part.strip()
                        break

        start = raw.find("{")
        end   = raw.rfind("}") + 1
        if start >= 0 and end > start:
            raw = raw[start:end]
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return None
        # B27: solo un oggetto e' un'analisi (moondream rispondeva con una lista o una stringa).
        return data if isinstance(data, dict) else None

    @staticmethod
    def _usable_prompt(text: str) -> bool:
        """
        Un next_prompt proveniente dal modello vision e' utilizzabile solo se e' un testo vero:
        non vuoto, piu' lungo di 15 caratteri (stessa soglia di refine_prompt) e senza i segnaposto
        <...> dello schema. Altrimenti si tiene il prompt corrente invece di perdere il soggetto
        dell'utente (bug del 15 ottobre 2026: /generate "un cubo rosso" produceva una rosa).
        """
        t = (text or "").strip()
        return len(t) > 15 and "<" not in t and ">" not in t

    def _parse_vision_analysis(self, raw: str, fallback_prompt: str) -> dict:
        """
        Analisi robusta della risposta vision.
        1. Tenta estrazione JSON strutturato.
        2. Se fallisce, estrae score e next_prompt con regex.
        3. Se ancora fallisce, restituisce fallback (score=5, next_prompt=originale).
        In ogni ramo, un next_prompt non utilizzabile (vedi _usable_prompt) viene sostituito
        con il prompt corrente.
        """
        data = self._extract_json_from_response(raw)
        if data:
            nxt = str(data.get("next_prompt", ""))
            try:
                score = int(str(data.get("score", 5)).split("/")[0].strip())
            except ValueError:
                score = 5
            return {
                "score":       max(1, min(10, score)),
                "found":       str(data.get("found", "")),
                "missing":     str(data.get("missing", "")),
                "issues":      str(data.get("issues", "")),
                "next_prompt": nxt if self._usable_prompt(nxt) else fallback_prompt,
            }
        # Fallback regex per estrarre almeno score e next_prompt
        score_match = re.search(r'"score"\s*:\s*(\d+)', raw)
        next_match  = re.search(r'"next_prompt"\s*:\s*"([^"]+)"', raw)
        return {
            "score":       int(score_match.group(1)) if score_match else 5,
            "found":       "",
            "missing":     "",
            "issues":      "Risposta non in formato JSON",
            "next_prompt": (next_match.group(1)
                            if next_match and self._usable_prompt(next_match.group(1))
                            else fallback_prompt),
        }

    def analyze_image_vision(
        self, image_bytes: bytes, prompt: str,
        model: str = "llava:7b", num_gpu: Optional[int] = None
    ) -> dict:
        """Analizza l'immagine con un modello vision (richiede JSON strutturato)."""
        b64 = base64.b64encode(image_bytes).decode()
        options: dict = {
            "num_ctx":    4096,
            "temperature": 0.2,   # output più deterministico
        }
        if num_gpu is not None:
            options["num_gpu"] = num_gpu

        # FIX (15 ott 2026): qui c'era un ESEMPIO CONCRETO (rosa rossa). llava:7b lo copiava parola per
        # parola e il ciclo usava quel next_prompt come prompt finale: /generate "un cubo rosso su fondo
        # bianco" produceva una rosa. Ora lo schema contiene solo segnaposto e si chiede di mantenere
        # lo stesso soggetto. Test: tests/test_image_loop.py (sezione anti-contaminazione).
        analysis_prompt = (
            f"Analyze this SDXL-generated image for the prompt: '{prompt}'.\n"
            "You MUST respond with a single JSON object (no markdown, no extra text) "
            "with exactly these keys:\n"
            '{"score": <integer 1-10: how well the image matches the prompt>, '
            '"found": "<elements of the prompt that are visible in the image>", '
            '"missing": "<elements of the prompt that are not visible>", '
            '"issues": "<visual defects you really see>", '
            '"next_prompt": "<improved SDXL prompt>"}\n\n'
            "Describe only what you really see in the image. "
            "The 'next_prompt' must be a concrete, improved SDXL prompt in English "
            "(not a question or request) of maximum 100 words, "
            "that keeps the SAME main subject and the SAME colors as the prompt above."
        )

        try:
            if model == self.valves.model_vision_fallback and self.valves.judge_model.strip():
                return self._analyze_by_caption(b64, prompt, model, options)
            keep = self._keep_alive_for(model)   # EGPU-05: parametro di primo livello
            resp, used_url = self._post_generate(model, {
                "model":      model,
                "prompt":     analysis_prompt,
                "images":     [b64],
                "stream":     False,
                "format":     ANALYSIS_SCHEMA,   # B27: JSON garantito da Ollama
                "keep_alive": keep,
                "options":    options,
            }, self.valves.vision_timeout_s)
            if keep > 0:
                self._kept[model] = used_url
            resp.raise_for_status()
            raw = resp.json().get("response", "{}").strip()
            return self._parse_vision_analysis(raw, prompt)
        except Exception as e:
            print(f"[IMAGE_LOOP] Errore vision ({model}): {e}", flush=True)
            log_event("vision_fallback", {"model": model, "error": str(e)})
            return {
                "score": 5, "found": "", "missing": "",
                "issues": f"errore: {str(e)[:100]}", "next_prompt": prompt,
            }

    def _analyze_by_caption(self, b64: str, prompt: str, model: str, options: dict) -> dict:
        """B27: moondream descrive l'immagine (lo fa bene), il voto lo da judge_model sul testo,
        con lo schema JSON. Cosi' il voto non e' piu' fisso a 5 ne' inventato."""
        keep = self._keep_alive_for(model)
        resp, used_url = self._post_generate(model, {
            "model": model, "prompt": "Describe this image in detail: subjects, objects, setting, colors.",
            "images": [b64], "stream": False, "keep_alive": keep, "options": options,
        }, self.valves.vision_timeout_s)
        if keep > 0:
            self._kept[model] = used_url
        resp.raise_for_status()
        caption = resp.json().get("response", "").strip()
        if not caption:
            raise ValueError("descrizione vuota")
        judge = self.valves.judge_model.strip()
        judge_prompt = (
            f"An image was generated for this prompt: '{prompt}'.\n"
            f"An observer describes the image like this: '{caption[:1500]}'.\n"
            "Compare the description with the prompt and answer with a JSON object: "
            "score (integer 1-10, how well the image matches the prompt), "
            "found (prompt elements present), missing (prompt elements absent), "
            "issues (problems), next_prompt (an improved SDXL prompt in English, max 100 words, "
            "same main subject, that adds what is missing)."
        )
        keep_j = self._keep_alive_for(judge)
        resp, used_url = self._post_generate(judge, {
            "model": judge, "prompt": judge_prompt, "stream": False, "format": ANALYSIS_SCHEMA,
            "keep_alive": keep_j, "options": {"num_ctx": 4096, "temperature": 0.2},
        }, self.valves.vision_timeout_s)
        if keep_j > 0:
            self._kept[judge] = used_url
        resp.raise_for_status()
        result = self._parse_vision_analysis(resp.json().get("response", "{}").strip(), prompt)
        result["caption"] = caption[:300]
        return result

    # =========================================================================
    # REFINEMENT
    # =========================================================================

    def refine_prompt(
        self, original_prompt: str, analysis: dict,
        model: str = "qwen3.5:9b", num_gpu: Optional[int] = None,
        user_request: Optional[str] = None,
    ) -> str:
        """Raffina il prompt usando i risultati dell'analisi vision."""
        options: dict = {"num_ctx": 4096, "num_predict": 200}
        if num_gpu is not None:
            options["num_gpu"] = num_gpu

        refine_prompt_text = (
            "You are an expert SDXL prompt engineer. Improve this prompt based on the analysis.\n\n"
            + (f"User request (every element MUST stay in the prompt): {user_request}\n"
               if user_request and user_request != original_prompt else "")
            + f"Current prompt: {original_prompt}\n"
            f"Score: {analysis['score']}/10\n"
            f"Found: {analysis['found']}\n"
            f"Missing: {analysis['missing']}\n"
            f"Issues: {analysis['issues']}\n\n"
            "Answer with a JSON object with the single key 'prompt': the improved SDXL prompt in English, "
            "under 100 words, with the SAME main subject, adding what is missing, "
            "including quality tags: 8k, highly detailed. No explanations, no apologies."
        )

        self.last_refine_error = ""
        try:
            keep = self._keep_alive_for(model)   # EGPU-05: parametro di primo livello
            payload = {
                "model":      model,
                "prompt":     refine_prompt_text,
                "stream":     False,
                "format":     REFINE_SCHEMA,     # B29: niente preamboli o rifiuti nel prompt
                "keep_alive": keep,
                "options":    options,
            }
            resp, used_url = self._post_generate(model, payload, self.valves.refine_timeout_s)
            if getattr(resp, "status_code", 200) >= 500 and options.get("num_gpu") != 0:
                # B28: tipicamente out of memory sulla GPU: un secondo tentativo su CPU.
                print(f"[IMAGE_LOOP] Refinement {model}: HTTP {resp.status_code}, riprovo su CPU", flush=True)
                payload = {**payload, "options": {**options, "num_gpu": 0}}
                resp, used_url = self._post_generate(model, payload, self.valves.refine_timeout_s)
            if keep > 0:
                self._kept[model] = used_url
            resp.raise_for_status()
            refined = resp.json().get("response", "").strip()
            try:
                data = json.loads(refined)
                if isinstance(data, dict):
                    refined = str(data.get("prompt", "")).strip()
            except (json.JSONDecodeError, ValueError):
                pass                      # modello senza supporto a format: testo semplice
            refined = refined.strip('"\'`')
            if refined.lower().startswith("improved sdxl prompt:"):
                refined = refined[len("improved sdxl prompt:"):].strip()
            if _REFUSAL_RE.search(refined):
                # B29: llama3.2:3b rispondeva "I can't help you with that. I can, however, ..." e il
                # preambolo finiva nel prompt di SDXL.
                print(f"[IMAGE_LOOP] Refinement {model}: risposta scartata (rifiuto o preambolo)", flush=True)
                self.last_refine_error = "risposta non utilizzabile (rifiuto o preambolo)"
                return analysis.get("next_prompt", original_prompt)
            if refined and len(refined) > 15:
                return refined
            return analysis.get("next_prompt", original_prompt)
        except Exception as e:
            print(f"[IMAGE_LOOP] Errore refinement ({model}): {e}", flush=True)
            self.last_refine_error = str(e)[:120]
            return analysis.get("next_prompt", original_prompt)

    # =========================================================================
    # PRE-FLIGHT
    # =========================================================================

    def preflight_optimize(self, prompt: str) -> str:
        if not self.valves.preflight_enabled:
            return prompt
        opt_prompt = (
            "You are an SDXL expert. Rate this prompt 1-5 for completeness, "
            f"then output ONE improved version.\n\nPrompt: {prompt}\n\n"
            "Output format:\nScore: X/5\nImproved: <improved prompt only>"
        )
        try:
            resp, _ = self._post_generate("llama3.2:3b", {   # EGPU-05: coordinator → aux se attivo
                "model":      "llama3.2:3b",
                "prompt":     opt_prompt,
                "stream":     False,
                "keep_alive": 600,
                "options":    {"num_ctx": 2048, "num_predict": 150},
            }, 30)
            raw = resp.json().get("response", "")
            if "Improved:" in raw:
                improved = raw.split("Improved:", 1)[1].strip()
                if improved:
                    return improved
        except Exception as e:
            print(f"[IMAGE_LOOP] Preflight error: {e}", flush=True)
        return prompt

    # =========================================================================
    # ENTRY POINT
    # =========================================================================

    def pipe(
        self, user_message: str, model_id: str, messages: list, body: dict
    ) -> Union[str, Iterator[str]]:

        def _run() -> Iterator[str]:
            original_prompt = user_message.strip()
            yield "🎨 **Image Loop v2.8.2** — avvio generazione SDXL\n\n"
            yield f"📝 *Prompt originale:* `{original_prompt}`\n\n"

            if self.valves.preflight_enabled:
                yield "🔎 Pre-flight: ottimizzazione prompt...\n"
                current_prompt = self.preflight_optimize(original_prompt)
                if current_prompt != original_prompt:
                    yield f"✨ *Prompt ottimizzato:* `{current_prompt}`\n\n"
                else:
                    yield "✅ Prompt già ottimale.\n\n"
            else:
                current_prompt = original_prompt

            # EGPU-05: se la GPU di ComfyUI non ha spazio per SDXL (es. un LLM da 20 GB caricato),
            # scarica gli LLM prima di iniziare, cosi' il primo draft non va in errore.
            if (self.valves.evict_llms_for_comfy
                    and self.vram_free_mb(self.valves.comfy_role) < self.valves.final_min_free_mb):
                n_evicted = self._evict_llms(self.valves.comfy_role)
                if n_evicted:
                    yield f"🧹 Liberati {n_evicted} modelli LLM dalla GPU {self.valves.comfy_role} per fare spazio a SDXL\n\n"

            # EGPU-05: pre-carica vision (aux) e refine (main) in parallelo ai draft.
            if self.valves.warmup_enabled:
                plan = self._plan_warmup()
                if plan:
                    names = ", ".join(f"`{m}`" for m, _ in plan)
                    yield f"⚡ Pre-caricamento in parallelo: {names}\n\n"
                    threading.Thread(target=self._warm_up, args=(plan,), daemon=True).start()

            best_prompt = current_prompt
            best_score  = 0
            best_seed   = None
            best_bytes  = None

            # ── Loop draft ──────────────────────────────────────────────────
            for iteration in range(self.valves.draft_max_iter):
                yield f"---\n### 🔄 Iterazione {iteration + 1}/{self.valves.draft_max_iter}\n\n"
                yield (
                    f"⚙️ Generazione draft {self.valves.draft_width}×{self.valves.draft_height}"
                    f" ({self.valves.draft_steps} step LCM)...\n"
                )

                draft_bytes, used_seed, _ = self.generate_image(
                    prompt=current_prompt,
                    width=self.valves.draft_width,
                    height=self.valves.draft_height,
                    steps=self.valves.draft_steps,
                    filename_prefix=f"orchestra_draft_iter{iteration + 1}",
                )

                # FIX-01: usa break invece di return per non perdere i draft
                # già accumulati nelle iterazioni precedenti.
                if draft_bytes is None:
                    yield "❌ Generazione draft fallita. Esco dal loop, uso il miglior draft ottenuto.\n"
                    break

                yield f"✅ Draft generato (seed={used_seed}, {len(draft_bytes) // 1024} KB)\n\n"
                # EGPU-05: /free solo se ComfyUI condivide la GPU col vision e la VRAM non basta.
                vision_role = self._role_of(self.valves.model_vision)
                self._free_comfy_if_needed(vision_role, self.valves.vram_vision_full_mb)

                vram_after_comfy = self.vram_free_mb(vision_role)
                vision_model, vision_num_gpu = self._select_vision_params(vram_after_comfy)
                yield f"📊 VRAM libera: **{vram_after_comfy} MB** → `{vision_model}`"
                if vision_num_gpu:
                    yield f" num_gpu={vision_num_gpu}"
                yield "\n\n🔍 Analisi vision...\n"

                analysis = self.analyze_image_vision(
                    image_bytes=draft_bytes,
                    prompt=original_prompt,   # B31: si giudica sempre la richiesta dell'utente
                    model=vision_model,
                    num_gpu=vision_num_gpu,
                )

                score = analysis["score"]
                yield f"**Score: {score}/10**\n"
                if analysis["found"]:   yield f"✅ Trovato: {analysis['found']}\n"
                if analysis["missing"]: yield f"❌ Mancante: {analysis['missing']}\n"
                if analysis["issues"]:  yield f"⚠️ Problemi: {analysis['issues']}\n"
                yield "\n"

                # Aggiornamento del miglior draft
                if score > best_score:
                    best_score  = score
                    best_prompt = current_prompt
                    best_seed   = used_seed
                    best_bytes  = draft_bytes
                    yield f"✨ Nuovo miglior draft (score {best_score})\n\n"
                else:
                    # B28: tornare al miglior prompt senza cambiarlo ripeteva lo stesso tentativo (cambiava solo
                    # il seme): ora si raffina il miglior prompt con le mancanze appena trovate.
                    current_prompt = best_prompt
                    if iteration + 1 < self.valves.draft_max_iter:
                        yield f"📉 Score non migliorato (max {best_score}). Raffino il miglior prompt.\n\n"
                    else:
                        yield f"📉 Score non migliorato (max {best_score}).\n\n"

                if score >= self.valves.early_stop_score:
                    yield f"🎯 Score {score}/10 ≥ {self.valves.early_stop_score} — early stop!\n\n"
                    break

                # B28: dopo l'ultimo draft il raffinamento non verrebbe mai usato.
                if iteration + 1 >= self.valves.draft_max_iter:
                    break
                refine_role = self._role_of(self.valves.model_refine)
                self._free_comfy_if_needed(refine_role, self.valves.vram_refine_full_mb)
                vram_after_vision = self.vram_free_mb(refine_role)
                refine_model, refine_num_gpu = self._select_refine_params(vram_after_vision)
                yield f"📊 VRAM dopo vision: **{vram_after_vision} MB** → `{refine_model}`"
                if refine_num_gpu:
                    yield f" num_gpu={refine_num_gpu}"
                yield "\n✏️ Raffinamento prompt...\n"

                previous_prompt = current_prompt
                self.last_refine_error = ""
                current_prompt = self.refine_prompt(
                    original_prompt=current_prompt,
                    analysis=analysis,
                    model=refine_model,
                    num_gpu=refine_num_gpu,
                    user_request=original_prompt,   # B31: il soggetto dell'utente non si perde
                )
                if getattr(self, "last_refine_error", ""):
                    yield f"⚠️ Raffinamento non riuscito ({self.last_refine_error}): uso il suggerimento della vision.\n"
                if current_prompt == previous_prompt:
                    yield "ℹ️ Prompt invariato.\n\n"
                else:
                    yield (
                        f"📝 *Nuovo prompt:* `"
                        f"{current_prompt[:100]}{'...' if len(current_prompt) > 100 else ''}`\n\n"
                    )

            # FIX-04: se nessun draft è stato ottenuto, esce con errore chiaro
            if best_bytes is None and best_score == 0:
                yield "❌ **Nessun draft generato con successo. Generazione annullata.**\n"
                return

            # ── Generazione finale ────────────────────────────────────────
            yield "---\n### 🖼️ Generazione finale ad alta risoluzione\n\n"

            if best_score >= self.valves.early_stop_score and best_seed is not None:
                final_steps = self.valves.final_high_quality_steps
                final_seed  = best_seed
                yield f"✨ **Modalità massimo dettaglio** — seed riutilizzato `{final_seed}`, step={final_steps}\n"
            else:
                final_steps = self.valves.final_steps
                final_seed  = None
                yield f"ℹ️ Modalità standard — nuovo seed casuale, step={final_steps}\n"

            yield (
                f"⚙️ Generazione {self.valves.final_width}×{self.valves.final_height} "
                f"({final_steps} step LCM) con prompt migliore (score {best_score}/10)...\n"
            )
            yield (
                f"📝 *Prompt finale:* `"
                f"{best_prompt[:100]}{'...' if len(best_prompt) > 100 else ''}`\n\n"
            )

            # EGPU-05: il render finale conta la VRAM della GPU di ComfyUI. Se basta (SDXL gia'
            # caricato o 3090 libera) non serve svuotare; altrimenti si scaricano i modelli LLM
            # tenuti dal loop su quella GPU e si svuota ComfyUI, come prima.
            comfy_role = self.valves.comfy_role
            min_free   = self.valves.final_min_free_mb
            if self.vram_free_mb(comfy_role) < min_free:
                self._release_kept(only_role=comfy_role)
                if self.valves.evict_llms_for_comfy:
                    self._evict_llms(comfy_role)
                self.free_comfyui_vram()
                time.sleep(2)
                free_before_final = self.vram_free_mb(comfy_role)
                if free_before_final < min_free:
                    yield f"⚠️ VRAM bassa ({free_before_final} MB), attendo liberazione...\n"
                    for _ in range(15):
                        time.sleep(2)
                        if self.vram_free_mb(comfy_role) >= min_free:
                            break

            final_image, used_final_seed, final_file_info = self.generate_image(
                prompt=best_prompt,
                width=self.valves.final_width,
                height=self.valves.final_height,
                steps=final_steps,
                seed=final_seed,
                filename_prefix="orchestra_final",
            )

            # FIX-05: fallback esplicito con log
            if final_image is None:
                yield "⚠️ Generazione finale fallita — uso miglior draft come risultato.\n"
                log_event("final_generation_fallback", {
                    "best_score": best_score, "best_seed": best_seed
                })
                final_image     = best_bytes
                final_file_info = None

            if final_image:
                # IMG-INLINE: sempre incorporata (niente link a /comfyui/view).
                mime, dati = _immagine_incorporata(
                    final_image, self.valves.final_inline_format, self.valves.final_inline_quality
                )
                b64_final = base64.b64encode(dati).decode()
                if final_file_info:
                    yield f"✅ Generazione completata! ({len(final_image) // 1024} KB, in chat {len(dati) // 1024} KB)\n\n"
                else:
                    yield f"✅ Generazione completata (draft fallback, {len(final_image) // 1024} KB, in chat {len(dati) // 1024} KB)\n\n"
                passo = max(1024, min(int(self.valves.final_inline_chunk_chars), 65536))
                yield f"![Immagine generata](data:{mime};base64,"
                for i in range(0, len(b64_final), passo):
                    yield b64_final[i:i + passo]
                yield ")\n\n"
                if final_file_info:
                    sub = final_file_info.get("subfolder") or ""
                    yield f"**File originale (PNG) in ComfyUI:** `output/{sub + '/' if sub else ''}{final_file_info.get('filename', '')}`\n"
                yield f"**Prompt usato:** `{best_prompt}`\n"
                yield f"**Score miglior draft:** {best_score}/10\n"
                if best_score >= self.valves.early_stop_score and best_seed is not None:
                    yield f"**Seed finale (riutilizzato):** `{used_final_seed}`\n"
                else:
                    yield f"**Seed finale:** `{used_final_seed}`\n"
            else:
                yield "❌ Nessuna immagine disponibile.\n"

        def generate() -> Iterator[str]:
            # EGPU-05: a fine loop (anche in caso di errore o interruzione) scarica i modelli
            # che il loop ha tenuto caricati, cosi' la VRAM torna libera.
            try:
                yield from _run()
            finally:
                self._release_kept()
                self._free_comfy_on_finish()

        return generate()
