/**
 * chat.js — ResolveAI Chat Interface
 *
 * Uses ask_confirmation flag (not text detection) to show Yes/No buttons.
 * This works for both OpenAI and FallbackChatbot paths.
 */

(function () {
  'use strict';

  const csrfToken = window.CSRF_TOKEN || '';
  const messagesEl = document.getElementById('chat-messages');
  const inputEl = document.getElementById('chat-input');
  const sendBtn = document.getElementById('chat-send-btn');
  const chatContainer = document.getElementById('chat-container');
  const manualForm = document.getElementById('manual-ticket-form');
  const manualSubmit = document.getElementById('manual-ticket-submit');
  const manualError = document.getElementById('manual-ticket-error');

  let conversationId = chatContainer ? (chatContainer.dataset.conversationId || null) : null;
  if (conversationId === '') conversationId = null;

  if (manualForm) {
    manualForm.addEventListener('submit', function (event) {
      event.preventDefault();
      manualError.hidden = true;
      manualSubmit.disabled = true;

      fetch('/api/chat/create-ticket', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': csrfToken,
        },
        body: JSON.stringify({
          support_mode: 'manual',
          issue_description: document.getElementById('manual-issue').value.trim(),
          category: document.getElementById('manual-category').value,
          priority: document.getElementById('manual-priority').value,
        }),
      })
      .then(function (res) {
        return res.json().then(function (data) {
          if (!res.ok) throw new Error(data.message || data.error || 'Could not create the ticket.');
          return data;
        });
      })
      .then(function (data) {
        window.location.href = '/employee/tickets/' + encodeURIComponent(data.ticket_id);
        })
        .catch(function (error) {
          manualError.textContent = error.message || 'Could not create the ticket. Please try again.';
          manualError.hidden = false;
        })
        .finally(function () {
          manualSubmit.disabled = false;
        });
    });
  }

  // ---- Utility ----

  function escapeHtml(str) {
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  /**
   * Simple Markdown-to-HTML converter for AI responses.
   * Handles bold, ordered/unordered lists, blockquotes.
   */
  function renderMarkdown(text) {
    let html = escapeHtml(text);
    // Bold
    html = html.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
    // Inline code
    html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
    // Blockquotes
    html = html.replace(/^&gt;\s*(.+)$/gm, '<blockquote>$1</blockquote>');
    // Ordered list items
    html = html.replace(/^\d+\.\s+(.+)$/gm, '<li>$1</li>');
    // Unordered list items
    html = html.replace(/^[-*]\s+(.+)$/gm, '<li>$1</li>');
    // Wrap consecutive li tags in ol/ul
    html = html.replace(/(<li>.*<\/li>\n?)+/g, function (m) {
      return '<ol>' + m + '</ol>';
    });
    // Line breaks to <br> (but not inside tags)
    html = html.replace(/\n\n/g, '</p><p>').replace(/\n/g, '<br>');
    html = '<p>' + html + '</p>';
    // Clean up empty paragraphs
    html = html.replace(/<p><\/p>/g, '');
    html = html.replace(/<p>(<(?:ol|ul|blockquote))/g, '$1');
    html = html.replace(/(<\/(?:ol|ul|blockquote)>)<\/p>/g, '$1');
    return html;
  }

  function scrollToBottom() {
    if (messagesEl) {
      messagesEl.scrollTop = messagesEl.scrollHeight;
    }
  }

  function timeNow() {
    return new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }

  // ---- Message rendering ----

  function appendUserMessage(text) {
    const wrap = document.createElement('div');
    wrap.className = 'chat-message-wrap user';
    wrap.innerHTML = `
      <div class="chat-bubble chat-bubble-user">${escapeHtml(text)}</div>
      <div class="chat-meta">${timeNow()}</div>
    `;
    messagesEl.appendChild(wrap);
    scrollToBottom();
  }

  function appendAIMessage(content, extras) {
    const wrap = document.createElement('div');
    wrap.className = 'chat-message-wrap ai';

    const bubble = document.createElement('div');
    bubble.className = 'chat-bubble chat-bubble-ai';
    bubble.innerHTML = renderMarkdown(content);

    wrap.appendChild(bubble);

    // Show confirmation buttons if ask_confirmation is true
    if (extras && extras.ask_confirmation === true) {
      const btnRow = document.createElement('div');
      btnRow.className = 'confirmation-buttons';

      const yesBtn = document.createElement('button');
      yesBtn.className = 'btn btn-success btn-sm';
      yesBtn.innerHTML = '<i data-lucide="check"></i> Yes, it\'s fixed';
      yesBtn.onclick = () => sendConfirmation(true, btnRow);

      const noBtn = document.createElement('button');
      noBtn.className = 'btn btn-secondary btn-sm';
      noBtn.innerHTML = '<i data-lucide="x"></i> No, still having issues';
      noBtn.onclick = () => sendConfirmation(false, btnRow);

      btnRow.appendChild(yesBtn);
      btnRow.appendChild(noBtn);
      wrap.appendChild(btnRow);
    }

    // Show "Create Ticket" banner if suggested
    if (extras && extras.suggested_ticket) {
      const banner = document.createElement('div');
      banner.className = 'ticket-banner';
      const label = document.createElement('span');
      label.textContent = 'Would you like to create a support ticket for the IT team?';
      const ticketButton = document.createElement('button');
      ticketButton.className = 'btn btn-primary btn-sm';
      ticketButton.textContent = 'Create Ticket';
      ticketButton.addEventListener('click', function () {
        window.createTicketFromChat(
          ticketButton.dataset.category,
          ticketButton.dataset.priority
        );
      });
      ticketButton.dataset.category = extras.category || 'general';
      ticketButton.dataset.priority = extras.priority || 'Normal';
      banner.appendChild(label);
      banner.appendChild(ticketButton);
      wrap.appendChild(banner);
    }

    const meta = document.createElement('div');
    meta.className = 'chat-meta';
    meta.textContent = 'ResolveAI · ' + timeNow();
    wrap.appendChild(meta);

    messagesEl.appendChild(wrap);
    if (window.lucide) window.lucide.createIcons();
    scrollToBottom();
  }

  function appendErrorMessage(text) {
    const wrap = document.createElement('div');
    wrap.className = 'chat-message-wrap ai';
    wrap.innerHTML = `
      <div class="chat-bubble chat-bubble-ai" style="border-color: var(--danger); color: var(--danger);">
        <strong>Error:</strong> ${escapeHtml(text)}
        <br><br>
        <a href="/employee/tickets" class="btn btn-secondary btn-sm">Create Ticket Manually</a>
      </div>
      <div class="chat-meta">${timeNow()}</div>
    `;
    messagesEl.appendChild(wrap);
    scrollToBottom();
  }

  function showLoading() {
    const el = document.createElement('div');
    el.className = 'chat-loading';
    el.id = 'chat-loading-indicator';
    el.innerHTML = '<span></span><span></span><span></span>';
    messagesEl.appendChild(el);
    scrollToBottom();
    return el;
  }

  function removeLoading() {
    const el = document.getElementById('chat-loading-indicator');
    if (el) el.remove();
  }

  // ---- API calls ----

  function sendMessage() {
    const text = (inputEl.value || '').trim();
    if (!text) return;

    inputEl.value = '';
    inputEl.style.height = 'auto';
    sendBtn.disabled = true;

    appendUserMessage(text);
    showLoading();

    fetch('/api/chat/message', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': csrfToken,
      },
      body: JSON.stringify({
        message: text,
        conversation_id: conversationId,
        active_ticket_id: chatContainer ? chatContainer.dataset.activeTicketId : null,
      }),
    })
      .then(function (res) {
        removeLoading();
        if (res.status === 503) {
          return res.json().then(function (data) {
            appendErrorMessage(data.error || 'AI service is currently unavailable.');
          });
        }
        if (!res.ok) {
          return res.json().then(function (data) {
            appendErrorMessage(data.error || 'An unexpected error occurred.');
          });
        }
        return res.json().then(function (data) {
          conversationId = data.conversation_id;
          if (chatContainer) {
            chatContainer.dataset.lastCategory = data.category || 'general';
            chatContainer.dataset.lastPriority = data.priority || 'Normal';
          }
          appendAIMessage(data.reply, {
            suggested_ticket: data.suggested_ticket,
            ask_confirmation: data.ask_confirmation,
            category: data.category,
            priority: data.priority,
          });
        });
      })
      .catch(function () {
        removeLoading();
        appendErrorMessage('Network error. Please check your connection.');
      })
      .finally(function () {
        sendBtn.disabled = false;
      });
  }

  function sendConfirmation(confirmed, btnRow) {
    if (btnRow) {
      btnRow.querySelectorAll('button').forEach(function (b) { b.disabled = true; });
    }
    inputEl.value = confirmed ? "Yes, it's fixed" : 'No, still having issues';
    sendMessage();
  }

  // Exposed globally so ticket banners can start ticket creation.
  window.createTicketFromChat = function (category, priority) {
    const issueDescription = getIssueDescription();
    if (!issueDescription) {
      window.location.href = '/employee/tickets';
      return;
    }

    fetch('/api/chat/create-ticket', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': csrfToken,
      },
      body: JSON.stringify({
        conversation_id: conversationId,
        issue_description: issueDescription,
        category: category,
        priority: priority,
      }),
    })
      .then(function (res) { return res.json(); })
      .then(function (data) {
        if (data.error) {
          appendErrorMessage(data.message || data.error);
        } else {
          // Update active ticket ID for confirmation
          if (chatContainer) chatContainer.dataset.activeTicketId = data.ticket_id;
          appendAIMessage(
            `✅ Support ticket **[${data.ticket_id}](/employee/tickets/${data.ticket_id})** created successfully. Our IT team will be in touch. You can track progress on the Tickets page.`,
            {}
          );
        }
      })
      .catch(function () {
        appendErrorMessage('Could not create ticket. Please try again.');
      });
  };

  function getIssueDescription() {
    const bubbles = messagesEl.querySelectorAll('.chat-bubble-user');
    const confirmation = /^(?:(?:yes|yeah|yep)(?:[, ]+(?:it's fixed|it is fixed|working now|resolved))?|(?:no|nope)(?:[, ]+(?:still having issues|not fixed|not yet|still broken))?)[.! ]*$/i;
    for (let i = bubbles.length - 1; i >= 0; i -= 1) {
      const text = bubbles[i].textContent.trim();
      if (text && !confirmation.test(text)) return text.slice(0, 2000);
    }
    return null;
  }

  // ---- Suggestion chips ----
  document.querySelectorAll('.suggestion-chip').forEach(function (chip) {
    chip.addEventListener('click', function () {
      inputEl.value = chip.dataset.text || chip.textContent;
      inputEl.focus();
      sendMessage();
    });
  });

  // ---- Input event handlers ----
  if (inputEl) {
    inputEl.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
      }
    });

    inputEl.addEventListener('input', function () {
      this.style.height = 'auto';
      this.style.height = Math.min(this.scrollHeight, 120) + 'px';
    });
  }

  if (sendBtn) {
    sendBtn.addEventListener('click', sendMessage);
  }

  // Initial scroll to bottom
  scrollToBottom();
})();
