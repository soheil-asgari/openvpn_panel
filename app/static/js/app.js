// OpenVPN Web Panel — progressive enhancement (theme toggle, CSRF for HTMX, toasts).
(function () {
  "use strict";

  var root = document.documentElement;
  var stored = null;
  try {
    stored = window.localStorage.getItem("ovpn-theme");
  } catch (e) {
    stored = null;
  }
  if (stored) {
    root.setAttribute("data-theme", stored);
  }

  function toggleTheme() {
    var current = root.getAttribute("data-theme") || "system";
    var next = current === "dark" ? "light" : "dark";
    root.setAttribute("data-theme", next);
    try {
      window.localStorage.setItem("ovpn-theme", next);
    } catch (e) {
      /* ignore */
    }
  }

  function csrfToken() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute("content") : "";
  }

  function toast(message) {
    var el = document.getElementById("toast");
    if (!el) return;
    el.textContent = message;
    el.hidden = false;
    window.setTimeout(function () {
      el.hidden = true;
    }, 2500);
  }

  document.addEventListener("click", function (event) {
    var target = event.target;
    if (target && target.matches("[data-theme-toggle]")) {
      toggleTheme();
    }
  });

  document.addEventListener("htmx:configRequest", function (event) {
    var token = csrfToken();
    if (token) {
      event.detail.headers["X-CSRF-Token"] = token;
    }
  });

  document.addEventListener("htmx:responseError", function () {
    toast("Request failed");
  });

  // Traffic chart on the dashboard.
  window.addEventListener("load", function () {
    var canvas = document.getElementById("traffic-chart");
    if (!canvas || typeof window.Chart === "undefined") return;
    var styles = getComputedStyle(root);
    window.__ovpnChart = new window.Chart(canvas, {
      type: "line",
      data: {
        labels: [],
        datasets: [
          {
            label: "In",
            data: [],
            borderColor: styles.getPropertyValue("--primary").trim() || "#2563eb",
            tension: 0.3,
          },
        ],
      },
      options: { responsive: true, plugins: { legend: { display: false } } },
    });
  });
})();
