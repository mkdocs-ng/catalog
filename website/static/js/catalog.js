// Filters and sorting for the All projects table (all.md). Without JavaScript the
// page still shows the full table; the filter bar stays hidden.
(function () {
  "use strict";
  var bar = document.querySelector(".catalog-filters");
  var table = document.querySelector(".catalog-all table");
  if (!bar || !table) return;

  var body = table.tBodies[0];
  var rows = Array.prototype.slice.call(body.rows);
  var count = bar.querySelector(".catalog-count");
  var fields = {
    q: bar.querySelector('[name="q"]'),
    category: bar.querySelector('[name="category"]'),
    type: bar.querySelector('[name="type"]'),
    ng: bar.querySelector('[name="ng"]'),
    active: bar.querySelector('[name="active"]'),
  };
  rows.forEach(function (row) {
    row._text = row.cells[0].textContent.toLowerCase();
  });

  function value(field) {
    return field.type === "checkbox" ? (field.checked ? "1" : "") : field.value.trim();
  }

  function apply() {
    var q = value(fields.q).toLowerCase();
    var category = value(fields.category);
    var type = value(fields.type);
    var ng = value(fields.ng);
    var active = value(fields.active);
    var shown = 0;
    rows.forEach(function (row) {
      var ok =
        (!q || row._text.indexOf(q) !== -1) &&
        (!category || row.dataset.category === category) &&
        (!type || row.dataset.type === type) &&
        (!ng || row.dataset.ng === "1") &&
        (!active || row.dataset.active === "1");
      row.hidden = !ok;
      if (ok) shown++;
    });
    count.textContent = shown === rows.length ? rows.length + " projects" : shown + " of " + rows.length + " projects";

    // Keep the filters in the address, so a filtered view can be shared.
    var params = new URLSearchParams();
    Object.keys(fields).forEach(function (key) {
      var v = value(fields[key]);
      if (v) params.set(key, v);
    });
    var query = params.toString();
    history.replaceState(null, "", location.pathname + (query ? "?" + query : "") + location.hash);
  }

  var params = new URLSearchParams(location.search);
  Object.keys(fields).forEach(function (key) {
    var field = fields[key];
    var v = params.get(key);
    if (v !== null) {
      if (field.type === "checkbox") field.checked = v === "1";
      else field.value = v;
    }
    field.addEventListener(field.type === "search" ? "input" : "change", apply);
  });

  // Click a column header to sort; click again to reverse. Numbers sort high to low first.
  var headers = Array.prototype.slice.call(table.tHead.rows[0].cells);
  headers.forEach(function (th, index) {
    var button = document.createElement("button");
    button.type = "button";
    button.textContent = th.textContent;
    th.textContent = "";
    th.appendChild(button);
    button.addEventListener("click", function () {
      var numeric = th.dataset.sort === "number";
      var current = th.getAttribute("aria-sort");
      var descending = current ? current === "ascending" : numeric;
      headers.forEach(function (other) {
        other.removeAttribute("aria-sort");
      });
      th.setAttribute("aria-sort", descending ? "descending" : "ascending");
      rows.sort(function (a, b) {
        var x = a.cells[index].dataset.value || a.cells[index].textContent;
        var y = b.cells[index].dataset.value || b.cells[index].textContent;
        var order = numeric ? Number(x) - Number(y) : x.localeCompare(y);
        if (order !== 0) return descending ? -order : order;
        return Number(a.dataset.rank) - Number(b.dataset.rank); // ties keep the catalog ranking
      });
      rows.forEach(function (row) {
        body.appendChild(row);
      });
    });
  });

  bar.hidden = false;
  apply();
})();
