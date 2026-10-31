/* Draws block maps from JSON embedded in the page (no other data source); the list beside it has the same places.
   A pin is a block (with a link to it) or a hawker centre (kind "hawker", no link).
   Every text shown on the map is set with textContent, never parsed as HTML. */
(function () {
  "use strict";

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) { node.className = className; }
    if (text) { node.textContent = text; }
    return node;
  }

  function popup(pin) {
    var box = el("div", "map-popup");
    box.appendChild(el("strong", "", pin.name));
    box.appendChild(el("span", "", pin.detail));
    if (pin.href) {
      var link = el("a", "", "Open block");
      link.href = pin.href;
      box.appendChild(link);
    }
    return box;
  }

  function priceIcon(pin) {
    var kind = pin.chosen ? " chosen" : pin.kind === "hawker" ? " hawker" : "";
    var label = el("span", "price-pin" + kind, pin.label);
    return L.divIcon({ className: "price-pin-wrap", html: label, iconSize: null, iconAnchor: [0, 0] });
  }

  function draw(container) {
    var data = JSON.parse(document.getElementById(container.dataset.source).textContent);
    var map = L.map(container, { scrollWheelZoom: false });
    var fallback = container.parentElement.querySelector(".map-fallback");
    var tiles = L.tileLayer(container.dataset.tiles, {
      maxZoom: Number(container.dataset.maxZoom),
      attribution: container.dataset.attribution
    });
    tiles.once("tileerror", function () { if (fallback) { fallback.hidden = false; } });
    tiles.addTo(map);

    var bounds = L.latLngBounds([]);
    if (data.place) {
      var centre = [Number(data.place.lat), Number(data.place.lon)];
      L.circle(centre, {
        radius: data.place.radius_m, color: "#1f6f5c", weight: 2, dashArray: "6 6", fillOpacity: 0.05
      }).addTo(map);
      // The whole search area, not only the blocks found in it (a circle's own bounds need a view first).
      bounds.extend(L.latLng(centre).toBounds(2 * data.place.radius_m));
    }
    data.pins.forEach(function (pin) {
      var at = [Number(pin.lat), Number(pin.lon)];
      L.marker(at, {
        icon: priceIcon(pin), title: pin.name, alt: pin.name, keyboard: true, zIndexOffset: pin.chosen ? 1000 : 0
      }).bindPopup(popup(pin)).addTo(map);
      bounds.extend(at);
    });

    function fit() {
      map.invalidateSize();
      if (bounds.getNorthEast().equals(bounds.getSouthWest())) {
        map.setView(bounds.getCenter(), 16);
      } else {
        map.fitBounds(bounds, { padding: [24, 24], maxZoom: 16 });
      }
    }
    fit();
    // The list beside the map can still change size while fonts load; fit again once the size settles.
    if (typeof ResizeObserver !== "undefined") {
      var last = container.clientWidth + "x" + container.clientHeight;
      new ResizeObserver(function () {
        var now = container.clientWidth + "x" + container.clientHeight;
        if (now !== last) { last = now; fit(); }
      }).observe(container);
    }
  }

  if (typeof L === "undefined") { return; }
  document.querySelectorAll("[data-map]").forEach(draw);
})();
