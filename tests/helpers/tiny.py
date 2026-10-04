"""Mini framework di test senza dipendenze: check(label, cond) + summary()."""
import sys
_n = _fail = 0
def check(label, cond, extra=""):
    global _n, _fail
    _n += 1
    if not cond: _fail += 1
    print(("PASS " if cond else "FAIL ") + label + (f"   [{extra}]" if (extra and not cond) else ""))
def summary(name):
    print(f"\n{_n} controlli"); print(f"{name} " + ("ALL OK" if _fail == 0 else "FAILED")); sys.exit(1 if _fail else 0)
