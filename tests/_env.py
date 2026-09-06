"""Wou d'Fotoen a wou d'Datebank leien -- fir d'Tester.

⚠ HEI GËTT NET GEROD. E Standardwee, deen zoufälleg op eng aner Installatioun
  passt, ass geféierlech: d'Prüfung, déi verhënnert datt en Test echt Fotoen
  ureiert, kuckt dann an de falsche Bam a léisst alles duerch. Also: aus der
  Ëmwelt, soss aus der Datei déi de Service liest, a soss GUER NET LAFEN.

  Genee dat ass den 06.09.2026 geschitt: de Fallback gouf vun `/mnt/my-photos`
  op `/srv/originals` gesat, an d'Tester hunn duerno op engem Bam geschafft,
  deen et net gëtt -- an de Wiechter huet näischt gemierkt.
"""
import os
import sys

_ENV_FILES = ("/etc/family/env", "/opt/roamlight/roamlight.env")


def _from_file(var):
    for path in _ENV_FILES:
        try:
            for line in open(path):
                line = line.strip()
                if line.startswith(var + "="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
        except OSError:
            continue
    return None


def need(var):
    """De Wäert -- oder den Test leeft guer net."""
    v = os.environ.get(var) or _from_file(var)
    if not v:
        sys.exit(f"ABORTED: {var} is not set, and none of {_ENV_FILES} names it.\n"
                 f"  Run the test with the service's environment, for example:\n"
                 f"    set -a; . /etc/family/env; set +a; python3 <test>")
    return v
