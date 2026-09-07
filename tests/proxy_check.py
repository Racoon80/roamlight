"""Wiem gleeft de Site, wann e seet, wou eng Ufro hierkënnt?

⚠ This is what the sign-in throttle stands on. `X-Forwarded-For` is a header,
  and a header is whatever the sender typed -- believe it from anyone and ten
  wrong passwords from ten invented addresses count as one try each, for ever.
  So the header is only read from a proxy that was named in the configuration.

Runs without an instance: it is the rule itself that is being checked.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _env                                                    # noqa: F401,E402

sys.path.insert(0, _env.app_root())
from app import config, security                               # noqa: E402


class Headers(dict):
    """⚠ HTTP header names do not care about case, and Starlette's `Headers`
    does not either. A plain dict does -- which is why the first version of
    this test failed against perfectly good code: it asked for
    `X-Forwarded-For` in a dict whose key was `x-forwarded-for`. A test double
    that is stricter than the real thing tests nothing but itself."""

    def __init__(self, d=None):
        super().__init__({k.lower(): v for k, v in (d or {}).items()})

    def get(self, key, default=None):
        return super().get(str(key).lower(), default)


class FakeRequest:
    """Only what `client_ip` touches: the peer and the headers."""

    class _Client:
        def __init__(self, host):
            self.host = host

    def __init__(self, peer, headers=None):
        self.client = self._Client(peer) if peer else None
        self.headers = Headers(headers)


def check(name, got, want):
    ok = got == want
    print(f"  {'ok  ' if ok else 'FAIL'} {name}: {got!r}" + ("" if ok else f" -- erwaart {want!r}"))
    return ok


def main():
    bad = 0

    # --- wiem gëtt gegleeft ------------------------------------------------
    config.TRUSTED_PROXIES = ()
    bad += not check("keng Proxyen -> keen gëtt gegleeft",
                     config.trusts_proxy("127.0.0.1"), False)

    config.TRUSTED_PROXIES = ("127.0.0.1", "192.168.1.0/24")
    for peer, want in (("127.0.0.1", True), ("192.168.1.62", True),
                       ("192.168.2.1", False), ("", False), ("net-eng-ip", False)):
        bad += not check(f"trusts_proxy({peer!r})", config.trusts_proxy(peer), want)

    # --- an dat entscheet, wat client_ip zréckgëtt -------------------------
    config.CLIENT_IP_HEADER = "X-Forwarded-For"
    forged = {"X-Forwarded-For": "203.0.113.77"}

    config.TRUSTED_PROXIES = ()
    bad += not check("ouni Vertrauen gëllt de TCP-Peer",
                     security.client_ip(FakeRequest("192.168.100.6", forged)),
                     "192.168.100.6")

    config.TRUSTED_PROXIES = ("192.168.100.6",)
    bad += not check("vum vertrauten Proxy gëllt den Header",
                     security.client_ip(FakeRequest("192.168.100.6", forged)),
                     "203.0.113.77")
    bad += not check("vun engem aneren Peer gëllt en NET",
                     security.client_ip(FakeRequest("10.9.9.9", forged)),
                     "10.9.9.9")
    bad += not check("eidele Header -> Peer",
                     security.client_ip(FakeRequest("192.168.100.6", {})),
                     "192.168.100.6")
    bad += not check("eng Kette -> déi éischt Adress",
                     security.client_ip(FakeRequest(
                         "192.168.100.6", {"X-Forwarded-For": "198.51.100.9, 172.68.1.1"})),
                     "198.51.100.9")

    config.CLIENT_IP_HEADER = "CF-Connecting-IP"
    bad += not check("aneren Header, wann esou agestallt",
                     security.client_ip(FakeRequest("192.168.100.6", {
                         "X-Forwarded-For": "203.0.113.77",
                         "CF-Connecting-IP": "198.51.100.9"})),
                     "198.51.100.9")

    print(f"\n{'ALLES GRÉNG' if not bad else str(bad) + ' FEELER'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
