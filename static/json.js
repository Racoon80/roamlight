/* Read an answer without choking on an error page.

   ⚠ `r.json()` gives back "JSON.parse: unexpected character at line 1 column 1"
   whenever the server does not send JSON -- and it does that as soon as the
   answer comes from the proxy (a 502, because the service is restarting) or
   from the identity provider (a sign-in page) instead of the app. That error
   tells the reader nothing.

   So: always read it as text, only then try to parse it, and otherwise show a
   message somebody can actually understand. */
window.famFetch = function (url, opts) {
  return fetch(url, opts).then(function (r) {
    return r.text().then(function (txt) {
      var d = null;
      try { d = txt ? JSON.parse(txt) : null; } catch (e) { d = null; }
      if (r.ok && d !== null) return d;
      if (r.ok) return {};
      var msg = (d && (d.detail || d.error)) ||
        (r.status === 502 || r.status === 503
          ? "the site is restarting — try again in a moment"
          : r.status === 403 ? "not allowed"
          : r.status === 401 ? "your session has ended — reload the page"
          : "the server answered " + r.status);
      var err = new Error(msg);
      err.status = r.status;
      throw err;
    });
  }, function () {
    throw new Error("no answer from the site — is it still running?");
  });
};

window.famPost = function (url, body) {
  return window.famFetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {})
  });
};
