/* Settings: fetch the list from the directory, and set the album permissions. */
(function () {
  "use strict";
  var rsay = document.getElementById("rsay");
  var btn = document.getElementById("refresh");
  if (btn) {
    btn.addEventListener("click", function () {
      btn.disabled = true; rsay.textContent = "asking Authentik…";
      window.famPost("/api/members/refresh", {}).then(function (d) {
        if (!d.ok) { rsay.textContent = "✗ " + (d.error || "failed"); btn.disabled = false; return; }
        rsay.textContent = d.members + " members · " + d.groups + " groups — reloading";
        setTimeout(function () { location.reload(); }, 900);
      }).catch(function (e) { rsay.textContent = "✗ " + e.message; btn.disabled = false; });
    });
  }

  /* ---- Tags a Persounen verwalten ---- */
  Array.prototype.forEach.call(document.querySelectorAll(".names__col"), function (col) {
    var kind = col.dataset.kind;

    function ruff(body, dann) {
      window.famPost("/api/names", body).then(dann).catch(function (e) {
        window.alert(e.message);
      });
    }

    col.querySelector("[data-add]").addEventListener("click", function () {
      var inp = col.querySelector(".names__new");
      var name = inp.value.trim();
      if (!name) { inp.focus(); return; }
      /* ⚠ A name that sits on no photograph is created here all the same --
         that is the whole point of "add by hand". It then stands in the lists
         and the dropdowns, with the count 0. */
      ruff({ kind: kind, action: "new", name: name }, function () { location.reload(); });
    });
    col.querySelector(".names__new").addEventListener("keydown", function (e) {
      if (e.key === "Enter") { e.preventDefault(); col.querySelector("[data-add]").click(); }
    });

    Array.prototype.forEach.call(col.querySelectorAll("[data-suggest]"), function (b) {
      b.addEventListener("click", function () {
        ruff({ kind: kind, action: "new", name: b.dataset.suggest },
             function () { location.reload(); });
      });
    });

    Array.prototype.forEach.call(col.querySelectorAll(".names__list li[data-id]"),
      function (li) {
        var say = li.querySelector(".names__say");
        li.querySelector("[data-save]").addEventListener("click", function () {
          var neu = li.querySelector(".names__name").value.trim();
          if (!neu) return;
          ruff({ kind: kind, action: "rename", id: +li.dataset.id, name: neu },
               function (d) {
                 say.textContent = d.merged_into ? "merged" : "saved";
                 if (d.merged_into) setTimeout(function () { location.reload(); }, 900);
               });
        });
        li.querySelector("[data-del]").addEventListener("click", function () {
          var name = li.querySelector(".names__name").value.trim();
          if (!window.confirm("Take “" + name + "” away?\n\nIt comes off every " +
                              "photograph it is on. The photographs stay exactly " +
                              "where they are.")) return;
          ruff({ kind: kind, action: "forget", id: +li.dataset.id },
               function () { location.reload(); });
        });
      });
  });

  /* ---- Single sign-on, and the key under the mat ------------------------

     ⚠ Nothing here decides anything. The switch, the guard that refuses to turn
     it off when there is no password account, the refusal to turn it on without
     an issuer -- all of that is in the routes. A page script can be read and
     re-run by anybody; it is a convenience, never a lock. */
  function val(id) { var e = document.getElementById(id); return e ? e.value.trim() : ""; }

  /* ⚠ Every button goes through here, and the reason is a bug that shipped:
     `val()` was deleted in a rewrite while three handlers still called it, so a
     click threw a ReferenceError before the first line of work and the page did
     NOTHING -- no error, no spinner, no clue. A button that fails silently is
     worse than one that says why. This catches the synchronous throw as well as
     the rejected promise, and always puts something in the status line. */
  function on(id, sayId, build) {
    var b = document.getElementById(id);
    if (!b) { return; }
    b.addEventListener("click", function () {
      var work;
      try {
        work = build();
      } catch (e) {
        say(sayId, "the page is broken here: " + e.message, true);
        return;
      }
      if (!work) { return; }
      b.disabled = true; say(sayId, "\u2026");
      work.then(function () {
        say(sayId, "saved");
        setTimeout(function () { location.reload(); }, 700);
      }).catch(function (e) { say(sayId, e.message, true); b.disabled = false; });
    });
  }

  function say(id, msg, bad) {
    var e = document.getElementById(id);
    if (e) { e.textContent = (bad ? "\u2717 " : "") + msg; }
  }

  var onbox = document.getElementById("oidc-on");
  if (onbox) {
    onbox.addEventListener("change", function () {
      var want = onbox.checked;
      onbox.disabled = true; say("oidc-say", "…");
      window.famPost("/api/auth/mode", { on: want }).then(function () {
        location.reload();
      }).catch(function (e) {
        say("oidc-say", e.message, true);
        onbox.checked = !want; onbox.disabled = false;
      });
    });
  }

  var osave = document.getElementById("oidc-save");
  if (osave) {
    osave.addEventListener("click", function () {
      var sec = val("oi-secret");
      osave.disabled = true; say("oidc-test-say", "…");
      /* ⚠ The secret first. If the issuer saved and the secret then failed, the
         page would come back showing a provider that cannot be talked to. */
      var first = sec
        ? window.famPost("/api/auth/secret", { which: "oidc", value: sec })
        : Promise.resolve();
      first.then(function () {
        return window.famPost("/api/auth/config", {
          oidc_issuer: val("oi-issuer"), oidc_client_id: val("oi-client"),
          oidc_scopes: val("oi-scopes"),
          oidc_username_claim: val("oi-userclaim"),
          oidc_groups_claim: val("oi-groupclaim")
        });
      }).then(function () {
        say("oidc-test-say", "saved");
        setTimeout(function () { location.reload(); }, 700);
      }).catch(function (e) {
        say("oidc-test-say", e.message, true); osave.disabled = false;
      });
    });
  }

  var otest = document.getElementById("oidc-test");
  if (otest) {
    otest.addEventListener("click", function () {
      var out = document.getElementById("oidc-test-out");
      otest.disabled = true; say("oidc-test-say", "asking…");
      window.famPost("/api/auth/oidc/test", {}).then(function (d) {
        say("oidc-test-say", "the provider answered");
        out.textContent = JSON.stringify(d, null, 2);
        out.hidden = false;
        otest.disabled = false;
      }).catch(function (e) {
        say("oidc-test-say", e.message, true);
        out.hidden = true;
        otest.disabled = false;
      });
    });
  }

  /* D'Gruppennimm an d'vertraut Peeren. */
  on("role-save", "role-say", function () {
    return window.famPost("/api/auth/config", {
      admin_groups: val("cfg-admin"), viewer_groups: val("cfg-viewer"),
      contributor_groups: val("cfg-contrib"), trusted_peers: val("cfg-peers")
    });
  });

  /* D'Verzeechnes: Adress an Token. ⚠ Den Token GEET ZEESCHT -- géing d'Adress
     duerchgoen an den Token duerno net, stéing op der Säit eng Adress, déi keen
     erreecht. */
  on("cfg-save", "cfg-say", function () {
    var tok = val("cfg-token"), url = val("cfg-url");
    var first = tok
      ? window.famPost("/api/auth/secret", { which: "authentik", value: tok })
      : Promise.resolve();
    return first.then(function () {
      return window.famPost("/api/auth/config", { authentik_url: url });
    });
  });

  var pwsave = document.getElementById("pw-save");
  if (pwsave) {
    pwsave.addEventListener("click", function () {
      var u = val("pw-user"), pw = val("pw-new");
      if (!u || !pw) { say("pw-say", "a name and a password", true); return; }
      pwsave.disabled = true; say("pw-say", "…");
      window.famPost("/api/members/make-admin", {
        username: u, password: pw,
        admin: document.getElementById("pw-admin").checked
      }).then(function (d) {
        say("pw-say", d.user + " \u2014 " + d.groups.join(", "));
        document.getElementById("pw-new").value = "";
        setTimeout(function () { location.reload(); }, 1200);
      }).catch(function (e) { say("pw-say", e.message, true); pwsave.disabled = false; });
    });
  }
})();
