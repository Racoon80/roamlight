#!/usr/bin/env bash
# Access test — runs FROM OUTSIDE, against the address people actually type.
#
# This belongs after every deploy, not once by hand. A manual test is a
# snapshot; this is a brake.
#
#   HOST=photos.example.com bash tests/external_check.sh
#   HOST=photos.example.com PROXY=10.0.0.2 bash tests/external_check.sh   # past the CDN
#
# ⚠ The three checks that matter most are 2, 3 and 5. The first says a forged
#   identity header gets nobody in; the second says the health endpoint gives
#   nothing away; the third says a share link is NOT redirected to the sign-in
#   page -- that last one is the failure nobody notices, because an admin is
#   always signed in already.
set -u
HOST="${HOST:?set HOST, e.g. HOST=photos.example.com}"
PROXY="${PROXY:-}"
CURL=(curl -sk -m 20)
[ -n "$PROXY" ] && CURL+=(--resolve "$HOST:443:$PROXY")

pass=0; fail=0
chk() {  # chk "description" "expected" "got"
    if [ "$2" = "$3" ]; then printf '  ok    %-52s %s\n' "$1" "$3"; pass=$((pass+1))
    else printf '  FAIL  %-52s expected %s, got %s\n' "$1" "$2" "$3"; fail=$((fail+1)); fi
}
code() { "${CURL[@]}" -o /dev/null -w '%{http_code}' "$@"; }

echo "Access test — https://$HOST${PROXY:+  (straight at $PROXY)}"
echo

# --- 1. Nothing is visible without signing in --------------------------------
chk "/ without signing in"            302 "$(code "https://$HOST/")"
chk "/admin without signing in"       302 "$(code "https://$HOST/admin")"
chk "/api/photos without signing in"  302 "$(code "https://$HOST/api/photos")"
chk "a location that does not exist"  302 "$(code "https://$HOST/does-not-exist-42/")"

# --- 2. A forged identity header gets nobody in ------------------------------
chk "/ with a forged identity header" 302 \
    "$(code -H 'X-authentik-username: hacker' -H 'X-authentik-groups: admin' "https://$HOST/")"

# --- 3. Health gives nothing away from outside -------------------------------
body=$("${CURL[@]}" "https://$HOST/api/health")
chk "/api/health is reachable"        200 "$(code "https://$HOST/api/health")"
case "$body" in
    *'"origins"'*|*'"problems"'*|*'"counts"'*)
        printf '  FAIL  %-52s %s\n' "/api/health leaks the state" "$body"; fail=$((fail+1));;
    *) printf '  ok    %-52s %s\n' "/api/health says only ok" "$body"; pass=$((pass+1));;
esac

# --- 4. Search engines and caches --------------------------------------------
hdr=$("${CURL[@]}" -D - -o /dev/null "https://$HOST/api/health")
case "$hdr" in *[Xx]-[Rr]obots-[Tt]ag*noindex*) ok=1;; *) ok=0;; esac
chk "X-Robots-Tag: noindex"           1 "$ok"
case "$hdr" in *[Cc][Ff]-[Cc]ache-[Ss]tatus*HIT*) ok=0;; *) ok=1;; esac
chk "the CDN does not cache (no HIT)" 1 "$ok"
chk "robots.txt"                      200 "$(code "https://$HOST/robots.txt")"

# --- 5. Share links ----------------------------------------------------------
# ⚠ Two things, and the second is the important one:
#   * a token that does not exist gets a 404 -- never a sign-in page, because
#     that would give away that the path exists at all;
#   * and `/s/` must NOT redirect to the identity provider. If it did, somebody
#     without an account could not open a share page at all -- and the whole
#     link would be worth nothing.
chk "an unknown token"                404 "$(code "https://$HOST/s/not-a-valid-token")"
c=$(code "https://$HOST/s/not-a-valid-token")
case "$c" in 30*) redirected=1;; *) redirected=0;; esac
chk "/s/ does NOT redirect to sign-in" 0 "$redirected"

# --- 6. The app itself is not reachable directly ------------------------------
if [ -n "${APP_IP:-}" ]; then
    for port in 8080 8000 5000; do
        c=$(curl -s -m 5 -o /dev/null -w '%{http_code}' "http://$APP_IP:$port/api/health" 2>/dev/null || echo 000)
        chk "port $port is dead from outside"  000 "$c"
    done
else
    echo "  --    APP_IP not set — the port test is skipped (APP_IP=10.0.0.5)"
fi

echo
echo "  $pass ok, $fail failed"
[ "$fail" -eq 0 ]
