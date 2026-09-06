#!/usr/bin/env bash
# Roamlight — create a Proxmox LXC container and install the site in it.
#
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/Racoon80/roamlight/main/deploy/proxmox-lxc.sh)"
#
# Run it ON a Proxmox host, as root. It asks a handful of questions (including
# whether you want the virus scanner), builds the container, and prints the
# address at the end.
#
# ⚠ You are about to pipe a script from the internet into a root shell. That is
#   the convenient way and it is also how a bad day starts. Read it first:
#   curl -fsSL <url> | less — it is 200 lines and it does nothing clever.
set -euo pipefail

REPO="${ROAMLIGHT_REPO:-https://github.com/Racoon80/roamlight}"
BRANCH="${ROAMLIGHT_BRANCH:-main}"
# A local tarball instead of GitHub (used to test the script before release).
SRC_TGZ="${ROAMLIGHT_SRC:-}"

TEMPLATE_STORE="${TEMPLATE_STORE:-local}"
OSTEMPLATE_NAME="debian-12-standard"

say()  { printf '\033[1;36m%s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m%s\033[0m\n' "$*"; }
die()  { printf '\033[1;31m%s\033[0m\n' "$*" >&2; exit 1; }

# --- is this actually a Proxmox host? ---------------------------------------
command -v pveversion >/dev/null 2>&1 || die "This has to run on a Proxmox host (pveversion not found)."
[ "$(id -u)" = "0" ] || die "Run it as root."

# ask <VARIABLE> <question> <default>
#
# ⚠ A value already in the environment wins over the question. That is what
#   makes an unattended install possible:
#     CTID=250 STORAGE=tank CLAMAV=yes bash proxmox-lxc.sh
#   and it is also how this script is tested — a question that can only be
#   answered by hand cannot be tested at all.
ask() {
    local var="$1" q="$2" d="$3" a=""
    a="$(eval "printf '%s' \"\${$var:-}\"")"
    if [ -n "$a" ]; then printf '%s' "$a"; return 0; fi
    read -r -p "$q [$d]: " a </dev/tty || true
    printf '%s' "${a:-$d}"
}

yesno() {               # yesno <VARIABLE> <question> <default y|n>
    local var="$1" q="$2" d="$3" a=""
    a="$(eval "printf '%s' \"\${$var:-}\"")"
    if [ -z "$a" ]; then
        read -r -p "$q [$( [ "$d" = y ] && echo 'Y/n' || echo 'y/N' )]: " a </dev/tty || true
        a="${a:-$d}"
    fi
    case "$a" in [yY]*) return 0 ;; *) return 1 ;; esac
}

say "Roamlight — a family photo library"
echo

CTID="$(ask CTID 'Container ID' "$(pvesh get /cluster/nextid 2>/dev/null || echo 200)")"
HOSTNAME="$(ask HOSTNAME 'Hostname' 'roamlight')"
DISK="$(ask DISK 'Disk in GB (photographs need room)' '32')"
CORES="$(ask CORES 'CPU cores' '2')"
RAM="$(ask RAM 'Memory in MB' '2048')"
BRIDGE="$(ask BRIDGE 'Network bridge' 'vmbr0')"
STORAGE="$(ask STORAGE 'Storage for the container' 'local-lvm')"
IPCONF="$(ask IPCONF 'Address (dhcp, or 10.0.0.5/24)' 'dhcp')"
GW=""
if [ "$IPCONF" != "dhcp" ]; then GW="$(ask GW 'Gateway' '')"; fi

# ⚠ Anything passed on as a number has to be one. `pct` gets these values
#   directly, and an answer with a space in it would land there as an extra
#   argument.
for pair in "CTID:$CTID" "DISK:$DISK" "CORES:$CORES" "RAM:$RAM"; do
    name="${pair%%:*}"; value="${pair#*:}"
    case "$value" in
        ''|*[!0-9]*) die "$name has to be a number, not \"$value\"." ;;
    esac
done

