target_file = "/home/claudio/ai-sessioni/rag/rag_service.py"

with open(target_file) as f:
    content = f.read()

# Rimuove l'excepthook inserito nel posto sbagliato
old = """app = Flask(__name__)

# Handler globale eccezioni nei thread — previene crash del processo principale
import sys as _sys
def _thread_excepthook(args):
    print(f"[RAG] Eccezione non gestita nel thread {args.thread.name}: {args.exc_value}", flush=True)
    import traceback
    traceback.print_exception(args.exc_type, args.exc_value, args.exc_tb)
threading.excepthook = _thread_excepthook"""

new = "app = Flask(__name__)"
content = content.replace(old, new, 1)

# Inserisce l'excepthook DOPO il blocco degli import threading (dopo _INDEX_JOBS_LOCK)
old = "_INDEX_JOBS_LOCK = threading.Lock()\n_INDEXING_IN_PROGRESS = threading.Event()"
new = """_INDEX_JOBS_LOCK = threading.Lock()
_INDEXING_IN_PROGRESS = threading.Event()

# Handler globale eccezioni nei thread — previene crash del processo principale
def _thread_excepthook(args):
    print(f"[RAG] Eccezione thread {args.thread.name}: {args.exc_value}", flush=True)
    import traceback
    traceback.print_exception(args.exc_type, args.exc_value, args.exc_tb)
threading.excepthook = _thread_excepthook"""

content = content.replace(old, new, 1)

with open(target_file, "w") as f:
    f.write(content)

print("Excepthook spostato nel posto corretto.")
