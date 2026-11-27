"""Test IMG-INLINE (23/11/2026): immagine finale incorporata nel messaggio, nessun link a /comfyui/view."""
import base64, io, os, re, sys
from pathlib import Path
from unittest import mock
HERE = Path(__file__).resolve().parent; REPO = HERE.parent
sys.path.insert(0, str(HERE / "helpers")); sys.path.insert(0, str(REPO / "ollama" / "pipelines"))
from tiny import check, summary
os.environ.pop("OLLAMA_AUX_URL", None); os.environ.pop("ORCHESTRA_COMFY_ROLE", None)
import image_loop as IL
from PIL import Image

def png_vero(w=1024, h=1024):   # immagine "fotografica": sfumature + rumore, come un render SDXL
    import random; random.seed(1)
    im = Image.new("RGB", (w, h)); px = im.load()
    for y in range(h):
        for x in range(w):
            n = random.randint(-12, 12)
            px[x, y] = (max(0, min(255, x // 4 + n)), max(0, min(255, y // 4 + n)), max(0, min(255, (x + y) // 8 + n)))
    b = io.BytesIO(); im.save(b, format="PNG"); return b.getvalue()
PNG = png_vero()
def png_piatto():               # immagine piatta: il PNG e' gia' piccolissimo
    b = io.BytesIO(); Image.new("RGB", (1024, 1024), (10, 20, 30)).save(b, format="PNG"); return b.getvalue()

print("== conversione")
mime, d = IL._immagine_incorporata(PNG, "jpeg", 90)
check("jpeg: mime image/jpeg", mime == "image/jpeg")
check("jpeg: byte JPEG validi", d[:3] == b"\xff\xd8\xff")
check("jpeg: piu' piccolo del PNG", len(d) < len(PNG), f"{len(d)} vs {len(PNG)}")
print(f"   PNG {len(PNG)//1024} KB -> JPEG q90 {len(d)//1024} KB")
with Image.open(io.BytesIO(d)) as im: check("jpeg: dimensioni conservate", im.size == (1024, 1024))
mime, d = IL._immagine_incorporata(PNG, "webp", 90)
check("webp: mime image/webp", mime == "image/webp" and d[8:12] == b"WEBP")
mime, d = IL._immagine_incorporata(PNG, "png", 90)
check("png: nessuna conversione", mime == "image/png" and d == PNG)
mime, d = IL._immagine_incorporata(b"non-e-una-immagine", "jpeg", 90)
check("byte non validi: ricade su PNG originale", mime == "image/png" and d == b"non-e-una-immagine")
mime, d = IL._immagine_incorporata(png_piatto(), "jpeg", 90)
check("JPEG piu' grande del PNG: resta PNG", mime == "image/png")
mime, d = IL._immagine_incorporata(PNG, "jpeg", 500)
check("qualita' fuori scala: limitata, nessun errore", mime == "image/jpeg")

print("== flusso completo pipe()")
class Resp:
    status_code = 200
    def raise_for_status(s): pass
    def json(s): return {}
def flusso(img, info, fmt="jpeg"):
    p = IL.Pipeline(); p.valves.ollama_url = "http://main:11434"; p.valves.final_inline_format = fmt
    p._gpu_total_mb = lambda role: {"main": 24576, "aux": 0}.get(role, 0)
    p.vram_free_mb = lambda role="main": 23848
    p.generate_image = mock.Mock(return_value=(img, 7, info))
    p.analyze_image_vision = mock.Mock(return_value={"score": 9, "found": "a", "missing": "", "issues": "", "next_prompt": "x"})
    p.refine_prompt = mock.Mock(return_value="a refined prompt")
    p.free_comfyui_vram = mock.Mock(); p._warm_up = mock.Mock()
    with mock.patch.object(IL.time, "sleep"), mock.patch.object(IL.requests, "post", mock.Mock(return_value=Resp())), \
         mock.patch.object(IL.requests, "get", mock.Mock(return_value=Resp())):
        pezzi = list(p.pipe("a red rose", "m", [], {}))
    PEZZI.append(pezzi)
    return "".join(pezzi)
PEZZI = []
INFO = {"filename": "orchestra_final_00001_.png", "subfolder": "", "type": "output"}
out = flusso(PNG, INFO)
check("nessun link pubblico a comfyui/view", "comfyui/view" not in out and "orchestra.tregambe.com" not in out)
m = re.search(r"!\[Immagine generata\]\(data:(image/[a-z]+);base64,([A-Za-z0-9+/=]+)\)", out)
check("immagine incorporata come data URI JPEG", bool(m) and m.group(1) == "image/jpeg", out[-300:])
if m:
    with Image.open(io.BytesIO(base64.b64decode(m.group(2)))) as im:
        check("data URI decodificabile, 1024x1024", im.size == (1024, 1024))
mx = max(len(x.encode()) for x in PEZZI[-1])
check("ogni pezzo dello stream sotto i 64 KB (limite Open WebUI 131072 byte per riga)", mx <= 65536, str(mx))
check("immagine divisa in piu' pezzi", sum(1 for x in PEZZI[-1] if x and set(x) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=")) >= 2)
check("nome del file originale nel messaggio", "output/orchestra_final_00001_.png" in out)
check("messaggio di completamento", "Generazione completata!" in out and "in chat" in out)
out = flusso(PNG, {"filename": "a.png", "subfolder": "sotto", "type": "output"})
check("sottocartella nel percorso", "output/sotto/a.png" in out)
out = flusso(PNG, None)
check("fallback draft: incorporato, senza nome file", "draft fallback" in out and "data:image/jpeg;base64," in out and "File originale" not in out)
out = flusso(PNG, INFO, fmt="png")
check("valvola png: data URI PNG", "data:image/png;base64," in out)
print("== versione")
src = open(IL.__file__, encoding="utf-8").read()
check("versione coerente: intestazione, nome e messaggio di avvio v2.8.2",
      "Image Generator Loop v2.8.2" in src and IL.Pipeline().name == "Image Loop v2.8.2" and "**Image Loop v2.8.2**" in src and "v2.8.1 —" not in src.split("CHANGELOG v2.8.1")[0])
summary("test_image_inline")
