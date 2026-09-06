#!/usr/bin/env bash
# Set the machine up. Idempotent -- it may run again and again.
set -euo pipefail
SRC="${1:?usage: install.sh /path/to/the/checkout}"

id -u family >/dev/null 2>&1 || useradd --system --uid 1000 --create-home \
    --home-dir /opt/family --shell /usr/sbin/nologin family
install -d -o family -g family /opt/family/app /opt/family/data \
    /opt/family/data/derivatives /opt/family/incoming

rsync -a --delete "$SRC/app/" /opt/family/app/app/
rsync -a "$SRC/templates/" /opt/family/app/templates/ 2>/dev/null || true
rsync -a "$SRC/static/"    /opt/family/app/static/    2>/dev/null || true
install -m 0644 "$SRC/requirements.txt" /opt/family/app/requirements.txt
# The tests run as `family`, not as root -- otherwise they leave folders owned
# by root behind and the service cannot get into them afterwards.
install -d -o family -g family /opt/family/tests
rsync -a "$SRC/tests/" /opt/family/tests/ 2>/dev/null || true
chown -R family:family /opt/family/tests
chown -R family:family /opt/family/app /opt/family/data /opt/family/incoming

# --- Lock 4: the proxy secret. Generated once, unchanged after that. ---------
install -d -m 0750 -o root -g family /etc/family
if [ ! -s /etc/family/proxy-secret ]; then
    head -c 48 /dev/urandom | base64 | tr -d '\n=+/' > /etc/family/proxy-secret
    chown root:family /etc/family/proxy-secret
    chmod 0640 /etc/family/proxy-secret
fi
printf 'proxy_set_header X-Family-Proxy "%s";\n' "$(cat /etc/family/proxy-secret)" \
    > /etc/nginx/family-proxy-secret.conf
chmod 0640 /etc/nginx/family-proxy-secret.conf

# ⚠ The same secret once more, but as a MAP: the guard for the app
# (`location = /family-authz`) goes either to the identity provider's outpost or
# to our own app. The secret may only travel in the second case -- the outpost
# should not get a key it does not need. This file is written here and not kept
# in the repository, because the value is in it.
cat > /etc/nginx/conf.d/family-authz-secret.conf <<EOF
map \$http_authorization \$family_authz_secret {
    default              "";
    "~*^Bearer\\s+fam_"   "$(cat /etc/family/proxy-secret)";
}
EOF
chmod 0640 /etc/nginx/conf.d/family-authz-secret.conf

# --- venv ---------------------------------------------------------------------
if [ ! -x /opt/family/venv/bin/python ]; then
    python3 -m venv /opt/family/venv
fi
/opt/family/venv/bin/pip install --quiet --upgrade pip
/opt/family/venv/bin/pip install --quiet -r /opt/family/app/requirements.txt
chown -R family:family /opt/family/venv

# --- systemd + nginx + nftables ----------------------------------------------
# ⚠ The environment file MUST be there before the service -- it reads it at
# startup, and without it it finds neither the database nor the mounts. It
# belongs to the group `family` and is used by the sync runs as well.
install -m 0640 -o root -g family "$SRC/deploy/family.env" /etc/family/env
install -m 0644 "$SRC/deploy/family.service" /etc/systemd/system/family.service
# The sync: a quick run every night, a deep run every Sunday.
for u in family-sync.service family-sync.timer \
         family-sync-deep.service family-sync-deep.timer \
         family-db-backup.service family-db-backup.timer; do
  install -m 0644 "$SRC/deploy/$u" "/etc/systemd/system/$u"
