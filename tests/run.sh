#!/usr/bin/env bash
# Every acceptance test, with the SERVICE's environment.
#
#   bash tests/run.sh                 # all of them
#   bash tests/run.sh tag share       # only these
#
# ⚠ Without the environment the tests do not run at all any more (see
#   tests/_env.py). That is deliberate: a test that guesses where the
#   photographs are is a test that can delete the wrong ones.
set -u
ENV_FILE="${FAMILY_ENV:-/etc/family/env}"
[ -r "$ENV_FILE" ] || { echo "no environment file at $ENV_FILE"; exit 1; }
set -a; . "$ENV_FILE"; set +a

HERE="$(cd "$(dirname "$0")" && pwd)"
# ⚠ De richtegen Interpreter ass deen, deen d'App selwer benotzt -- soss
#   otherwise pyvips or Pillow is missing and the test dies on an ImportError.
PY="${PYTHON:-}"
if [ -z "$PY" ]; then
    for c in /opt/family/venv/bin/python /opt/roamlight/venv/bin/python3 python3; do
        [ -x "$c" ] || command -v "$c" >/dev/null 2>&1 || continue
        PY="$c"; break
    done
fi
ALL="pages convert gallery upload album album_undo owner tag share sync acl av"
WANT="${*:-$ALL}"

pass=0; fail=0
for t in $WANT; do
    f="$HERE/${t}_check.py"
    [ -f "$f" ] || { echo "  --    no such test: $t"; continue; }
    printf '\n════ %s ════\n' "$t"
    if "$PY" "$f"; then pass=$((pass+1)); else fail=$((fail+1)); fi
done
printf '\n%d test file(s) green, %d red\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
