"""
Image Generator Loop v2.9.0 — Orchestra dual-GPU
Pipeline di generazione immagini SDXL con LCM-LoRA e loop di raffinamento.

CHANGELOG v2.9.0 rispetto a v2.8.2 (27/11/2026, richiesta dell'utente: scomporre la frase per aiutare i modelli piccoli)
  SCOMP   La richiesta viene scomposta (decompose_request, modello di refine con schema JSON) in elementi
          visivi concreti in inglese (soggetto, dettagli, ambientazione, stile), con ortografia corretta e le
          confusioni da evitare (prompt negativo). Il prompt SDXL e' costruito dagli elementi con i pesi
          di ComfyUI "(testo:1.3)". Esperimento del 27/11 con gli stessi seed: "a starship like an ancient
          galeon flying over saturn ring" → dischi volanti; prompt scomposto e pesato → galeoni con le vele.
  SCOMP   La vision controlla OGNI elemento (presente si/no, schema JSON) invece di un voto globale: il voto
          e' la quota di elementi presenti. Il raffinamento e' deterministico: alza il peso solo degli elementi
          mancanti (weight_step, fino a weight_max), senza far riscrivere la frase al modello piccolo.
  SCOMP   Prova reale: senza il luogo esplicito i draft erano navi sul mare. Ora la scomposizione dichiara il
          luogo (setting) e mette nel negativo il posto dove il soggetto si vede di solito (nave → mare);
          elementi brevi (max 12 parole: SDXL legge blocchi di 77 token); llava controlla un elemento alla
          volta (con la lista intera diceva "presente" a tutto). Poi anche le domande singole si sono rivelate
          suggestive (anelli "presenti" in un'immagine senza anelli): ora llava fa l'inventario di cio' che vede
          senza sapere cosa si cerca e un modello di testo lo confronta con gli elementi e indica gli intrusi
          (cose viste che non appartengono al luogo: ghiaccio e mare in una scena nello spazio), che entrano nel
          negativo per i draft successivi. Negativo ripulito da parole generiche (sky) e da termini della
          richiesta (spaceship per una nave spaziale). Le cose vietate, che entrano nel
          negativo con peso; qwen3.5 senza ragionamento (think false). Il migliore si sceglie prima per
          elementi presenti, poi per meno cose vietate.
  SCOMP   Immagine finale = draft migliore quando i draft sono a piena risoluzione (rigenerare con lo
          stesso seed e piu' passi cambiava la composizione). Negativo verificato da un secondo passaggio
          (tiene solo cio' che contraddice la scena: "stars" era nel negativo di una scena spaziale).
  SCOMP   Draft alla risoluzione finale se la GPU di ComfyUI ha almeno draft_full_res_min_total_mb (3090:
          1024x1024 in circa 3 s) e render finale con il seed del draft migliore: prima i draft erano a
          512 (composizione diversa da quella a 1024) e il finale usava un seed nuovo, buttando la
          composizione valutata. cfg SDXL 2.0 (era 1.5). Se la scomposizione non riesce: ciclo come in v2.8.2.

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
  B33     Lettura del JSON della vision tollerante ad a capo e tabulazioni nelle stringhe (strict=False);
          se resta illeggibile la risposta grezza finisce nel log di Pipelines.
  B32     Trasparenza e controllo VRAM: all'avvio, a ogni iterazione e prima del render finale la chat
          mostra le schede (nome, VRAM libera/totale, GPU di ComfyUI). La scelta di vision e refine usa la
          memoria che serve davvero al modello (dimensione in Ollama + 5% + 300 MB, misurato) invece di soglie fisse,
          dice su quale scheda gira e perche', e dopo la chiamata controlla in /api/ps quanta parte del
          modello e' finita in GPU (avviso se una parte e' su CPU).
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
    _daemon_gpu_snapshot = None   # B32: prima restava indefinito (NameError con embedding_utils vecchio)


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
    filename_prefix: str = "orchestra_draft",
    negative: Optional[str] = None, cfg: Optional[float] = None,
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
    if negative:
        wf["7"]["inputs"]["text"] = negative          # SCOMP: base + confusioni da evitare
    if cfg is not None:
        wf["14"]["inputs"]["cfg"] = cfg
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


# SCOMP: scomposizione della richiesta e controllo per elemento.
DECOMPOSE_SCHEMA = {
    "type": "object",
    "properties": {
        "subject":  {"type": "string"},
        "setting":  {"type": "string"},
        "elements": {"type": "array", "items": {"type": "object", "properties": {
            "name":   {"type": "string"},
            "prompt": {"type": "string"},
            "kind":   {"type": "string", "enum": ["subject", "detail", "setting", "style"]},
        }, "required": ["name", "prompt", "kind"]}},
        "negative": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["subject", "setting", "elements", "negative"],
}
CHECK_SCHEMA = {
    "type": "object",
    "properties": {
        "elements": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "present": {"type": "boolean"}}, "required": ["name", "present"]}},
        "issues":   {"type": "string"},
    },
    "required": ["elements", "issues"],
}
JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "elements":  {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "present": {"type": "boolean"}}, "required": ["name", "present"]}},
        "forbidden": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "present": {"type": "boolean"}}, "required": ["name", "present"]}},
        "intruders": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["elements", "forbidden", "intruders"],
}
INVENTORY_PROMPT = ("List every object and every background element visible in this image, as a comma separated "
                    "list of short nouns. Do not guess.")
BACKGROUND_PROMPT = "Describe the background of this image in one sentence: what is behind and below the main subject?"
NEG_CHECK_SCHEMA = {"type": "object", "properties": {"wrong": {"type": "array", "items": {"type": "string"}}},
                    "required": ["wrong"]}
YES_NO_SCHEMA = {"type": "object", "properties": {"present": {"type": "boolean"}}, "required": ["present"]}
BASE_NEGATIVE = ("blurry, low quality, watermark, text, signature, ugly, deformed, bad anatomy, worst quality")
INITIAL_WEIGHTS = {"subject": 1.3, "setting": 1.2, "detail": 1.1, "style": 1.0}

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
        # SCOMP (v2.9.0): scomposizione della richiesta, pesi per elemento, draft a piena risoluzione.
        decompose_enabled:        bool  = True
        weight_step:              float = 0.2
        weight_max:               float = 1.7
        sdxl_cfg:                 float = 2.0
        draft_full_res_min_total_mb: int = 16000   # GPU di ComfyUI >= 16 GB: draft a 1024 (3090: ~3 s)
        draft_full_res_steps:     int  = 6

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
        self.name      = "Image Loop v2.9.0"
        self.id        = "image_loop"
        self.valves    = self.Valves()
        # self.pipelines è richiesto dal framework Pipelines per i pipe autonomi.
        # "*" significa che questo pipe è disponibile per tutte le pipeline.
        self.pipelines = ["*"]
        self._aux_healthy       = False   # EGPU-05: cache del controllo di salute dell'aux
        self._aux_checked_until = 0.0
        self._kept: dict        = {}      # modello -> URL Ollama dove l'abbiamo tenuto caricato
        self.last_refine_error  = ""      # B28: motivo dell'ultimo raffinamento fallito (mostrato in chat)
        self.last_choice        = ""      # B32: motivo della scelta di vision/refine (mostrato in chat)

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

    @staticmethod
    def _nvidia_smi_info(gpu: str) -> Optional[dict]:
        """B32: nome, VRAM libera e totale di una GPU precisa (UUID) con nvidia-smi."""
        if not gpu:
            return None
        try:
            out = subprocess.check_output(
                ["nvidia-smi", "-i", gpu, "--query-gpu=name,memory.free,memory.total",
                 "--format=csv,noheader,nounits"], stderr=subprocess.DEVNULL, timeout=3)
            name, free, total = [x.strip() for x in out.decode().strip().splitlines()[0].split(",")]
            return {"name": name, "free_mb": int(free), "total_mb": int(total)}
        except Exception:
            return None

    def _gpu_snapshot_all(self) -> dict:
        """B32: {"main": {name, free_mb, total_mb}, "aux": {...}} dal daemon, altrimenti da nvidia-smi."""
        snap: dict = {}
        if _daemon_gpu_snapshot is not None:
            try:
                snap = {k: dict(v) for k, v in _daemon_gpu_snapshot().items()}
            except Exception:
                snap = {}
        for role, env in (("main", "ORCHESTRA_GPU_MAIN"), ("aux", "ORCHESTRA_GPU_AUX")):
            if role in snap or (role == "aux" and not self._aux_enabled()):
                continue
            info = self._nvidia_smi_info(os.environ.get(env, "").strip())
            if info:
                snap[role] = info
        return snap

    @staticmethod
    def _short_gpu_name(name: str) -> str:
        return (name or "").replace("NVIDIA GeForce ", "").replace("NVIDIA ", "").strip() or "GPU"

    def gpu_name(self, role: str) -> str:
        g = self._gpu_snapshot_all().get(role)
        return self._short_gpu_name(g.get("name", "")) if g else role

    def gpu_status_line(self, title: str = "GPU") -> str:
        """B32: riga per la chat con le schede, la VRAM libera/totale e la GPU di ComfyUI."""
        snap = self._gpu_snapshot_all()
        parts = []
        for role in ("main", "aux"):
            g = snap.get(role)
            if g:
                parts.append(f"{role} **{self._short_gpu_name(g.get('name', ''))}** "
                             f"{g.get('free_mb', 0) / 1024:.1f}/{g.get('total_mb', 0) / 1024:.1f} GB liberi")
        if not parts:
            return f"🖥️ **{title}** — dati delle schede non disponibili\n\n"
        return f"🖥️ **{title}** — " + " · ".join(parts) + f" · ComfyUI su {self.valves.comfy_role}\n\n"

    def _model_need_mb(self, model: str, default: int) -> int:
        """B32: VRAM che serve davvero al modello: dimensione in Ollama (/api/tags) + 5% + 300 MB
        (contesto e buffer). Se Ollama non risponde si usa la soglia della valvola."""
        cache = self.__dict__.setdefault("_need_cache", {})
        if model in cache:
            return cache[model]
        try:
            r = requests.get(self._url_for(model).rstrip("/") + "/api/tags", timeout=3)
            for m in r.json().get("models", []):
                if model in (m.get("name"), m.get("model")) and int(m.get("size", 0)) > 0:
                    cache[model] = int(int(m["size"]) / 2**20 * 1.05) + 300
                    return cache[model]
        except Exception:
            pass
        return default

    def _placement(self, model: str) -> Optional[int]:
        """B32: percentuale del modello in GPU secondo /api/ps (None se non e' in memoria)."""
        try:
            r = requests.get(self._url_for(model).rstrip("/") + "/api/ps", timeout=3)
            for m in r.json().get("models", []):
                if model in (m.get("name"), m.get("model")):
                    size, vram = int(m.get("size", 0)), int(m.get("size_vram", 0))
                    return round(100 * vram / size) if size else None
        except Exception:
            pass
        return None

    def _placement_line(self, model: str) -> str:
        pct = self._placement(model)
        if pct is None:
            return ""
        if pct >= 100:
            return f"   ↳ `{model}` in memoria: 100% GPU\n"
        return f"   ↳ ⚠️ `{model}` in memoria: {pct}% GPU, {100 - pct}% su CPU (piu' lento)\n"

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
        # B32: soglia = memoria che serve davvero a llava (dimensione in Ollama), non un numero fisso.
        full_th    = self._model_need_mb(self.valves.model_vision, self.valves.vram_vision_full_mb)
        partial_th = (self.valves.vram_vision_partial_mb if full_th == self.valves.vram_vision_full_mb
                      else int(full_th * 0.6))

        # B30: se il modello vision e' gia' caricato sulla sua GPU la VRAM "libera" non conta (e' quella
        # che occupa lui): prima si ripiegava su moondream proprio quando llava era pronto.
        if self._is_loaded(self.valves.model_vision):
            print(f"[IMAGE_LOOP] {self.valves.model_vision} gia' caricato → uso quello", flush=True)
            self.last_choice = "gia' in memoria"
            return self.valves.model_vision, None

        if vram_mb >= full_th:
            print(f"[IMAGE_LOOP] VRAM={vram_mb}MB → {self.valves.model_vision} full GPU", flush=True)
            self.last_choice = f"servono ~{full_th / 1024:.1f} GB, liberi {vram_mb / 1024:.1f} GB"
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
            self.last_choice = (f"servono ~{full_th / 1024:.1f} GB, liberi {vram_mb / 1024:.1f} GB: "
                                f"{num_gpu} layer in GPU, il resto su CPU")
            return self.valves.model_vision, num_gpu

        else:
            print(
                f"[IMAGE_LOOP] VRAM={vram_mb}MB → fallback {self.valves.model_vision_fallback}",
                flush=True
            )
            fb_need = self._model_need_mb(self.valves.model_vision_fallback, 2000)
            self.last_choice = (f"per `{self.valves.model_vision}` servono ~{full_th / 1024:.1f} GB, "
                                f"liberi {vram_mb / 1024:.1f} GB → ripiego")
            if vram_mb < fb_need:
                self.last_choice += f" (servono ~{fb_need / 1024:.1f} GB anche a questo: in parte su CPU)"
            return self.valves.model_vision_fallback, None

    def _select_refine_params(self, vram_mb: int) -> Tuple[str, Optional[int]]:
        need = self._model_need_mb(self.valves.model_refine, self.valves.vram_refine_full_mb)   # B32
        if self._is_loaded(self.valves.model_refine):
            self.last_choice = "gia' in memoria"
            return self.valves.model_refine, None
        if vram_mb >= need:
            print(f"[IMAGE_LOOP] VRAM={vram_mb}MB → {self.valves.model_refine} full GPU", flush=True)
            self.last_choice = f"servono ~{need / 1024:.1f} GB, liberi {vram_mb / 1024:.1f} GB"
            return self.valves.model_refine, None
        # B28: con poca VRAM sul main, prima il modello piccolo dell'aux se ha spazio.
        aux_model = self.valves.refine_aux_model.strip()
        if aux_model and self._role_of(aux_model) == "aux":
            aux_free = self.vram_free_mb("aux")
            aux_need = self._model_need_mb(aux_model, self.valves.refine_aux_min_free_mb)
            if self._is_loaded(aux_model) or aux_free >= aux_need:
                print(f"[IMAGE_LOOP] VRAM main={vram_mb}MB → {aux_model} sull'aux", flush=True)
                self.last_choice = (f"per `{self.valves.model_refine}` servono ~{need / 1024:.1f} GB, "
                                    f"liberi {vram_mb / 1024:.1f} GB → modello dell'aux")
                return aux_model, None
        # B28: layer in GPU secondo la VRAM davvero libera (prima 12 fissi: out of memory → 500).
        per_layer = max(1, self.valves.refine_mb_per_layer)
        num_gpu = max(0, min(self.valves.refine_fallback_num_gpu, (vram_mb - 500) // per_layer))
        print(f"[IMAGE_LOOP] VRAM={vram_mb}MB → {self.valves.model_refine_fallback} num_gpu={num_gpu}", flush=True)
        self.last_choice = (f"VRAM insufficiente ({vram_mb / 1024:.1f} GB liberi): {num_gpu} layer in GPU, "
                            f"il resto su CPU (lento)")
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
        filename_prefix: str = "orchestra_draft",
        negative: Optional[str] = None,
    ) -> Tuple[Optional[bytes], int, Optional[dict]]:
        """Genera un'immagine. Restituisce (bytes, seed_usato, file_info)."""
        client_id = str(uuid.uuid4())
        workflow, used_seed = _build_workflow(
            prompt=prompt, width=width, height=height,
            steps=steps, seed=seed, filename_prefix=filename_prefix,
            negative=negative, cfg=self.valves.sdxl_cfg,
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
            # B33: strict=False accetta a capo e tabulazioni dentro le stringhe (il grammar di Ollama li
            # lascia passare, json.loads in modalita' stretta no: "Risposta non in formato JSON").
            data = json.loads(raw, strict=False)
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
        print(f"[IMAGE_LOOP] Vision: risposta non JSON ({len(raw)} caratteri): {raw[:300]!r}", flush=True)   # B33
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
    # SCOMP — SCOMPOSIZIONE, PROMPT PESATO, CONTROLLO PER ELEMENTO
    # =========================================================================

    def decompose_request(self, text: str) -> Optional[dict]:
        """Scompone la richiesta in elementi visivi. None se il modello non risponde o la risposta non serve
        (in quel caso il ciclo resta quello di v2.8.2). Nessun esempio concreto nel prompt: un esempio
        verrebbe copiato dal modello (bug del 15/10: il cubo rosso diventato una rosa)."""
        if not self.valves.decompose_enabled or not text.strip():
            return None
        model, num_gpu = self._select_refine_params(self.vram_free_mb(self._role_of(self.valves.model_refine)))
        ask = (
            f"Image request from a user: '{text}'.\n"
            "Split it into the visual elements an image generator must draw. Fix spelling mistakes and translate "
            "to English.\nRules:\n"
            "- setting: the PLACE where the scene happens, in plain words (for example: outer space, underwater, "
            "a forest, a city street), exactly as the request implies; if not said, the most natural place.\n"
            "- elements: 3 to 5, the main subject first. Each has name (2-4 words), prompt (AT MOST 8 words, "
            "concrete and visual: what it looks like) and kind (subject, detail, setting or style). At least one "
            "element of kind setting, whose prompt names the place.\n"
            "- Describe only what the request asks: do not add weather, water, wind, ground or sky that the place "
            "does not have.\n"
            "- negative: 2 to 5 plain nouns that must NOT appear. Include the place where the subject is USUALLY "
            "seen, when the request moves it somewhere else, and common confusions with other objects. Never "
            "objects that could belong to the requested scene, never phrases with 'no'.\n"
            "Only elements that are in the request."
        ).replace("\n", "\n")
        options = {"num_ctx": 4096, "temperature": 0.2}
        if num_gpu is not None:
            options["num_gpu"] = num_gpu
        try:
            resp, used_url = self._post_generate(model, {
                "model": model, "prompt": ask, "stream": False, "think": False, "format": DECOMPOSE_SCHEMA,
                "keep_alive": self._keep_alive_for(model), "options": options,
            }, self.valves.refine_timeout_s)
            resp.raise_for_status()
            data = json.loads(resp.json().get("response", "") or "{}", strict=False)
        except Exception as e:
            print(f"[IMAGE_LOOP] Scomposizione non riuscita ({model}): {e}", flush=True)
            return None
        els = []
        for el in (data.get("elements") or [])[:8] if isinstance(data, dict) else []:
            if not isinstance(el, dict):
                continue
            name = str(el.get("name", "")).replace("_", " ").strip()
            prm = str(el.get("prompt", "")).replace("_", " ").strip()
            kind = el.get("kind") if el.get("kind") in INITIAL_WEIGHTS else "detail"
            if name and prm and not _REFUSAL_RE.search(prm) and "<" not in prm:
                # SCOMP-2: al massimo 12 parole: SDXL legge a blocchi di 77 token e i blocchi dopo il primo
                # contano poco (prova reale: 6 elementi lunghi, lo spazio si e' perso e il galeone era sul mare).
                prm = " ".join(prm.replace("(", "").replace(")", "").split()[:12])
                # SCOMP-3: il nome apre sempre la descrizione (prova reale: "vast icy bands encircling the
                # planet" senza "Saturn" ha prodotto navi tra i ghiacci).
                if name.lower() not in prm.lower():
                    prm = f"{name}, {prm}"
                els.append({"name": name[:60], "prompt": prm, "kind": kind})
        if len(els) < 2:
            return None
        els = els[:5]
        # SCOMP-3: il luogo dichiarato deve stare in un elemento, altrimenti SDXL sceglie da solo (mare, ghiaccio).
        setting = " ".join(str(data.get("setting", "")).replace("_", " ").replace("(", "").replace(")", "").split()[:8])
        if setting and not any(setting.lower() in el["prompt"].lower() for el in els):
            els = els[:4] + [{"name": setting[:60], "prompt": setting, "kind": "setting"}]
        neg = self._clean_negatives([str(n) for n in (data.get("negative") or [])], text, els)
        neg = self._validate_negatives(neg, text, setting, model, options)
        return {"subject": str(data.get("subject", "")).strip(), "setting": setting,
                "elements": els, "negative": neg, "model": model, "request": text}

    def _validate_negatives(self, neg: list, request: str, setting: str, model: str, options: dict) -> list:
        """SCOMP-6: secondo passaggio breve: tiene solo le cose che CONTRADDICONO la scena (prova reale:
        'stars' nel negativo di una scena nello spazio). Se il modello non risponde il negativo resta com'e'."""
        if not neg:
            return neg
        try:
            resp, _ = self._post_generate(model, {
                "model": model, "stream": False, "think": False, "format": NEG_CHECK_SCHEMA,
                "prompt": (f"Requested image: '{request}' (place: {setting or 'not said'}). Candidate things to "
                           f"exclude from the image: {neg}. Return in 'wrong' only the candidates that would "
                           "CONTRADICT the request if they appeared. Things that naturally belong to the requested "
                           "place or subject are not wrong."),
                "keep_alive": self._keep_alive_for(model), "options": {**options, "temperature": 0},
            }, self.valves.refine_timeout_s)
            resp.raise_for_status()
            wrong = json.loads(resp.json().get("response", "") or "{}", strict=False).get("wrong")
            if isinstance(wrong, list):
                low = {str(w).strip().lower() for w in wrong}
                kept = [n for n in neg if n.lower() in low]
                dropped = [n for n in neg if n not in kept]
                if dropped:
                    print(f"[IMAGE_LOOP] Negativo: tolti {dropped} (appartengono alla scena)", flush=True)
                return kept
        except Exception as e:
            print(f"[IMAGE_LOOP] Verifica del negativo non riuscita: {e}", flush=True)
        return neg

    _GENERIC_NEG = {"sky", "background", "image", "picture", "scene", "light", "lighting", "color", "colors"}

    @classmethod
    def _clean_negatives(cls, items: list, request: str, els: list) -> list:
        """SCOMP-5: niente frasi con 'no', niente parole generiche (sky: in una scena nello spazio il cielo
        stellato serve) e niente termini che si sovrappongono alla richiesta (prova reale: 'spaceship' nel
        negativo per una nave spaziale)."""
        # parole della richiesta (senza i verbi in -ing: 'flying saucer' resta un buon negativo per
        # 'flying over saturn') e nomi degli elementi
        req_words = {w for w in re.findall(r"[a-z]{4,}", request.lower()) if not w.endswith("ing")}
        name_words = set(re.findall(r"[a-z]{4,}", " ".join(e["name"] for e in els).lower()))
        wanted = req_words | name_words
        out = []
        for n in items:
            n = n.replace("_", " ").strip()
            low = n.lower()
            words = set(re.findall(r"[a-z]{4,}", low))
            if (not n or low.startswith(("no ", "not ", "without ")) or low in cls._GENERIC_NEG
                    or (words and words <= cls._GENERIC_NEG) or words & wanted
                    # parole composte con la stessa radice finale (spaceship ↔ starship) o contenute in una
                    # parola della richiesta (ship ⊂ starship: prova reale, il soggetto segnato come intruso)
                    or any(len(w) >= 6 and len(v) >= 6 and w[-4:] == v[-4:] for w in words for v in wanted)
                    or any(w != v and (w in v or v in w) for w in words for v in wanted)):
                continue
            if n not in out:
                out.append(n)
        return out[:5]

    @staticmethod
    def initial_weights(decomp: dict) -> dict:
        return {el["name"]: INITIAL_WEIGHTS.get(el["kind"], 1.0) for el in decomp["elements"]}

    @staticmethod
    def build_weighted_prompt(decomp: dict, weights: dict, neg_weights: Optional[dict] = None) -> Tuple[str, str]:
        """(prompt positivo pesato per ComfyUI, prompt negativo pesato)."""
        parts = []
        for el in decomp["elements"]:
            w = weights.get(el["name"], 1.0)
            parts.append(f"({el['prompt']}:{w:.1f})" if abs(w - 1.0) > 0.05 else el["prompt"])
        positive = ", ".join(parts + ["cinematic lighting", "highly detailed", "8k"])
        negs = []
        for n in decomp.get("negative", []):
            w = (neg_weights or {}).get(n, 1.0)
            negs.append(f"({n}:{w:.1f})" if abs(w - 1.0) > 0.05 else n)
        negative = ", ".join([BASE_NEGATIVE] + negs)
        return positive, negative

    def raise_missing(self, weights: dict, missing: list) -> list:
        """Alza il peso degli elementi mancanti. Restituisce [(nome, prima, dopo)]."""
        changes = []
        for name in missing:
            if name in weights:
                old = weights[name]
                new = round(min(self.valves.weight_max, old + self.valves.weight_step), 2)
                if new > old:
                    weights[name] = new
                    changes.append((name, old, new))
        return changes

    def check_elements(self, image_bytes: bytes, decomp: dict, model: str,
                       num_gpu: Optional[int] = None) -> dict:
        """Controllo per elemento: presente si/no. Voto = quota di elementi presenti (0-10)."""
        names = [el["name"] for el in decomp["elements"]]
        listing = "\n".join(f"{i + 1}. {el['name']}: {el['prompt']}" for i, el in enumerate(decomp["elements"]))
        b64 = base64.b64encode(image_bytes).decode()
        options: dict = {"num_ctx": 4096, "temperature": 0.1}
        if num_gpu is not None:
            options["num_gpu"] = num_gpu
        question = ("Look carefully at this image. For each element below say if it is CLEARLY visible "
                    "(present true) or not (present false). Be strict: a similar but different object counts "
                    f"as not present.\n{listing}\nAnswer JSON: elements (same order, with name and present) "
                    "and issues (visible defects, short).")
        if not (model == self.valves.model_vision_fallback and self.valves.judge_model.strip()):
            return self._check_by_inventory(b64, decomp, model, options)
        try:
            if model == self.valves.model_vision_fallback and self.valves.judge_model.strip():
                cap = self._post_generate(model, {
                    "model": model, "prompt": "Describe this image in detail: subjects, objects, setting, colors.",
                    "images": [b64], "stream": False, "keep_alive": self._keep_alive_for(model),
                    "options": options}, self.valves.vision_timeout_s)[0]
                cap.raise_for_status()
                caption = cap.json().get("response", "").strip()
                judge = self.valves.judge_model.strip()
                resp, _ = self._post_generate(judge, {
                    "model": judge, "stream": False, "format": CHECK_SCHEMA,
                    "prompt": (f"An observer describes an image: '{caption[:1500]}'.\nFor each element say if "
                               f"the description shows it is present.\n{listing}\nAnswer JSON: elements (same "
                               "order, with name and present) and issues."),
                    "keep_alive": self._keep_alive_for(judge), "options": {"num_ctx": 4096, "temperature": 0.1},
                }, self.valves.vision_timeout_s)
            else:
                keep = self._keep_alive_for(model)
                resp, used_url = self._post_generate(model, {
                    "model": model, "prompt": question, "images": [b64], "stream": False,
                    "format": CHECK_SCHEMA, "keep_alive": keep, "options": options,
                }, self.valves.vision_timeout_s)
                if keep > 0:
                    self._kept[model] = used_url
            resp.raise_for_status()
            data = json.loads(resp.json().get("response", "") or "{}", strict=False)
            items = data.get("elements") if isinstance(data, dict) else None
            if not isinstance(items, list) or not items:
                raise ValueError("lista elementi assente")
        except Exception as e:
            print(f"[IMAGE_LOOP] Controllo per elemento non riuscito ({model}): {e}", flush=True)
            return {"score": 0, "checks": {}, "found": "", "missing": "", "issues": f"controllo non riuscito: {str(e)[:80]}",
                    "next_prompt": "", "failed": True}
        checks = {}
        for i, name in enumerate(names):
            hit = next((it for it in items if isinstance(it, dict) and str(it.get("name", "")).strip().lower() == name.lower()), None)
            if hit is None and i < len(items) and isinstance(items[i], dict):
                hit = items[i]                      # stesso ordine
            checks[name] = bool(hit.get("present")) if isinstance(hit, dict) else False
        present = [n for n in names if checks[n]]
        missing = [n for n in names if not checks[n]]
        return {"score": round(10 * len(present) / len(names)), "checks": checks,
                "found": ", ".join(present), "missing": ", ".join(missing),
                "issues": str(data.get("issues", ""))[:300], "next_prompt": ""}

    def _ask_yes_no(self, b64: str, what: str, model: str, options: dict,
                    question: Optional[str] = None) -> Optional[bool]:
        """Una sola domanda si/no: llava risponde bene a una domanda alla volta (prova del 27/11: 6 su 6
        corrette, mentre con la lista intera rispondeva 'presente' a tutto)."""
        keep = self._keep_alive_for(model)
        resp, used_url = self._post_generate(model, {
            "model": model, "images": [b64], "stream": False, "format": YES_NO_SCHEMA, "keep_alive": keep,
            "prompt": f"Look at the image. {question or f'Is there {what} clearly visible?'} Answer JSON: present (true or false).",
            "options": {**options, "temperature": 0},
        }, self.valves.vision_timeout_s)
        if keep > 0:
            self._kept[model] = used_url
        resp.raise_for_status()
        data = json.loads(resp.json().get("response", "") or "{}", strict=False)
        v = data.get("present") if isinstance(data, dict) else None
        return v if isinstance(v, bool) else None

    def _vision_text(self, b64: str, prompt: str, model: str, options: dict) -> str:
        keep = self._keep_alive_for(model)
        resp, used_url = self._post_generate(model, {
            "model": model, "prompt": prompt, "images": [b64], "stream": False, "keep_alive": keep,
            "options": {**options, "temperature": 0, "num_predict": 60},
        }, self.valves.vision_timeout_s)
        if keep > 0:
            self._kept[model] = used_url
        resp.raise_for_status()
        return resp.json().get("response", "").strip()

    @staticmethod
    def _words_hit(term: str, text: str) -> bool:
        """Una parola significativa (>3 lettere) del termine compare nel testo (es. 'ocean water' ↔ 'ocean')."""
        t = re.sub(r"[^a-z ]", " ", text.lower())
        return any(re.search(rf"\b{re.escape(w)}s?\b", t) for w in re.sub(r"[^a-z ]", " ", term.lower()).split() if len(w) > 3)

    def _check_by_inventory(self, b64: str, decomp: dict, model: str, options: dict) -> dict:
        """SCOMP-4: llava fa l'inventario SENZA sapere cosa si cerca (le domande si/no lo spingevano a dire si:
        prova reale, 'Saturn ring system' presente in un'immagine senza anelli), poi un modello di testo
        confronta l'inventario con gli elementi. Le cose vietate valgono anche per parola (ocean ↔ ocean water)."""
        names = [el["name"] for el in decomp["elements"]]
        negs = decomp.get("negative", [])[:5]
        try:
            inventory = self._vision_text(b64, INVENTORY_PROMPT, model, options)
            background = self._vision_text(b64, BACKGROUND_PROMPT, model, options)
            if not inventory and not background:
                raise ValueError("inventario vuoto")
            judge, _ = self._select_refine_params(self.vram_free_mb(self._role_of(self.valves.model_refine)))
            listing = "; ".join(f"{el['name']} ({el['prompt']})" for el in decomp["elements"])
            q = (f"An observer looked at an image and listed what is visible: '{inventory[:600]}'. "
                 f"Background: '{background[:300]}'.\nRequired elements: {listing}.\nForbidden items: {negs}.\n"
                 "For each required element, present is true only if the observer clearly mentions it, an obvious "
                 "synonym, or an object of the same kind (for example a ship for any kind of sailing ship, a "
                 "starry black sky for outer space). The request may be fantastic (for example a ship that flies): "
                 "judge the kind of object seen, not whether it is fantastic. For each forbidden item, present is "
                 "true if the observer mentions it or a synonym. intruders: up to 3 plain nouns the observer saw "
                 "that do NOT belong to the requested place"
                 + (f" ({decomp.get('setting')})" if decomp.get("setting") else "")
                 + " (for example sea, snow or ice when the scene is in space). "
                 "Answer JSON with elements, forbidden and intruders, in the same order.")
            resp, _ = self._post_generate(judge, {
                "model": judge, "prompt": q, "stream": False, "think": False, "format": JUDGE_SCHEMA,
                "keep_alive": self._keep_alive_for(judge), "options": {"num_ctx": 4096, "temperature": 0},
            }, self.valves.refine_timeout_s)
            resp.raise_for_status()
            data = json.loads(resp.json().get("response", "") or "{}", strict=False)
            items = data.get("elements") if isinstance(data, dict) else None
            if not isinstance(items, list) or not items:
                raise ValueError("giudizio senza elementi")
        except Exception as e:
            print(f"[IMAGE_LOOP] Controllo per inventario non riuscito ({model}): {e}", flush=True)
            return {"score": 0, "checks": {}, "forbidden": [], "found": "", "missing": "",
                    "issues": f"controllo non riuscito: {str(e)[:80]}", "next_prompt": "", "failed": True}
        checks = {}
        for i, name in enumerate(names):
            hit = next((it for it in items if isinstance(it, dict) and str(it.get("name", "")).strip().lower() == name.lower()), None)
            if hit is None and i < len(items) and isinstance(items[i], dict):
                hit = items[i]
            checks[name] = bool(hit.get("present")) if isinstance(hit, dict) else False
        fitems = data.get("forbidden") if isinstance(data.get("forbidden"), list) else []
        seen = inventory + " " + background
        forbidden = []
        for i, n in enumerate(negs):
            fh = next((it for it in fitems if isinstance(it, dict) and str(it.get("name", "")).strip().lower() == n.lower()), None)
            if fh is None and i < len(fitems) and isinstance(fitems[i], dict):
                fh = fitems[i]
            if (isinstance(fh, dict) and fh.get("present") is True) or self._words_hit(n, seen):
                forbidden.append(n)
        intruders = self._clean_negatives([str(x) for x in (data.get("intruders") or []) if isinstance(x, str)],
                                          decomp.get("request", ""), decomp["elements"])
        intruders = [x for x in intruders if x not in forbidden][:3]
        if intruders:   # SCOMP-6: anche gli intrusi passano dalla verifica (prova reale: 'moon' attorno a Saturno)
            intruders = self._validate_negatives(intruders, decomp.get("request", ""), decomp.get("setting", ""),
                                                 judge, {"num_ctx": 4096})
        present = [n for n, ok in checks.items() if ok]
        missing = [n for n, ok in checks.items() if not ok]
        score = max(0, round(10 * len(present) / len(checks)) - len(forbidden))
        return {"score": score, "checks": checks, "forbidden": forbidden, "intruders": intruders,
                "found": ", ".join(present),
                "missing": ", ".join(missing), "inventory": inventory[:300], "background": background[:200],
                "issues": ("compare: " + ", ".join(forbidden)) if forbidden else "", "next_prompt": ""}

    def _check_one_by_one(self, b64: str, decomp: dict, model: str, options: dict) -> dict:
        checks, forbidden, unknown = {}, [], 0
        try:
            for el in decomp["elements"]:
                # SCOMP-3: solo il nome: con la descrizione llava si faceva guidare dalle parole ("icy" → ghiaccio).
                v = self._ask_yes_no(b64, el["name"], model, options,
                                     question=(f"Does this scene take place in {el['name']}?" if el["kind"] == "setting" else None))
                unknown += v is None
                checks[el["name"]] = bool(v)
            for n in decomp.get("negative", [])[:5]:
                if self._ask_yes_no(b64, n, model, options):
                    forbidden.append(n)
        except Exception as e:
            print(f"[IMAGE_LOOP] Controllo per elemento non riuscito ({model}): {e}", flush=True)
            return {"score": 0, "checks": {}, "forbidden": [], "found": "", "missing": "",
                    "issues": f"controllo non riuscito: {str(e)[:80]}", "next_prompt": "", "failed": True}
        if unknown == len(checks):
            return {"score": 0, "checks": {}, "forbidden": [], "found": "", "missing": "",
                    "issues": "controllo non riuscito: risposte non leggibili", "next_prompt": "", "failed": True}
        present = [n for n, ok in checks.items() if ok]
        missing = [n for n, ok in checks.items() if not ok]
        # Ogni cosa vietata presente toglie 1 punto (un galeone sul mare non e' "nello spazio"); 2 punti erano
        # troppi: llava scambia a volte un pianeta per la Terra (prova reale del 27/11).
        score = max(0, round(10 * len(present) / len(checks)) - len(forbidden))
        return {"score": score, "checks": checks, "forbidden": forbidden, "found": ", ".join(present),
                "missing": ", ".join(missing),
                "issues": ("compare: " + ", ".join(forbidden)) if forbidden else "", "next_prompt": ""}

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
                "think":      False,             # SCOMP-2: qwen3.5 ragionava a lungo e a volte non rispondeva
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
            yield "🎨 **Image Loop v2.9.0** — avvio generazione SDXL\n\n"
            yield f"📝 *Prompt originale:* `{original_prompt}`\n\n"
            yield self.gpu_status_line("Schede all'avvio")   # B32

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

            # SCOMP: scomposizione della richiesta (None → ciclo di v2.8.2).
            decomp = None
            weights: dict = {}
            neg_weights: dict = {}       # SCOMP-2: cose vietate comparse → peso nel negativo
            learned_neg: dict = {}       # SCOMP-5: intrusi imparati dalle immagini (restano fino alla fine)
            negative = None
            if self.valves.decompose_enabled:
                yield "🧩 Scomposizione della richiesta...\n"
                decomp = self.decompose_request(original_prompt)
                if decomp:
                    weights = self.initial_weights(decomp)
                    current_prompt, negative = self.build_weighted_prompt(decomp, weights)
                    yield f"🧩 Elementi (`{decomp['model']}`):\n"
                    for i, el in enumerate(decomp["elements"]):
                        yield f"   {i + 1}. **{el['name']}** ({el['kind']}, peso {weights[el['name']]:.1f}) — {el['prompt']}\n"
                    if decomp["negative"]:
                        yield f"🚫 Da evitare: {', '.join(decomp['negative'])}\n"
                    yield "\n"
                else:
                    yield "ℹ️ Scomposizione non disponibile: uso la frase intera.\n\n"

            full_res = (decomp is not None
                        and self._gpu_total_mb(self.valves.comfy_role) >= self.valves.draft_full_res_min_total_mb)
            d_w = self.valves.final_width if full_res else self.valves.draft_width
            d_h = self.valves.final_height if full_res else self.valves.draft_height
            d_steps = self.valves.draft_full_res_steps if full_res else self.valves.draft_steps

            best_prompt = current_prompt
            best_score  = 0
            best_seed   = None
            best_bytes  = None
            best_info   = None
            best_weights = dict(weights)
            best_neg_weights: dict = {}
            best_missing: list = []      # SCOMP: elementi mancanti nel draft migliore
            best_rank = (-1, 0, -1)
            tries_since_best = 0

            # ── Loop draft ──────────────────────────────────────────────────
            for iteration in range(self.valves.draft_max_iter):
                yield f"---\n### 🔄 Iterazione {iteration + 1}/{self.valves.draft_max_iter}\n\n"
                yield self.gpu_status_line()   # B32
                yield f"⚙️ Generazione draft {d_w}×{d_h} ({d_steps} step LCM)...\n"
                if decomp:
                    yield f"📝 *Prompt:* `{current_prompt[:160]}{'...' if len(current_prompt) > 160 else ''}`\n"

                draft_bytes, used_seed, draft_info = self.generate_image(
                    prompt=current_prompt,
                    width=d_w,
                    height=d_h,
                    steps=d_steps,
                    filename_prefix=f"orchestra_draft_iter{iteration + 1}",
                    negative=negative,
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
                self.last_choice = ""
                vision_model, vision_num_gpu = self._select_vision_params(vram_after_comfy)
                v_role = self._role_of(vision_model)
                yield (f"🔍 Vision: `{vision_model}` su **{self.gpu_name(v_role)}** ({v_role})"
                       f"{' — ' + self.last_choice if self.last_choice else ''}\n")   # B32
                yield "🔍 Analisi vision...\n"

                if decomp:
                    analysis = self.check_elements(draft_bytes, decomp, vision_model, vision_num_gpu)   # SCOMP
                else:
                    analysis = self.analyze_image_vision(
                        image_bytes=draft_bytes,
                        prompt=original_prompt,   # B31: si giudica sempre la richiesta dell'utente
                        model=vision_model,
                        num_gpu=vision_num_gpu,
                    )

                yield self._placement_line(vision_model)   # B32: dove e' finito davvero il modello
                score = analysis["score"]
                yield f"**Score: {score}/10**\n"
                if decomp and analysis.get("checks"):
                    yield "🧾 " + " · ".join(f"{'✅' if ok else '❌'} {n}" for n, ok in analysis["checks"].items()) + "\n"
                    if analysis.get("forbidden"):
                        yield "🚫 Non doveva esserci: " + ", ".join(analysis["forbidden"]) + "\n"
                    if analysis.get("intruders"):
                        yield "🚫 Fuori luogo: " + ", ".join(analysis["intruders"]) + "\n"
                    if analysis.get("background"):
                        yield f"👁️ Visto: {analysis.get('inventory', '')[:160]} — sfondo: {analysis['background'][:160]}\n"
                    if analysis["issues"]: yield f"⚠️ Problemi: {analysis['issues']}\n"
                else:
                    if analysis["found"]:   yield f"✅ Trovato: {analysis['found']}\n"
                    if analysis["missing"]: yield f"❌ Mancante: {analysis['missing']}\n"
                    if analysis["issues"]:  yield f"⚠️ Problemi: {analysis['issues']}\n"
                yield "\n"

                # Aggiornamento del miglior draft
                # SCOMP-2: con la scomposizione conta prima quanti elementi ci sono, poi quante cose vietate.
                if decomp and analysis.get("checks"):
                    rank = (sum(analysis["checks"].values()), -len(analysis.get("forbidden", [])), score)
                else:
                    rank = (score, 0, score)
                if rank > best_rank or best_bytes is None:
                    best_rank   = rank
                    best_score  = score
                    best_prompt = current_prompt
                    best_seed   = used_seed
                    best_bytes  = draft_bytes
                    best_info   = draft_info
                    best_weights = dict(weights)
                    best_neg_weights = dict(neg_weights)
                    best_missing = [n for n, ok in analysis.get("checks", {}).items() if not ok]
                    tries_since_best = 0
                    yield f"✨ Nuovo miglior draft (score {best_score})\n\n"
                else:
                    # B28: tornare al miglior prompt senza cambiarlo ripeteva lo stesso tentativo (cambiava solo
                    # il seme): ora si raffina il miglior prompt con le mancanze appena trovate.
                    current_prompt = best_prompt
                    weights = dict(best_weights)
                    neg_weights = {**learned_neg, **best_neg_weights}
                    tries_since_best += 1
                    if iteration + 1 < self.valves.draft_max_iter:
                        yield f"📉 Score non migliorato (max {best_score}). Raffino il miglior prompt.\n\n"
                    else:
                        yield f"📉 Score non migliorato (max {best_score}).\n\n"

                if score >= self.valves.early_stop_score and not analysis.get("forbidden"):   # SCOMP-2
                    yield f"🎯 Score {score}/10 ≥ {self.valves.early_stop_score} — early stop!\n\n"
                    break

                # B28: dopo l'ultimo draft il raffinamento non verrebbe mai usato.
                if iteration + 1 >= self.valves.draft_max_iter:
                    break

                if decomp:
                    # SCOMP: raffinamento deterministico, solo i pesi degli elementi mancanti.
                    if tries_since_best == 0:
                        missing = [n for n, ok in analysis.get("checks", {}).items() if not ok]
                        changes = self.raise_missing(weights, missing)
                    else:
                        # peggiorato: dai pesi del migliore si alzano di un passo in piu' i SUOI mancanti
                        # (non quelli del draft peggiore, spesso persi per la competizione tra elementi).
                        changes = []
                        for n in best_missing:
                            if n in weights:
                                old = weights[n]
                                new = round(min(self.valves.weight_max,
                                                old + self.valves.weight_step * (tries_since_best + 1)), 2)
                                if new > old:
                                    weights[n] = new
                                    changes.append((n, old, new))
                    for n in analysis.get("intruders", []):
                        # SCOMP-5: il negativo impara dall'immagine (prova reale: ghiaccio e mare in una scena
                        # nello spazio, mai previsti dalla scomposizione).
                        if n not in decomp["negative"]:
                            decomp["negative"].append(n)
                            neg_weights[n] = learned_neg[n] = 1.5
                            yield f"🚫 Fuori luogo «{n}»: aggiunto al negativo con peso 1.5\n"
                    for n in analysis.get("forbidden", []):
                        old = neg_weights.get(n, 1.0)
                        neg_weights[n] = round(min(self.valves.weight_max, max(1.5, old + self.valves.weight_step)), 2)
                        yield f"🚫 Comparso «{n}»: nel negativo con peso {neg_weights[n]:.1f}\n"
                    if changes:
                        yield "⬆️ " + " · ".join(f"{n} {a:.1f} → {b:.1f}" for n, a, b in changes) + "\n\n"
                    elif analysis.get("failed"):
                        yield "ℹ️ Controllo non riuscito: nuovo seed con gli stessi pesi.\n\n"
                    elif not any(n in weights for n in (missing if tries_since_best == 0 else best_missing)):
                        yield "ℹ️ Tutti gli elementi presenti: nuovo seed con il negativo aggiornato.\n\n"
                    else:
                        yield "ℹ️ Pesi gia' al massimo: nuovo seed.\n\n"
                    current_prompt, negative = self.build_weighted_prompt(decomp, weights, neg_weights)
                    continue

                refine_role = self._role_of(self.valves.model_refine)
                self._free_comfy_if_needed(refine_role, self.valves.vram_refine_full_mb)
                vram_after_vision = self.vram_free_mb(refine_role)
                self.last_choice = ""
                refine_model, refine_num_gpu = self._select_refine_params(vram_after_vision)
                r_role = self._role_of(refine_model)
                yield (f"✏️ Raffinamento: `{refine_model}` su **{self.gpu_name(r_role)}** ({r_role})"
                       f"{' — ' + self.last_choice if self.last_choice else ''}\n")   # B32

                previous_prompt = current_prompt
                self.last_refine_error = ""
                current_prompt = self.refine_prompt(
                    original_prompt=current_prompt,
                    analysis=analysis,
                    model=refine_model,
                    num_gpu=refine_num_gpu,
                    user_request=original_prompt,   # B31: il soggetto dell'utente non si perde
                )
                yield self._placement_line(refine_model)   # B32
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

            use_draft = full_res and best_bytes is not None
            if use_draft:
                # SCOMP-6: il draft migliore e' gia' a piena risoluzione: e' lui l'immagine finale. Rigenerarlo
                # con lo stesso seed e piu' passi cambiava la composizione (prova reale: galeone → sfera).
                final_steps = self.valves.draft_full_res_steps
                final_seed  = best_seed
                yield f"✨ Il draft migliore e' gia' {self.valves.final_width}×{self.valves.final_height}: e' l'immagine finale (seed `{best_seed}`)\n\n"
            elif best_score >= self.valves.early_stop_score and best_seed is not None:
                final_steps = self.valves.final_high_quality_steps
                final_seed  = best_seed
                yield f"✨ **Modalità massimo dettaglio** — seed riutilizzato `{final_seed}`, step={final_steps}\n"
            else:
                final_steps = self.valves.final_steps
                final_seed  = None
                yield f"ℹ️ Modalità standard — nuovo seed casuale, step={final_steps}\n"

            if not use_draft:
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
            if not use_draft and self.vram_free_mb(comfy_role) < min_free:
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

            if use_draft:
                final_image, used_final_seed, final_file_info = best_bytes, best_seed, best_info
            else:
                yield self.gpu_status_line("Render finale")   # B32
                final_image, used_final_seed, final_file_info = self.generate_image(
                    negative=negative if decomp else None,
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
