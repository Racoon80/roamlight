"""Déi zwee Weeër no bausse: Apple an Google.

⚠ Nothing here decides WHETHER to send -- `notify.py` does that. This file only
  knows how to hand one message to one phone.

⚠ Neither of these can be avoided if the message is to arrive while the app is
  closed. That is the trade, and it should be said plainly: the title and the
  one-line body pass through Apple's or Google's servers. So they carry no
  names and no places beyond the album's own title, and never a photograph.
"""
import json
import logging
import time
from pathlib import Path

from . import config

log = logging.getLogger("family")

_APNS_LIVE = "https://api.push.apple.com"
_APNS_TEST = "https://api.sandbox.push.apple.com"
_FCM = "https://fcm.googleapis.com/v1/projects/{project}/messages:send"
_GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"


# --- Ënnerschreiwen --------------------------------------------------------

def _b64(raw: bytes) -> str:
    import base64
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _jwt(header: dict, claims: dict, key_pem: bytes, alg: str) -> str:
    """A signed JWT, with `cryptography` and nothing else.

    ⚠ ES256 wants the signature as raw r‖s, 64 bytes. `cryptography` hands back
      DER, which is a different shape entirely -- pass that on and Apple
      answers `InvalidProviderToken` with no hint as to why.
    """
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec, padding
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

    key = serialization.load_pem_private_key(key_pem, password=None)
    signing_input = f"{_b64(json.dumps(header, separators=(',', ':')).encode())}." \
                    f"{_b64(json.dumps(claims, separators=(',', ':')).encode())}"
    if alg == "ES256":
        der = key.sign(signing_input.encode(), ec.ECDSA(hashes.SHA256()))
        r, s = decode_dss_signature(der)
        sig = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    else:                                                        # RS256
        sig = key.sign(signing_input.encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{signing_input}.{_b64(sig)}"


# --- Apple -----------------------------------------------------------------

_apns_token = {"value": None, "made": 0.0}


def _apns_jwt() -> str:
    """⚠ Kept for the best part of an hour and then made again. Apple refuses a
    token older than an hour, and it also refuses an account that asks for a new
    one too often -- so neither "every request" nor "once forever" is right."""
    if _apns_token["value"] and time.time() - _apns_token["made"] < 45 * 60:
        return _apns_token["value"]
    pem = Path(config.APNS_KEY_FILE).read_bytes()
    tok = _jwt({"alg": "ES256", "kid": config.APNS_KEY_ID},
               {"iss": config.APNS_TEAM_ID, "iat": int(time.time())},
               pem, "ES256")
    _apns_token.update(value=tok, made=time.time())
    return tok


def apns(token: str, title: str, body: str, data: dict) -> None:
    from . import notify
    if not config.apns_ready():
        raise notify.NotReady("APNs is not set up (key, key id, team, topic)")
    import httpx

    base = _APNS_TEST if config.APNS_SANDBOX else _APNS_LIVE
    payload = {"aps": {"alert": {"title": title, "body": body},
                       "sound": "default", "thread-id": data.get("album", "")},
               **{k: v for k, v in data.items()}}
    # ⚠ HTTP/2 is not optional here -- Apple speaks nothing else.
    with httpx.Client(http2=True, timeout=15) as c:
        r = c.post(f"{base}/3/device/{token}",
                   headers={"authorization": f"bearer {_apns_jwt()}",
                            "apns-topic": config.APNS_TOPIC,
                            "apns-push-type": "alert",
                            "apns-priority": "10"},
                   json=payload)
    if r.status_code == 200:
        return
    reason = ""
    try:
        reason = r.json().get("reason", "")
    except Exception:                                            # noqa: BLE001
        pass
    if r.status_code == 410 or reason == "Unregistered":
        raise notify.Unregistered(reason or "410")
    # ⚠ `BadDeviceToken` is NOT treated as dead. It is also exactly what Apple
    #   answers when the address was minted by the other service -- a build
    #   signed for development against the live endpoint, or FAMILY_APNS_SANDBOX
    #   set the wrong way. Deleting on it would wipe every registered phone in
    #   the house because of one line in a settings file. It counts as a
    #   failure, and five of those hide the device until it registers again.
    raise RuntimeError(f"APNs {r.status_code} {reason}")


# --- Google ----------------------------------------------------------------

_fcm_token = {"value": None, "until": 0.0}


def _fcm_access_token(creds: dict) -> str:
    """A service account, swapped for an access token that lasts an hour."""
    if _fcm_token["value"] and time.time() < _fcm_token["until"] - 120:
        return _fcm_token["value"]
    import httpx
    now = int(time.time())
    assertion = _jwt(
        {"alg": "RS256", "typ": "JWT"},
        {"iss": creds["client_email"],
         "scope": "https://www.googleapis.com/auth/firebase.messaging",
         "aud": _GOOGLE_TOKEN, "iat": now, "exp": now + 3600},
        creds["private_key"].encode(), "RS256")
    with httpx.Client(timeout=15) as c:
        r = c.post(_GOOGLE_TOKEN, data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": assertion})
    r.raise_for_status()
    d = r.json()
    _fcm_token.update(value=d["access_token"], until=time.time() + d.get("expires_in", 3600))
    return _fcm_token["value"]


def fcm(token: str, title: str, body: str, data: dict) -> None:
    from . import notify
    if not config.fcm_ready():
        raise notify.NotReady("FCM is not set up (no service account file)")
    import httpx
    creds = json.loads(Path(config.FCM_CREDENTIALS).read_text())
    access = _fcm_access_token(creds)
    msg = {"message": {"token": token,
                       "notification": {"title": title, "body": body},
                       # ⚠ Everything in `data` has to be a STRING for FCM. A
                       #   number goes through as an error, not as a number.
                       "data": {k: str(v) for k, v in data.items()},
                       "android": {"priority": "high"}}}
    with httpx.Client(timeout=15) as c:
        r = c.post(_FCM.format(project=creds["project_id"]),
                   headers={"Authorization": f"Bearer {access}"}, json=msg)
    if r.status_code == 200:
        return
    reason = ""
    try:
        reason = r.json().get("error", {}).get("status", "")
    except Exception:                                            # noqa: BLE001
        pass
    if r.status_code == 404 or reason == "UNREGISTERED":
        raise notify.Unregistered(reason or "404")
    raise RuntimeError(f"FCM {r.status_code} {reason}")
