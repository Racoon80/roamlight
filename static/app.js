/* The devices page: show a code (QR), list them, throw one out.

   ⚠ Here and not inline: `script-src 'self'` blocks an inline script, and the
   button would have done nothing without anybody noticing. */
(function () {
  "use strict";
  var post = window.famPost, get = window.famFetch;
  var btn = document.getElementById("pair");
  if (!btn) return;
  var box = document.getElementById("qr"), img = document.getElementById("qrimg");
  var codeEl = document.getElementById("code"), left = document.getElementById("left");
  var say = document.getElementById("pairsay"), list = document.getElementById("list");
  var note = document.getElementById("devnote");
  var timer = null;

  function zeit(s) {
    var m = Math.floor(s / 60), r = s % 60;
    return m + ":" + (r < 10 ? "0" : "") + r;
  }

  function laf(sek) {
    if (timer) clearInterval(timer);
    var rest = sek;
    left.textContent = "good for " + zeit(rest);
    timer = setInterval(function () {
      rest -= 1;
      if (rest <= 0) {
        clearInterval(timer); timer = null;
        box.hidden = true;
        say.textContent = "the code has run out — press again for a new one";
        return;
      }
      left.textContent = "good for " + zeit(rest);
    }, 1000);
  }

  btn.addEventListener("click", function () {
    btn.disabled = true; say.textContent = "…";
    post("/api/devices/pair", {}).then(function (d) {
      btn.disabled = false; say.textContent = "";
      /* If no SVG comes back (the library is missing on the server), the code
         stays there as text -- which is still perfectly usable. */
      img.textContent = "";
      if (d.svg && d.svg.slice(0, 4) === "<svg") img.innerHTML = d.svg;
      codeEl.textContent = d.code;
      box.hidden = false;
      laf(d.expires_in || 300);
    }).catch(function (e) {
      btn.disabled = false; say.textContent = "✗ " + (e.message || e);
    });
  });

  function zeil(d, mir) {
    var el = document.createElement("div");
    el.className = "dev__row";
    var wien = d.username === mir ? "" : " · " + d.username;
    el.innerHTML = '<b></b><span class="meta"></span>'
      + '<button class="btn alb2__remove" type="button">take off</button>';
    el.querySelector("b").textContent = d.name || "a device";
    el.querySelector(".meta").textContent =
      "added " + (d.created_at || "").slice(0, 10)
      + (d.last_seen ? " · last seen " + d.last_seen.slice(0, 16).replace("T", " ")
                     : " · not seen yet") + wien;
    el.querySelector("button").addEventListener("click", function () {
      post("/api/devices/revoke", { id: d.id }).then(lueden)
        .catch(function (e) { say.textContent = "✗ " + (e.message || e); });
    });
    return el;
  }

  function lueden() {
    get("/api/devices", {}).then(function (d) {
      var devs = d.devices || [];
      list.textContent = "";
      note.textContent = devs.length ? devs.length + " device" + (devs.length === 1 ? "" : "s")
                                     : "none yet";
      if (!devs.length) {
        var p = document.createElement("p");
        p.className = "meta";
        p.textContent = "No phone or tablet is connected.";
        list.appendChild(p);
        return;
      }
      devs.forEach(function (x) { list.appendChild(zeil(x, d.me)); });
    }).catch(function (e) { list.textContent = "✗ " + (e.message || e); });
  }

  /* Whoever scans the QR code with the ordinary camera lands on `/app/pair#c=…`.
     The code is in the fragment, so the server knows nothing about it -- but we
     can show it here, so it can be typed into the app. */
  if (location.pathname.indexOf("/app/pair") === 0 && location.hash.indexOf("c=") > -1) {
    codeEl.textContent = decodeURIComponent(location.hash.split("c=")[1] || "");
    box.hidden = false;
    img.textContent = "";
    say.textContent = "this is the code — type it into the app";
  }

  lueden();
})();
