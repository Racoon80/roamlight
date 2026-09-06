#!/bin/sh
# Start the site.
#
# ⚠ The container runs as root only long enough to own the volumes, then drops
#   to PUID/PGID. Photographs sit on a share that other machines write to as
#   well — if the files end up owned by root, the next program that touches
#   them is locked out, and that is a mess to undo.
set -e

PUID="${PUID:-1000}"
PGID="${PGID:-1000}"

# Can PUID write into $1? Tested by actually writing, not by reading the mode:
# a network share can answer anything it likes about permissions.
writable() {
    su_out=$(gosu "$PUID:$PGID" sh -c "t=\"$1/.roamlight-write-test.\$\$\"; \
        : > \"\$t\" 2>/dev/null && rm -f \"\$t\" && echo yes" 2>/dev/null || true)
    [ "$su_out" = "yes" ]
}

if [ "$(id -u)" = "0" ]; then
    if ! getent group "$PGID" >/dev/null 2>&1; then
        groupadd -g "$PGID" family 2>/dev/null || addgroup -g "$PGID" family 2>/dev/null || true
    fi
    if ! getent passwd "$PUID" >/dev/null 2>&1; then
        useradd -u "$PUID" -g "$PGID" -M -s /usr/sbin/nologin family 2>/dev/null || true
    fi

    for d in "$FAMILY_DATA" "$FAMILY_INCOMING" "$FAMILY_DERIVATIVES"; do
        [ -n "$d" ] && mkdir -p "$d" && chown "$PUID:$PGID" "$d" || true
    done

    # ⚠ /originals and /library are NOT chowned recursively: they may be a
    #   network share with thousands of files, and rewriting the owner of a
    #   whole library on every start is both slow and rude.
    #
    #   But they do have to be WRITABLE by PUID, and on a first run they are
    #   usually not: a fresh bind mount belongs to root, the site runs as 1000,
    #   and then every upload is accepted and then quietly never converted.
    #   That is the kind of failure nobody notices for a week.
    #
    #   So: if the folder is EMPTY, take ownership of that one directory (cheap,
    #   and it cannot damage anything that is not there). If it holds files,
    #   leave it alone and refuse to start, naming the folder and the uid.
    for d in "${FAMILY_ORIGINS:-/originals}" "${FAMILY_WEB:-/library}"; do
        [ -d "$d" ] || mkdir -p "$d"
        if ! writable "$d"; then
            if [ -z "$(ls -A "$d" 2>/dev/null)" ]; then
                chown "$PUID:$PGID" "$d" || true
            fi
        fi
        if ! writable "$d"; then
            echo "roamlight: $d is not writable by uid $PUID." >&2
            echo "  It holds files already, so the owner is not changed here." >&2
            echo "  Fix it on the host, for example:" >&2
            echo "      chown -R $PUID:$PGID <the folder mounted at $d>" >&2
            echo "  or set PUID/PGID to the user that owns it." >&2
            exit 1
        fi
    done

    exec gosu "$PUID:$PGID" "$@"
fi

exec "$@"