# ⚠ The one question that costs memory: ClamAV keeps its signatures in RAM,
#   about 1 GB of it. On a 2 GB container that is the difference between a
#   site that works and one that swaps.
WANT_CLAMAV=no
if yesno CLAMAV 'Install the virus scanner (ClamAV)? It needs ~1 GB extra memory' n; then
    WANT_CLAMAV=yes
    if [ "$RAM" -lt 3072 ]; then
        warn "Memory raised to 3072 MB — ClamAV does not fit in ${RAM} MB."
        RAM=3072
    fi
fi

# --- the template ------------------------------------------------------------
say "Looking for a Debian 12 template…"
TEMPLATE="$(pveam list "$TEMPLATE_STORE" 2>/dev/null | awk -v n="$OSTEMPLATE_NAME" '$1 ~ n {print $1}' | tail -1 || true)"
if [ -z "$TEMPLATE" ]; then
    pveam update >/dev/null
    NEWEST="$(pveam available --section system | awk -v n="$OSTEMPLATE_NAME" '$2 ~ n {print $2}' | tail -1)"
    [ -n "$NEWEST" ] || die "No $OSTEMPLATE_NAME template available."
    say "Downloading $NEWEST…"
    pveam download "$TEMPLATE_STORE" "$NEWEST" >/dev/null
    TEMPLATE="$TEMPLATE_STORE:vztmpl/$NEWEST"
fi

# --- the container -----------------------------------------------------------
say "Creating container $CTID…"
NET="name=eth0,bridge=$BRIDGE"
if [ "$IPCONF" = "dhcp" ]; then
    NET="$NET,ip=dhcp"
else
    NET="$NET,ip=$IPCONF"
    [ -n "$GW" ] && NET="$NET,gw=$GW"
fi

pct create "$CTID" "$TEMPLATE" \
    --hostname "$HOSTNAME" \
    --cores "$CORES" --memory "$RAM" --swap 512 \
    --rootfs "$STORAGE:$DISK" \
    --net0 "$NET" \
    --unprivileged 1 --features nesting=1 --onboot 1 \
    --description "Roamlight — $REPO"

pct start "$CTID"
say "Waiting for the network…"
for _ in $(seq 1 30); do
    pct exec "$CTID" -- getent hosts deb.debian.org >/dev/null 2>&1 && break
    sleep 2
done

# --- inside the container ----------------------------------------------------
say "Installing (this takes a few minutes)…"

pct exec "$CTID" -- bash -eu <<'INSIDE'
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
# ⚠ These three do the actual work: libvips resizes, exiftool reads and writes
#   metadata, ffmpeg makes the poster and the web copy of a video.
apt-get install -y -qq --no-install-recommends \
    python3 python3-venv python3-dev build-essential \
    libvips42 libimage-exiftool-perl ffmpeg sqlite3 \
    git ca-certificates curl >/dev/null
id -u roamlight >/dev/null 2>&1 || useradd --system --uid 1000 --create-home \
    --home-dir /opt/roamlight --shell /usr/sbin/nologin roamlight
install -d -o roamlight -g roamlight /opt/roamlight /opt/roamlight/data \
    /opt/roamlight/data/derivatives /opt/roamlight/incoming \
    /srv/originals /srv/library
INSIDE

if [ -n "$SRC_TGZ" ]; then
    say "Using the local source $SRC_TGZ"
    pct push "$CTID" "$SRC_TGZ" /tmp/src.tgz
    pct exec "$CTID" -- bash -eu -c '
        rm -rf /opt/roamlight/app-src && mkdir -p /opt/roamlight/app-src
        tar xzf /tmp/src.tgz -C /opt/roamlight/app-src'
else
    # ⚠ The address and branch as ARGUMENTS, not inside the command text: a repo
    #   with an apostrophe in it would otherwise break out of the quoting and
    #   run something else in the host's root shell.
    pct exec "$CTID" -- bash -eu -c '
        rm -rf /opt/roamlight/app-src
        git clone --depth 1 --branch "$2" "$1" /opt/roamlight/app-src >/dev/null 2>&1
    ' _ "$REPO" "$BRANCH"
fi

pct exec "$CTID" -- bash -eu <<INSIDE
cd /opt/roamlight/app-src
python3 -m venv /opt/roamlight/venv
/opt/roamlight/venv/bin/pip install --quiet --upgrade pip
/opt/roamlight/venv/bin/pip install --quiet -r requirements.txt

