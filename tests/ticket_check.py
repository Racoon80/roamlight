"""D'Ticket-Logik selwer.

Leeft ouni Instanz: et ass d'Regel déi zielt, an eng Regel déi ee
Video opmécht muss een ouni Opbau kënne noliesen an nofueren. Der
Datebank hir zwou Funktiounen ginn ersat, soss bräicht de Test eng.
"""
import sys, time
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
import types
# db.get_state/set_state fake, sou datt kee Datebank néideg ass
fake = types.ModuleType("app.db")
_store = {}
fake.get_state = lambda k, d=None: _store.get(k, d)
fake.set_state = lambda k, v: _store.__setitem__(k, v)
sys.modules["app.db"] = fake
from app import tickets

ok = bad = 0
def check(name, cond, note=""):
    global ok, bad
    if cond: ok += 1; print(f"  ok    {name}" + (f" — {note}" if note else ""))
    else: bad += 1; print(f"  FAIL  {name}" + (f" — {note}" if note else ""))

P = "/photos/12/video.mp4"
t = tickets.mint(P, "guy")
check("een Ticket gëtt ausgestallt", bool(t), t[:24] + "…")
check("en huet op senger Adress Gëltegkeet", tickets.user_for(P, t) == "guy")
check("op enger ANERER Foto NET", tickets.user_for("/photos/13/video.mp4", t) is None)
check("op enger anerer Route NET", tickets.user_for("/api/albums", t) is None)
check("mat engem Buschtaf geännert NET", tickets.user_for(P, t[:-1] + ("0" if t[-1] != "0" else "1")) is None)
check("Bloedsinn NET", tickets.user_for(P, "aaa.bbb.ccc") is None)
check("eidel NET", tickets.user_for(P, "") is None)
check("ouni Punkten NET", tickets.user_for(P, "keenTicket") is None)
old = tickets.mint(P, "guy", minutes=-1)
check("ofgelaf NET", tickets.user_for(P, old) is None)
check("en anere Benotzer kritt en anere Ticket", tickets.mint(P, "eve") != tickets.mint(P, "guy"))
check("an de Numm kënnt richteg zréck", tickets.user_for(P, tickets.mint(P, "Eve")) == "Eve")
_store["ticket_key"] = "een-ganz-aneren-Schlëssel"
check("mat engem anere Schlëssel gëllt en net méi", tickets.user_for(P, t) is None)
print(f"\n{'ALLES GRÉNG' if not bad else str(bad)+' FEELER'} — {ok} ok")
sys.exit(1 if bad else 0)