done
# A consistent database dump, rotated locally and copied offsite.
install -d -o family -g family /opt/family/data/backups
# ⚠ The three site-specific values are NOT in the repository -- the files there
# carry placeholders (photos.example.com, 10.0.0.2, 10.0.0.3:9000, 10.0.0.0/24).
# They are substituted here out of the environment file, so a checkout stays
# free of anybody's addresses and a deploy still lands on the right ones.
#
#   FAMILY_SITE_HOST   the address people type          (server_name, Host)
#   FAMILY_PROXY_ADDR  the reverse proxy in front       (allow, nftables)
#   FAMILY_OUTPOST     the identity provider's outpost  (host:port)
#   FAMILY_ADMIN_NET   where you administer the box     (nftables, ssh)
#
# Unset ones stay at the placeholder -- which is a locked door, not an open one.
set -a; . /etc/family/env; set +a
subst() {
    sed -e "s|photos\.example\.com|${FAMILY_SITE_HOST:-photos.example.com}|g" \
        -e "s|10\.0\.0\.3:9000|${FAMILY_OUTPOST:-10.0.0.3:9000}|g" \
        -e "s|10\.0\.0\.2|${FAMILY_PROXY_ADDR:-10.0.0.2}|g" \
        -e "s|10\.0\.0\.0/24|${FAMILY_ADMIN_NET:-10.0.0.0/24}|g" "$1"
}
subst "$SRC/deploy/nginx.conf"      > /etc/nginx/sites-available/family
subst "$SRC/deploy/nginx-http.conf" > /etc/nginx/conf.d/family-limits.conf
chmod 0644 /etc/nginx/sites-available/family /etc/nginx/conf.d/family-limits.conf
ln -sf /etc/nginx/sites-available/family /etc/nginx/sites-enabled/family
rm -f /etc/nginx/sites-enabled/default
subst "$SRC/deploy/nftables.conf" > /etc/nftables.conf
chmod 0644 /etc/nftables.conf
echo "   nginx: host=${FAMILY_SITE_HOST:-photos.example.com} proxy=${FAMILY_PROXY_ADDR:-10.0.0.2} outpost=${FAMILY_OUTPOST:-10.0.0.3:9000}"

# ---------------------------------------------------------------------------
# The virus scanner (app/av.py). ClamAV inside the container, scanning uploads
# and the originals. clamd keeps its signatures in memory (~1 GB) -- on a 4 GB
# container that sits comfortably alongside the site (~1.5 GB) and the OS.
# ---------------------------------------------------------------------------
if ! command -v clamdscan >/dev/null; then
  DEBIAN_FRONTEND=noninteractive apt-get install -y clamav-daemon clamav-freshclam
  systemctl stop clamav-freshclam clamav-daemon 2>/dev/null || true
  freshclam --quiet || true          # first signatures, before the daemon starts
  systemctl enable clamav-freshclam clamav-daemon
fi
# Enforced every time (not only on the first install): big files -- RAW, and
# video above all -- have to be scanned too. The default is 25M, we go up to
# 1 GB. `--fdpass` uses MaxFileSize/MaxScanSize; StreamMaxLength is set as well,
# in case it is ever streamed.
sed -i 's/^MaxFileSize .*/MaxFileSize 1000M/; s/^MaxScanSize .*/MaxScanSize 1000M/; s/^StreamMaxLength .*/StreamMaxLength 1000M/' /etc/clamav/clamd.conf
mkdir -p /etc/systemd/system/clamav-daemon.service.d
# ⚠ clamd + the site (2500M) + the OS have to stay under 4 GB together. clamd
# needs ~435M for its signatures; 1200M is plenty, even for big files (fdpass
# streams, nothing is loaded into memory whole).
printf '[Service]\nMemoryMax=1200M\nMemoryHigh=1000M\n' > /etc/systemd/system/clamav-daemon.service.d/limits.conf
systemctl daemon-reload 2>/dev/null || true
command -v clamdscan >/dev/null && systemctl restart clamav-daemon 2>/dev/null || true

systemctl daemon-reload
systemctl enable nftables
systemctl restart nftables   # "enable --now" does NOT restart a running service
nginx -t && systemctl reload nginx
systemctl enable --now family.service
systemctl restart family.service
systemctl enable --now family-sync.timer family-sync-deep.timer family-db-backup.timer
sleep 2
curl -fsS -H "X-Family-Proxy: $(cat /etc/family/proxy-secret)" \
     http://127.0.0.1:8080/api/health && echo
