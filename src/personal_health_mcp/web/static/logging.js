// Logging page: client-side sort, filter, and an error-detail dialog.
// Loaded as an external file because the CSP forbids inline scripts.
(function () {
  "use strict";
  var table = document.getElementById("log-table");
  if (!table) return; // empty log -> nothing to wire up
  var tbody = table.querySelector("tbody");
  var rows = Array.prototype.slice.call(tbody.querySelectorAll("tr.log-row"));
  var search = document.getElementById("log-search");
  var providerSel = document.getElementById("log-provider");
  var levelSel = document.getElementById("log-level");
  var empty = document.getElementById("log-empty");

  // Render stored UTC ISO timestamps in the viewer's locale.
  rows.forEach(function (tr) {
    var cell = tr.querySelector(".log-time");
    var iso = cell && cell.getAttribute("data-iso");
    if (!iso) return;
    var d = new Date(iso);
    if (!isNaN(d.getTime())) cell.textContent = d.toLocaleString();
  });

  // ── filtering ────────────────────────────────────────────────────────
  function applyFilters() {
    var q = (search.value || "").toLowerCase();
    var prov = providerSel.value;
    var lvl = levelSel.value;
    var visible = 0;
    rows.forEach(function (tr) {
      var hay = (
        tr.getAttribute("data-provider") + " " + tr.getAttribute("data-message")
      ).toLowerCase();
      var ok =
        (!q || hay.indexOf(q) !== -1) &&
        (!prov || tr.getAttribute("data-provider") === prov) &&
        (!lvl || tr.getAttribute("data-level") === lvl);
      tr.hidden = !ok;
      if (ok) visible++;
    });
    if (empty) empty.hidden = visible !== 0;
  }
  [search, providerSel, levelSel].forEach(function (el) {
    if (!el) return;
    el.addEventListener("input", applyFilters);
    el.addEventListener("change", applyFilters);
  });

  // ── sorting ──────────────────────────────────────────────────────────
  var keyToAttr = {
    level: "data-level",
    provider: "data-provider",
    created: "data-created",
    message: "data-message",
  };
  function sortBy(th) {
    var key = th.getAttribute("data-key");
    var attr = keyToAttr[key];
    var dir = th.getAttribute("data-dir") === "asc" ? "desc" : "asc";
    table.querySelectorAll("th.sortable").forEach(function (h) {
      h.removeAttribute("data-dir");
    });
    th.setAttribute("data-dir", dir);
    var factor = dir === "asc" ? 1 : -1;
    rows.sort(function (a, b) {
      var av = a.getAttribute(attr) || "";
      var bv = b.getAttribute(attr) || "";
      return av.localeCompare(bv, undefined, { numeric: true }) * factor;
    });
    rows.forEach(function (tr) {
      tbody.appendChild(tr);
    });
  }
  table.querySelectorAll("th.sortable").forEach(function (th) {
    th.addEventListener("click", function () {
      sortBy(th);
    });
  });

  // ── detail dialog ────────────────────────────────────────────────────
  var dialog = document.getElementById("log-dialog");
  function openDetail(tr) {
    if (!dialog) return;
    document.getElementById("ld-provider").textContent = tr.getAttribute("data-provider");
    document.getElementById("ld-level").textContent = tr.getAttribute("data-level");
    var iso = tr.getAttribute("data-created");
    var d = new Date(iso);
    document.getElementById("ld-time").textContent = isNaN(d.getTime())
      ? iso
      : d.toLocaleString();
    document.getElementById("ld-message").textContent = tr.getAttribute("data-message");
    if (typeof dialog.showModal === "function") dialog.showModal();
    else dialog.setAttribute("open", "");
  }
  rows.forEach(function (tr) {
    tr.addEventListener("click", function () {
      openDetail(tr);
    });
    tr.addEventListener("keydown", function (ev) {
      if (ev.key === "Enter" || ev.key === " ") {
        ev.preventDefault();
        openDetail(tr);
      }
    });
  });
  var closeBtn = document.getElementById("ld-close");
  if (closeBtn && dialog) {
    closeBtn.addEventListener("click", function () {
      dialog.close();
    });
  }
  // Click on the backdrop (outside the dialog content) closes it.
  if (dialog) {
    dialog.addEventListener("click", function (ev) {
      if (ev.target === dialog) dialog.close();
    });
  }
})();
