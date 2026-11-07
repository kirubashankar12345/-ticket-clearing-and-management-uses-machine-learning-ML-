/**
 * dashboard.js — Admin charts and notification polling
 */
(function () {
  'use strict';

  const PALETTE_PRIMARY = [
    '#6366f1', '#7c3aed', '#3b82f6', '#10b981',
    '#f59e0b', '#ef4444', '#8b5cf6', '#06b6d4',
  ];

  // ---- Charts ----
  function initCharts() {
    const volumeEl = document.getElementById('chart-volume');
    const categoryEl = document.getElementById('chart-category');
    const priorityEl = document.getElementById('chart-priority');

    if (!volumeEl && !categoryEl && !priorityEl) return;

    fetch('/api/admin/stats', {
      headers: { 'X-CSRFToken': window.CSRF_TOKEN || '' },
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (volumeEl && data.volume_by_day) {
          renderVolumeChart(volumeEl, data.volume_by_day);
        }
        if (categoryEl && data.by_category) {
          renderDoughnutChart(categoryEl, data.by_category, 'Tickets by Category');
        }
        if (priorityEl && data.by_priority) {
          renderDoughnutChart(priorityEl, data.by_priority, 'Tickets by Priority');
        }
      })
      .catch(function (err) {
        console.error('Failed to load stats:', err);
      });
  }

  function renderVolumeChart(el, volumeData) {
    const labels = Object.keys(volumeData);
    const values = Object.values(volumeData);

    // eslint-disable-next-line no-undef
    new Chart(el, {
      type: 'line',
      data: {
        labels: labels,
        datasets: [{
          label: 'Tickets',
          data: values,
          borderColor: '#6366f1',
          backgroundColor: 'rgba(99,102,241,0.08)',
          borderWidth: 2,
          pointRadius: 3,
          pointBackgroundColor: '#6366f1',
          tension: 0.35,
          fill: true,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: { mode: 'index', intersect: false },
        },
        scales: {
          x: {
            grid: { display: false },
            ticks: {
              maxTicksLimit: 10,
              font: { size: 11 },
              color: '#94a3b8',
            },
          },
          y: {
            beginAtZero: true,
            ticks: { stepSize: 1, font: { size: 11 }, color: '#94a3b8' },
            grid: { color: '#e2e8f0' },
          },
        },
      },
    });
  }

  function renderDoughnutChart(el, dataObj, label) {
    const labels = Object.keys(dataObj);
    const values = Object.values(dataObj);

    // eslint-disable-next-line no-undef
    new Chart(el, {
      type: 'doughnut',
      data: {
        labels: labels,
        datasets: [{
          data: values,
          backgroundColor: PALETTE_PRIMARY.slice(0, labels.length),
          borderWidth: 2,
          borderColor: '#ffffff',
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: {
            position: 'bottom',
            labels: { font: { size: 12 }, padding: 16, color: '#475569' },
          },
        },
        cutout: '60%',
      },
    });
  }

  // ---- Admin bulk AI clearing progress ----
  function initAiClearing() {
    const form = document.getElementById('ai-clearing-form');
    const progressEl = document.getElementById('ai-clearing-progress');
    if (!form || !progressEl) return;

    const button = document.getElementById('ai-clearing-start');
    const errorEl = document.getElementById('ai-clearing-error');
    const statusTemplate = form.dataset.statusTemplate;
    const latestUrl = form.dataset.latestUrl;
    let pollTimer = null;

    function showError(message) {
      errorEl.textContent = message;
      errorEl.hidden = false;
    }

    function setProgress(job) {
      progressEl.dataset.jobId = String(job.id);
      document.getElementById('ai-job-state').textContent =
        job.status.charAt(0).toUpperCase() + job.status.slice(1);
      document.getElementById('ai-job-summary').textContent =
        job.processed + ' of ' + job.total + ' processed';
      document.getElementById('ai-job-progress').value = job.progress_percent;
      document.getElementById('ai-job-processed').textContent = job.processed;
      document.getElementById('ai-job-resolved').textContent = job.resolved_by_ai;
      document.getElementById('ai-job-awaiting').textContent = job.awaiting_confirmation;
      document.getElementById('ai-job-transferred').textContent = job.transferred_to_manual;
      document.getElementById('ai-job-failed').textContent = job.failed;
      const active = job.status === 'queued' || job.status === 'running';
      button.disabled = active;
      if (active && pollTimer === null) {
        pollTimer = window.setInterval(poll, 1500);
      } else if (!active && pollTimer !== null) {
        window.clearInterval(pollTimer);
        pollTimer = null;
      }
    }

    function statusUrl(jobId) {
      return statusTemplate.replace('123456789', String(jobId));
    }

    function poll() {
      const jobId = progressEl.dataset.jobId;
      if (!jobId) return;
      fetch(statusUrl(jobId), { headers: { 'X-CSRFToken': window.CSRF_TOKEN || '' } })
        .then(function (response) {
          if (!response.ok) throw new Error('Could not load AI clearing progress.');
          return response.json();
        })
        .then(setProgress)
        .catch(function (err) {
          showError(err.message || 'Could not load AI clearing progress.');
        });
    }

    function discoverLatestJob() {
      fetch(latestUrl, { headers: { 'X-CSRFToken': window.CSRF_TOKEN || '' } })
        .then(function (response) {
          if (!response.ok) throw new Error('Could not load AI clearing progress.');
          return response.json();
        })
        .then(function (data) {
          if (data.job && String(data.job.id) !== progressEl.dataset.jobId) {
            setProgress(data.job);
          }
        })
        .catch(function (err) {
          showError(err.message || 'Could not load AI clearing progress.');
        });
    }

    form.addEventListener('submit', function (event) {
      event.preventDefault();
      errorEl.hidden = true;
      button.disabled = true;
      fetch(form.action, {
        method: 'POST',
        headers: {
          'X-CSRFToken': window.CSRF_TOKEN || '',
          'Accept': 'application/json',
        },
      })
        .then(function (response) {
          return response.json().then(function (data) {
            if (!response.ok) throw new Error(data.error || 'Could not start AI ticket clearing.');
            return data;
          });
        })
        .then(function (data) {
          setProgress(data.job);
        })
        .catch(function (err) {
          button.disabled = false;
          showError(err.message || 'Could not start AI ticket clearing.');
        });
    });

    if (progressEl.dataset.jobId) poll();
    discoverLatestJob();
    window.setInterval(discoverLatestJob, 3000);
  }

  // ---- Notification polling ----
  function initNotificationPoll() {
    const badge = document.getElementById('notif-badge');
    if (!badge) return;

    function poll() {
      fetch('/api/notifications', {
        headers: { 'X-CSRFToken': window.CSRF_TOKEN || '' },
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          const count = data.unread_count || 0;
          badge.textContent = count > 99 ? '99+' : String(count);
          badge.style.display = count > 0 ? 'flex' : 'none';

          // Populate dropdown
          const list = document.getElementById('notif-list');
          if (list && data.notifications) {
            if (data.notifications.length === 0) {
              list.innerHTML = '<div class="notif-item text-muted" style="text-align:center;padding:20px;">No notifications</div>';
            } else {
              list.innerHTML = data.notifications.map(function (n) {
                return '<div class="notif-item ' + (n.is_read ? '' : 'unread') + '" onclick="markNotifRead(' + n.id + ', this)">'
                  + '<div class="notif-item-msg">' + escapeHtml(n.message) + '</div>'
                  + '<div class="notif-item-time">' + (n.created_at ? new Date(n.created_at).toLocaleString() : '') + '</div>'
                  + '</div>';
              }).join('');
            }
          }
        })
        .catch(function () { /* non-critical */ });
    }

    poll();
    setInterval(poll, 60000);
  }

  function escapeHtml(str) {
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
  }

  window.markNotifRead = function (id, el) {
    fetch('/api/notifications/' + id + '/read', {
      method: 'POST',
      headers: { 'X-CSRFToken': window.CSRF_TOKEN || '' },
    });
    if (el) el.classList.remove('unread');
    const badge = document.getElementById('notif-badge');
    if (badge) {
      const current = parseInt(badge.textContent) || 0;
      const newCount = Math.max(0, current - 1);
      badge.textContent = String(newCount);
      if (newCount === 0) badge.style.display = 'none';
    }
  };

  window.markAllNotifRead = function () {
    fetch('/api/notifications/read-all', {
      method: 'POST',
      headers: { 'X-CSRFToken': window.CSRF_TOKEN || '' },
    }).then(function (response) {
      if (!response.ok) return;
      document.querySelectorAll('#notif-list .notif-item.unread').forEach(function (item) {
        item.classList.remove('unread');
      });
      const badge = document.getElementById('notif-badge');
      if (badge) {
        badge.textContent = '0';
        badge.style.display = 'none';
      }
    });
  };

  // ---- Notification dropdown toggle ----
  function initNotifDropdown() {
    const btn = document.getElementById('notif-btn');
    const dropdown = document.getElementById('notif-dropdown');
    if (!btn || !dropdown) return;

    btn.addEventListener('click', function (e) {
      e.stopPropagation();
      dropdown.classList.toggle('open');
    });

    document.addEventListener('click', function () {
      if (dropdown) dropdown.classList.remove('open');
    });

    dropdown.addEventListener('click', function (e) {
      e.stopPropagation();
    });
  }

  // ---- Mobile sidebar toggle ----
  function initSidebar() {
    const hamburger = document.getElementById('hamburger-btn');
    const overlay = document.getElementById('sidebar-overlay');

    if (hamburger) {
      hamburger.addEventListener('click', function () {
        document.body.classList.toggle('sidebar-open');
      });
    }
    if (overlay) {
      overlay.addEventListener('click', function () {
        document.body.classList.remove('sidebar-open');
      });
    }
  }

  // ---- Flash message dismiss ----
  function initFlashDismiss() {
    document.querySelectorAll('.alert-close').forEach(function (btn) {
      btn.addEventListener('click', function () {
        const alert = btn.closest('.alert');
        if (alert) alert.remove();
      });
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    initCharts();
    initAiClearing();
    initNotificationPoll();
    initNotifDropdown();
    initSidebar();
    initFlashDismiss();
    if (window.lucide) window.lucide.createIcons();
  });
})();
