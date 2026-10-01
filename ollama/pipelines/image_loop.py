"""
Image Generator Loop v2.7.0 — Orchestra 8GB
Pipeline di generazione immagini SDXL con LCM-LoRA e loop di raffinamento.

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
import re
import subprocess
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


class Pipeline:

    class Valves(BaseModel):
        model_config = {"protected_namespaces": ()}

        comfyui_url:              str  = "http://172.19.0.1:8188"
        ollama_url:               str  = "http://ai-ollama-session:11434"
        model_vision:             str  = "llava:7b"
        model_vision_fallback:    str  = "moondream:v2"
        model_refine:             str  = "qwen3.5:9b"
        model_refine_fallback:    str  = "qwen2.5-coder:14b-instruct-q4_K_M"
        refine_fallback_num_gpu:  int  = 12

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

        preflight_enabled:        bool = False
        comfyui_timeout_s:        int  = 120
        vision_timeout_s:         int  = 120
        refine_timeout_s:         int  = 120

    def __init__(self):
        self.type      = "pipe"
        self.name      = "Image Loop v2.7.0"
        self.id        = "image_loop"
        self.valves    = self.Valves()
        # self.pipelines è richiesto dal framework Pipelines per i pipe autonomi.
        # "*" significa che questo pipe è disponibile per tutte le pipeline.
        self.pipelines = ["*"]

    # =========================================================================
    # UTILITÀ SISTEMA
    # =========================================================================

    def vram_free_mb(self) -> int:
        """
        Restituisce la VRAM libera in MB.
        EVO-01: legge dal VRAM daemon di embedding_utils (0ms di latenza).
        Fallback: subprocess nvidia-smi diretto se il daemon non è disponibile.
        Fallback finale: valore conservativo 2000 MB.
        """
        if _VRAM_DAEMON_AVAILABLE:
            free = _daemon_vram_free_mb()
            print(f"[IMAGE_LOOP] VRAM libera (daemon): {free} MB", flush=True)
            return free
        # fallback subprocess — solo se embedding_utils non è importabile
        try:
            out = subprocess.check_output(
                "nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits",
                shell=True, stderr=subprocess.DEVNULL, timeout=3,
            )
            free = int(out.decode().strip())
            print(f"[IMAGE_LOOP] VRAM libera (subprocess): {free} MB", flush=True)
            return free
        except Exception as e:
            print(
                f"[IMAGE_LOOP] nvidia-smi fallito ({e}), uso fallback conservativo 2000MB",
                flush=True
            )
            return 2000

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
        else:
            print(
                f"[IMAGE_LOOP] VRAM={vram_mb}MB → "
                f"{self.valves.model_refine_fallback} num_gpu={self.valves.refine_fallback_num_gpu}",
                flush=True
            )
            return self.valves.model_refine_fallback, self.valves.refine_fallback_num_gpu

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
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    def _parse_vision_analysis(self, raw: str, fallback_prompt: str) -> dict:
        """
        Analisi robusta della risposta vision.
        1. Tenta estrazione JSON strutturato.
        2. Se fallisce, estrae score e next_prompt con regex.
        3. Se ancora fallisce, restituisce fallback (score=5, next_prompt=originale).
        """
        data = self._extract_json_from_response(raw)
        if data:
            return {
                "score":       max(1, min(10, int(data.get("score", 5)))),
                "found":       str(data.get("found", "")),
                "missing":     str(data.get("missing", "")),
                "issues":      str(data.get("issues", "")),
                "next_prompt": str(data.get("next_prompt", fallback_prompt)),
            }
        # Fallback regex per estrarre almeno score e next_prompt
        score_match = re.search(r'"score"\s*:\s*(\d+)', raw)
        next_match  = re.search(r'"next_prompt"\s*:\s*"([^"]+)"', raw)
        return {
            "score":       int(score_match.group(1)) if score_match else 5,
            "found":       "",
            "missing":     "",
            "issues":      "Risposta non in formato JSON",
            "next_prompt": next_match.group(1) if next_match else fallback_prompt,
        }

    def analyze_image_vision(
        self, image_bytes: bytes, prompt: str,
        model: str = "llava:7b", num_gpu: Optional[int] = None
    ) -> dict:
        """Analizza l'immagine con un modello vision (richiede JSON strutturato)."""
        b64 = base64.b64encode(image_bytes).decode()
        options: dict = {
            "num_ctx":    4096,
            "keep_alive": 0,
            "temperature": 0.2,   # output più deterministico
        }
        if num_gpu is not None:
            options["num_gpu"] = num_gpu

        analysis_prompt = (
            f"Analyze this SDXL-generated image for the prompt: '{prompt}'.\n"
            "You MUST respond with a single JSON object (no markdown, no extra text) "
            "exactly like this example:\n"
            '{"score":7,"found":"red rose, green leaves","missing":"dew drops, '
            'darker background","issues":"overexposed petals, soft focus",'
            '"next_prompt":"a close-up of a red rose with dew drops, sharp focus, '
            'dark background, 8k, highly detailed"}\n\n'
            "Now output YOUR analysis for the given image and prompt. "
            "The 'next_prompt' must be a concrete, improved SDXL prompt "
            "(not a question or request) of maximum 100 words."
        )

        try:
            resp = requests.post(
                f"{self.valves.ollama_url}/api/generate",
                json={
                    "model":   model,
                    "prompt":  analysis_prompt,
                    "images":  [b64],
                    "stream":  False,
                    "options": options,
                },
                timeout=self.valves.vision_timeout_s,
            )
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

    # =========================================================================
    # REFINEMENT
    # =========================================================================

    def refine_prompt(
        self, original_prompt: str, analysis: dict,
        model: str = "qwen3.5:9b", num_gpu: Optional[int] = None
    ) -> str:
        """Raffina il prompt usando i risultati dell'analisi vision."""
        options: dict = {"num_ctx": 4096, "num_predict": 200, "keep_alive": 0}
        if num_gpu is not None:
            options["num_gpu"] = num_gpu

        refine_prompt_text = (
            "You are an expert SDXL prompt engineer. Improve this prompt based on the analysis.\n\n"
            f"Current prompt: {original_prompt}\n"
            f"Score: {analysis['score']}/10\n"
            f"Found: {analysis['found']}\n"
            f"Missing: {analysis['missing']}\n"
            f"Issues: {analysis['issues']}\n\n"
            "Output ONLY the improved prompt (no quotes). Keep under 100 words.\n"
            "Include quality tags: 8k, highly detailed.\n"
            "Improved SDXL prompt:"
        )

        try:
            resp = requests.post(
                f"{self.valves.ollama_url}/api/generate",
                json={
                    "model":   model,
                    "prompt":  refine_prompt_text,
                    "stream":  False,
                    "options": options,
                },
                timeout=self.valves.refine_timeout_s,
            )
            resp.raise_for_status()
            refined = resp.json().get("response", "").strip()
            refined = refined.strip('"\'`')
            if refined.lower().startswith("improved sdxl prompt:"):
                refined = refined[len("improved sdxl prompt:"):].strip()
            if refined and len(refined) > 15:
                return refined
            return analysis.get("next_prompt", original_prompt)
        except Exception as e:
            print(f"[IMAGE_LOOP] Errore refinement ({model}): {e}", flush=True)
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
            resp = requests.post(
                f"{self.valves.ollama_url}/api/generate",
                json={
                    "model":   "llama3.2:3b",
                    "prompt":  opt_prompt,
                    "stream":  False,
                    "options": {"num_ctx": 2048, "num_predict": 150, "keep_alive": 600},
                },
                timeout=30,
            )
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

        def generate() -> Iterator[str]:
            original_prompt = user_message.strip()
            yield "🎨 **Image Loop v2.6.0** — avvio generazione SDXL\n\n"
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
                self.free_comfyui_vram()

                vram_after_comfy = self.vram_free_mb()
                vision_model, vision_num_gpu = self._select_vision_params(vram_after_comfy)
                yield f"📊 VRAM libera: **{vram_after_comfy} MB** → `{vision_model}`"
                if vision_num_gpu:
                    yield f" num_gpu={vision_num_gpu}"
                yield "\n\n🔍 Analisi vision...\n"

                analysis = self.analyze_image_vision(
                    image_bytes=draft_bytes,
                    prompt=current_prompt,
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
                    yield f"📉 Score non migliorato (max {best_score}). Ripristino miglior prompt.\n\n"
                    current_prompt = best_prompt
                    continue   # salta raffinamento, prossima iterazione usa best_prompt

                if score >= self.valves.early_stop_score:
                    yield f"🎯 Score {score}/10 ≥ {self.valves.early_stop_score} — early stop!\n\n"
                    break

                # Raffinamento solo se lo score è migliorato
                vram_after_vision = self.vram_free_mb()
                refine_model, refine_num_gpu = self._select_refine_params(vram_after_vision)
                yield f"📊 VRAM dopo vision: **{vram_after_vision} MB** → `{refine_model}`"
                if refine_num_gpu:
                    yield f" num_gpu={refine_num_gpu}"
                yield "\n✏️ Raffinamento prompt...\n"

                current_prompt = self.refine_prompt(
                    original_prompt=current_prompt,
                    analysis=analysis,
                    model=refine_model,
                    num_gpu=refine_num_gpu,
                )
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

            self.free_comfyui_vram()
            time.sleep(2)
            free_before_final = self.vram_free_mb()
            if free_before_final < 6000:
                yield f"⚠️ VRAM bassa ({free_before_final} MB), attendo liberazione...\n"
                for _ in range(15):
                    time.sleep(2)
                    if self.vram_free_mb() >= 6000:
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
                if final_file_info:
                    image_url = (
                        "https://orchestra.tregambe.com/comfyui/view"
                        f"?filename={final_file_info['filename']}"
                        f"&subfolder={final_file_info['subfolder']}"
                        f"&type={final_file_info['type']}"
                    )
                    yield f"✅ Generazione completata! ({len(final_image) // 1024} KB)\n\n"
                    yield f"![Immagine generata]({image_url})\n\n"
                else:
                    b64_final = base64.b64encode(final_image).decode()
                    yield f"✅ Generazione completata (draft fallback, {len(final_image) // 1024} KB)\n\n"
                    yield f"![Immagine generata](data:image/png;base64,{b64_final})\n\n"
                yield f"**Prompt usato:** `{best_prompt}`\n"
                yield f"**Score miglior draft:** {best_score}/10\n"
                if best_score >= self.valves.early_stop_score and best_seed is not None:
                    yield f"**Seed finale (riutilizzato):** `{used_final_seed}`\n"
                else:
                    yield f"**Seed finale:** `{used_final_seed}`\n"
            else:
                yield "❌ Nessuna immagine disponibile.\n"

        return generate()
