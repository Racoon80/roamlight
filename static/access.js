/* Who sees which album. Its own page since 08.09.2026 -- it was section 05
   of the settings, and it is the one thing on that page a person opens on
   purpose rather than to look something up. */
(function () {
  "use strict";

  Array.prototype.forEach.call(document.querySelectorAll(".aclrow"), function (form) {
    var say = form.querySelector(".alb__say");
    var state = form.querySelector(".aclrow__state");

    function send(liste) {
      say.textContent = "…";
      return window.famPost("/api/albums/audience", {
        year: form.dataset.year, country: form.dataset.country,
        event: form.dataset.event, audience: liste
      }).then(function (d) {
        say.textContent = d.admin_only ? "only you" : "saved";
        state.textContent = d.admin_only ? "only you" : d.audience.length + " allowed";
      }).catch(function (e) { say.textContent = "✗ " + e.message; });
    }

    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var liste = Array.prototype.filter.call(
        form.querySelectorAll('input[type="checkbox"]'), function (c) { return c.checked; })
        .map(function (c) { return c.value; });
      /* An empty list opens the album to everybody -- that is a change somebody
         should notice, so it asks first. */
      if (!liste.length && !window.confirm(
          "Nothing is ticked.\n\nThis album will then be seen by nobody but you.")) return;
      send(liste);
    });

    form.querySelector("[data-open]").addEventListener("click", function () {
      Array.prototype.forEach.call(form.querySelectorAll('input[type="checkbox"]'),
        function (c) { c.checked = false; });
      send([]);
    });
  });
})();
