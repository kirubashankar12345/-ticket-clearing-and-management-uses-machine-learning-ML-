/**
 * tickets.js — Ticket list filtering, confirmation modals, SLA badges
 */
(function () {
  'use strict';

  // ---- Auto-submit filter dropdowns ----
  document.querySelectorAll('.filter-auto-submit').forEach(function (el) {
    el.addEventListener('change', function () {
      const form = el.closest('form');
      if (form) form.submit();
    });
  });

  // ---- Confirmation modal ----
  const modal = document.getElementById('confirm-modal');
  const modalTitle = document.getElementById('confirm-modal-title');
  const modalMessage = document.getElementById('confirm-modal-message');
  const modalForm = document.getElementById('confirm-modal-form');
  const modalConfirmBtn = document.getElementById('confirm-modal-btn');
  const modalCancelBtn = document.getElementById('confirm-modal-cancel');
  const modalClose = document.getElementById('confirm-modal-close');

  function openModal(title, message, action, extraInputs) {
    if (!modal) return;
    if (modalTitle) modalTitle.textContent = title;
    if (modalMessage) modalMessage.textContent = message;
    if (modalForm) {
      modalForm.action = action;
      // Clear previous extra inputs
      const existing = modalForm.querySelectorAll('.modal-extra-input');
      existing.forEach(function (el) { el.remove(); });
      // Add new extra inputs
      if (extraInputs) {
        Object.entries(extraInputs).forEach(function (pair) {
          const inp = document.createElement('input');
          inp.type = 'hidden';
          inp.name = pair[0];
          inp.value = pair[1];
          inp.className = 'modal-extra-input';
          modalForm.appendChild(inp);
        });
      }
    }
    modal.classList.add('open');
  }

  function closeModal() {
    if (modal) modal.classList.remove('open');
  }

  if (modalCancelBtn) modalCancelBtn.addEventListener('click', closeModal);
  if (modalClose) modalClose.addEventListener('click', closeModal);
  if (modal) {
    modal.addEventListener('click', function (e) {
      if (e.target === modal) closeModal();
    });
  }

  // Expose globally for inline onclick handlers
  window.openConfirmModal = openModal;
  window.closeConfirmModal = closeModal;

  // ---- Assign engineer modal ----
  document.querySelectorAll('[data-action="assign"]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      const ticketId = btn.dataset.ticketId;
      const engineers = JSON.parse(btn.dataset.engineers || '[]');
      const action = btn.dataset.url;

      // Build a select dropdown inside the modal
      let selectHtml = '<div class="form-group" style="margin-top:12px;">'
        + '<label class="form-label">Select Engineer</label>'
        + '<select name="engineer_id" class="form-select modal-extra-input">';

      engineers.forEach(function (eng) {
        selectHtml += '<option value="' + eng.id + '">' + eng.name + '</option>';
      });

      selectHtml += '</select></div>';

      if (modalMessage) modalMessage.innerHTML = 'Assign this ticket to an engineer:' + selectHtml;
      if (modalTitle) modalTitle.textContent = 'Assign Ticket';
      if (modalForm) {
        modalForm.action = action;
        const existing = modalForm.querySelectorAll('.modal-extra-input');
        existing.forEach(function (el) { el.remove(); });
      }
      if (modal) modal.classList.add('open');
    });
  });

  // ---- SLA badge calculation ----
  document.querySelectorAll('[data-sla]').forEach(function (el) {
    const createdAt = el.dataset.sla;
    const status = el.dataset.status || '';
    const slaHours = parseInt(el.dataset.slaHours || '24', 10);

    if (!createdAt || status === 'Resolved' || status === 'Closed') {
      el.textContent = '—';
      return;
    }

    const created = new Date(createdAt);
    const now = new Date();
    const elapsedHours = (now - created) / 3600000;
    const remaining = slaHours - elapsedHours;

    if (remaining <= 0) {
      el.textContent = 'Breached';
      el.className = 'sla-breached';
    } else if (remaining <= slaHours * 0.25) {
      const h = Math.ceil(remaining);
      el.textContent = h + 'h remaining';
      el.className = 'sla-warning';
    } else {
      const h = Math.floor(remaining);
      el.textContent = h + 'h remaining';
      el.className = 'sla-ok';
    }
  });

  // ---- Flash message dismiss (also in dashboard.js for pages not loading dashboard.js) ----
  document.querySelectorAll('.alert-close').forEach(function (btn) {
    btn.addEventListener('click', function () {
      const alert = btn.closest('.alert');
      if (alert) alert.remove();
    });
  });

  // ---- Sidebar toggle (for pages without dashboard.js) ----
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

  if (window.lucide) window.lucide.createIcons();
})();
