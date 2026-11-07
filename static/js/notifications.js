/**
 * notifications.js — Notification badge update
 * Shared across pages that don't include dashboard.js
 */
(function () {
  'use strict';

  document.addEventListener('DOMContentLoaded', function () {
    const badge = document.getElementById('notif-badge');
    if (!badge) return;

    function pollNotifications() {
      fetch('/api/notifications', {
        headers: { 'X-CSRFToken': window.CSRF_TOKEN || '' },
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          const count = data.unread_count || 0;
          badge.textContent = count > 99 ? '99+' : String(count);
          badge.style.display = count > 0 ? 'flex' : 'none';
        })
        .catch(function () { /* non-critical */ });
    }

    pollNotifications();
    setInterval(pollNotifications, 60000);

    // Sidebar hamburger (fallback for pages without tickets.js)
    const hamburger = document.getElementById('hamburger-btn');
    const overlay = document.getElementById('sidebar-overlay');

    if (hamburger && !hamburger._bound) {
      hamburger._bound = true;
      hamburger.addEventListener('click', function () {
        document.body.classList.toggle('sidebar-open');
      });
    }
    if (overlay && !overlay._bound) {
      overlay._bound = true;
      overlay.addEventListener('click', function () {
        document.body.classList.remove('sidebar-open');
      });
    }
  });
})();
