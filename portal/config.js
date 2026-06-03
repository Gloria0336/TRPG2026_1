(() => {
  const origin = window.location.origin && window.location.origin !== "null"
    ? window.location.origin
    : "http://127.0.0.1:8001";
  window.TRPG_PORTAL_API_BASE = window.TRPG_PORTAL_API_BASE || origin;
})();
