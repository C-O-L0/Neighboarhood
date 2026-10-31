/* The block profile's lease check (UC6) as the buyer types: the same sums as app/services/block.py.
   Without this script the form still works; it submits and the server answers. Invalid input leaves the
   last answer in place until the form is submitted, so the server's messages stay the only error text. */
(function () {
  "use strict";

  var LEASE_YEARS = 99, MAX_AHEAD = 120, MAX_AGE = 120;
  var form = document.getElementById("lease");
  if (!form) { return; }
  var leaseYear = Number(form.dataset.leaseYear), now = Number(form.dataset.now);
  var result = form.querySelector(".lease-result");

  function whole(name, lo, hi) {
    var text = form.elements[name].value.trim();
    if (!/^\d{1,4}$/.test(text)) { return text ? NaN : null; }
    var n = Number(text);
    return n >= lo && n <= hi ? n : NaN;
  }

  function years(n) { return n + (n === 1 ? " year" : " years"); }

  function update() {
    var age = whole("age", 1, MAX_AGE), atAge = whole("at_age", 1, MAX_AGE);
    var minLeft = whole("min_left", 1, LEASE_YEARS - 1), year;
    if (age !== null || atAge !== null) {
      if (!(age >= 1 && atAge > age)) { return; }
      year = now + atAge - age;
    } else {
      year = whole("at", now, now + MAX_AHEAD);
      if (!(year >= now)) { return; }
    }
    if (Number.isNaN(minLeft)) { return; }
    var left = leaseYear + LEASE_YEARS - year;
    var when = "In " + year + (atAge ? ", at age " + atAge : "") + ", ";
    result.textContent = "";
    if (left <= 0) {
      result.appendChild(document.createTextNode(when + "the lease will have run out."));
      return;
    }
    result.appendChild(document.createTextNode(when + "about "));
    var strong = document.createElement("strong");
    strong.textContent = years(left);
    result.appendChild(strong);
    result.appendChild(document.createTextNode(" of lease will be left."));
    if (minLeft !== null && left < minLeft) {
      var warn = document.createElement("span");
      warn.className = "warn";
      warn.textContent = "That is below your " + minLeft + " years.";
      result.appendChild(document.createTextNode(" "));
      result.appendChild(warn);
    } else if (minLeft !== null) {
      result.appendChild(document.createTextNode(" That is at least your " + minLeft + " years."));
    }
  }

  form.addEventListener("input", update);
})();
