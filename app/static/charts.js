/* Draws the trends and rankings charts from the JSON embedded in the page (no other data source). */
(function () {
  "use strict";

  // The site palette (app.css): accent for the monthly figure, warm for the rolling one, a quiet grey for counts.
  var BLUE = "#1f6f5c";
  var ORANGE = "#b4520f";
  var GREY = "#d3d9d4";
  var BARS = "#5d8fc7";

  function read(id) {
    var el = document.getElementById(id);
    return el ? JSON.parse(el.textContent) : null;
  }

  function num(v) {
    return v === null ? null : Number(v);
  }

  function dollars(v) {
    return "$" + Number(v).toLocaleString("en-SG", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  function whole(v) {
    return "$" + Number(v).toLocaleString("en-SG", { maximumFractionDigits: 0 });
  }

  var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  function monthLabel(text) {  // "2026-09" is "Sep 2026", as the tables write months
    return MONTHS[Number(text.slice(5, 7)) - 1] + " " + text.slice(0, 4);
  }

  function axisTitle(text) {
    return { display: true, text: text };
  }

  function trends(canvas, data) {
    new Chart(canvas, {
      data: {
        labels: data.months.map(monthLabel),
        datasets: [
          { type: "line", label: "Typical price each month", data: data.median.map(num), yAxisID: "y",
            borderColor: BLUE, backgroundColor: BLUE, spanGaps: false, tension: 0, order: 1,
            pointRadius: data.months.length > 36 ? 0 : 2, pointHoverRadius: 4 },  // dots crowd a long series
          { type: "line", label: "12-month typical price", data: data.rolling_median.map(num), yAxisID: "y",
            borderColor: ORANGE, backgroundColor: ORANGE, borderDash: [6, 3], spanGaps: false, tension: 0,
            pointRadius: 0, order: 2 },
          { type: "bar", label: "Sales each month", data: data.n, yAxisID: "y1",
            backgroundColor: GREY, borderColor: GREY, order: 3 }
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { display: false },  // the page draws its own legend, with the dashed line as a line
          tooltip: { callbacks: { label: function (c) {
            return c.dataset.label + ": " + (c.dataset.yAxisID === "y" ? dollars(c.parsed.y) : c.parsed.y);
          } } }
        },
        scales: {
          x: { ticks: { maxRotation: 0, autoSkipPadding: 16 } },
          y: { position: "left", title: axisTitle("Typical price (SGD)"),
               ticks: { callback: function (v) { return whole(v); } } },
          y1: { position: "right", beginAtZero: true, title: axisTitle("Sales each month"),
                grid: { drawOnChartArea: false }, ticks: { precision: 0 } }
        }
      }
    });
  }

  function rankings(canvas, data) {
    var box = canvas.parentElement;
    box.style.height = Math.max(240, data.towns.length * 22 + 110) + "px";
    new Chart(canvas, {
      data: {
        labels: data.towns,
        datasets: [
          { type: "bar", label: "Town typical price", data: data.median.map(num),
            backgroundColor: BARS, borderColor: BARS, order: 2 },
          { type: "line", label: "Island typical price", data: data.towns.map(function () { return Number(data.island_median); }),
            borderColor: ORANGE, backgroundColor: ORANGE, borderWidth: 2, borderDash: [6, 3], pointRadius: 0, order: 1 }
        ]
      },
      options: {
        indexAxis: "y",
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: true, position: "bottom" },
          tooltip: { callbacks: { label: function (c) { return c.dataset.label + ": " + dollars(c.parsed.x); } } }
        },
        scales: {
          x: { beginAtZero: true, title: axisTitle("Typical price (SGD)"),
               ticks: { callback: function (v) { return dollars(v); } } },
          y: { title: axisTitle("Town (rank order)"), ticks: { autoSkip: false } }
        }
      }
    });
  }

  if (typeof Chart === "undefined") { return; }
  document.querySelectorAll("canvas[data-chart]").forEach(function (canvas) {
    var data = read(canvas.dataset.source);
    if (!data) { return; }
    if (canvas.dataset.chart === "trends") { trends(canvas, data); }
    if (canvas.dataset.chart === "rankings") { rankings(canvas, data[Number(canvas.dataset.index)]); }
  });
})();
