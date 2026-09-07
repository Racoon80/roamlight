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
# ⚠ Two shapes, and both are normal: a service reads an environment FILE, a
#   container gets the same values in its process environment. Requiring the
#   file meant the suite could not run in the container at all.
ENV_FILE="${FAMILY_ENV:-}"
if [ -z "$ENV_FILE" ]; then
    for c in /etc/family/env /opt/roamlight/roamlight.env; do
        [ -r "$c" ] && ENV_FILE="$c" && break
    done
fi
if [ -n "$ENV_FILE" ] && [ -r "$ENV_FILE" ]; then
    set -a; . "$ENV_FILE"; set +a
elif [ -z "${FAMILY_DB:-}${FAMILY_DATA:-}" ]; then
    echo "no environment: neither a file nor FAMILY_DB/FAMILY_DATA in the environment"
    exit 1
fi

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
# ⚠ Als DEE SELWECHTE Benotzer lafe loossen, deem d'Fotoen gehéieren.
#
#   Leeft d'Suite als root, da leet si Uerdner als root un -- an duerno kann de
#   Service (deen als `roamlight`/`family` leeft) net méi eran. Wat een dann
#   gesäit, ass `Permission denied` op enger Plaz déi guer näischt mam Test ze
#   dinn huet, an et gesäit aus wéi e Feeler am Site. Et ass keen.
if [ -n "${FAMILY_ORIGINS:-}" ] && [ -d "$FAMILY_ORIGINS" ]; then
    tree_owner="$(stat -c %U "$FAMILY_ORIGINS" 2>/dev/null || stat -f %Su "$FAMILY_ORIGINS" 2>/dev/null)"
    me="$(id -un)"
    if [ -n "$tree_owner" ] && [ "$tree_owner" != "$me" ]; then
        echo "REFUSED: the photographs belong to '$tree_owner', and this is running as '$me'."
        echo
        echo "  Anything this run creates would belong to '$me', and the site could"
        echo "  not write into it afterwards. Start it as the service user:"
        echo
        echo "      su -s /bin/bash -c 'bash tests/run.sh' $tree_owner"
        echo
        exit 1
    fi
fi

ALL="pages proxy ticket convert gallery upload album album_undo owner tag share sync acl av"
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