cat > /opt/roamlight/roamlight.env <<'ENV'
FAMILY_AUTH=local
FAMILY_REQUIRE_AUTH=1
FAMILY_SITE_TITLE=Roamlight
# ⚠ Set this to the address people actually type before you hand out a share
#   link -- the link is built from it, and one built from an address only this
#   machine knows is useless to a guest.
FAMILY_SITE_URL=http://SITE_ADDRESS:8080
FAMILY_BASE=/opt/roamlight/app-src
FAMILY_DATA=/opt/roamlight/data
FAMILY_DB=/opt/roamlight/data/roamlight.db
FAMILY_DERIVATIVES=/opt/roamlight/data/derivatives
FAMILY_INCOMING=/opt/roamlight/incoming
FAMILY_ORIGINS=/srv/originals
FAMILY_WEB=/srv/library
FAMILY_REQUIRE_MOUNT=0
FAMILY_SHARES=1
FAMILY_CLAMAV=$( [ "$WANT_CLAMAV" = yes ] && echo 1 || echo 0 )
ENV
chown roamlight:roamlight /opt/roamlight/roamlight.env
chmod 640 /opt/roamlight/roamlight.env

cat > /etc/systemd/system/roamlight.service <<'UNIT'
[Unit]
Description=Roamlight — a family photo library
After=network-online.target

[Service]
User=roamlight
Group=roamlight
EnvironmentFile=/opt/roamlight/roamlight.env
WorkingDirectory=/opt/roamlight/app-src
# ⚠ --no-proxy-headers: without it uvicorn replaces the peer address with
#   whatever X-Forwarded-For says, and the site's own "is this local" check
#   would then be reading a header a client can write.
ExecStart=/opt/roamlight/venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8080 --no-proxy-headers
Restart=on-failure
RestartSec=5
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=full
ProtectHome=yes

[Install]
WantedBy=multi-user.target
UNIT

chown -R roamlight:roamlight /opt/roamlight
systemctl daemon-reload
systemctl enable --now roamlight.service
INSIDE

if [ "$WANT_CLAMAV" = yes ]; then
    say "Installing ClamAV…"
    pct exec "$CTID" -- bash -eu <<'INSIDE'
export DEBIAN_FRONTEND=noninteractive
apt-get install -y -qq clamav-daemon clamav-freshclam >/dev/null
systemctl stop clamav-freshclam clamav-daemon 2>/dev/null || true
freshclam --quiet || true
# ⚠ 25 MB is the default limit and a photograph from a real camera is bigger
#   than that -- a RAW file or a video would come back "clean" without ever
#   having been looked at.
sed -i 's/^MaxFileSize .*/MaxFileSize 1000M/; s/^MaxScanSize .*/MaxScanSize 1000M/; s/^StreamMaxLength .*/StreamMaxLength 1000M/' /etc/clamav/clamd.conf
systemctl enable --now clamav-freshclam clamav-daemon
INSIDE
fi

IP="$(pct exec "$CTID" -- hostname -I 2>/dev/null | awk '{print $1}')"
# The address is only known once the container is up, so it is filled in here.
if [ -n "$IP" ]; then
    pct exec "$CTID" -- sed -i "s|http://SITE_ADDRESS:8080|http://$IP:8080|" \
        /opt/roamlight/roamlight.env
    pct exec "$CTID" -- systemctl restart roamlight.service
fi
# ⚠ No apostrophe inside ${...:-...}: bash parses the default value, and a
#   lone ' there opens a quote that never closes ("unexpected EOF").
[ -n "$IP" ] || IP="the address of container $CTID"
echo
say "Done."
echo "  Container : $CTID ($HOSTNAME)"
echo "  Address   : http://$IP:8080"
echo "  Scanner   : $WANT_CLAMAV"
echo
echo "Open the address in a browser. The first screen asks you to create the"
echo "administrator account — nobody can sign in until you do."
echo
warn "Put a reverse proxy with HTTPS in front of it before it leaves your LAN."
