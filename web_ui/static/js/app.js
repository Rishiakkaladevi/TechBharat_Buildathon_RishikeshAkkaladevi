/**
 * app.js — MeetingMind Web UI
 * Vanilla JS SPA: page navigation, file upload, SSE pipeline progress,
 * participant mapper, review/execute, results.
 */

'use strict';

// ─────────────────────────────────────────────
// State
// ─────────────────────────────────────────────

const APP = {
  sessionId: null,
  meetingRecord: {},
  actionItems: [],
  speakers: [],
  collaborators: [],
  itemStatuses: {},   // id → 'approved' | 'rejected' | 'pending'
};


// ─────────────────────────────────────────────
// Page navigation
// ─────────────────────────────────────────────

function showPage(id) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  const page = document.getElementById(id);
  if (page) {
    page.classList.add('active');
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }
}


// ─────────────────────────────────────────────
// Page 1 — Upload
// ─────────────────────────────────────────────

(function initUpload() {
  // Set today as default date
  const dateInput = document.getElementById('meeting-date');
  dateInput.value = new Date().toISOString().split('T')[0];

  const dropZone = document.getElementById('drop-zone');
  const fileInput = document.getElementById('file-input');
  const fileName = document.getElementById('drop-filename');
  const processBtn = document.getElementById('process-btn');
  const spinner = document.getElementById('process-spinner');

  let selectedFile = null;

  function setFile(file) {
    selectedFile = file;
    fileName.textContent = `📁 ${file.name}`;
    fileName.style.display = 'block';
    processBtn.disabled = false;
    dropZone.style.borderColor = 'var(--accent-a)';
    dropZone.style.background = 'rgba(99,102,241,0.08)';
  }

  fileInput.addEventListener('change', e => {
    if (e.target.files[0]) setFile(e.target.files[0]);
  });

  dropZone.addEventListener('dragover', e => {
    e.preventDefault();
    dropZone.classList.add('drag-over');
  });
  dropZone.addEventListener('dragleave', () => dropZone.classList.remove('drag-over'));
  dropZone.addEventListener('drop', e => {
    e.preventDefault();
    dropZone.classList.remove('drag-over');
    if (e.dataTransfer.files[0]) setFile(e.dataTransfer.files[0]);
  });

  processBtn.addEventListener('click', async () => {
    if (!selectedFile) return;

    processBtn.disabled = true;
    spinner.style.display = 'inline-block';

    const formData = new FormData();
    formData.append('file', selectedFile);
    formData.append('meeting_date', dateInput.value);
    formData.append('reviewer', document.getElementById('reviewer-name').value || 'user');

    try {
      const res = await fetch('/api/upload', { method: 'POST', body: formData });
      const data = await res.json();
      APP.sessionId = data.session_id;
      showPage('page-processing');
      startPipelineSSE();
    } catch (err) {
      processBtn.disabled = false;
      spinner.style.display = 'none';
      alert('Upload failed: ' + err.message);
    }
  });
})();


// ─────────────────────────────────────────────
// Page 2 — Pipeline SSE
// ─────────────────────────────────────────────

const STEP_KEYS = ['ingest', 'extract', 'resolve', 'review'];

function setStep(key, state) {
  // state: 'active' | 'done' | 'idle'
  const el = document.getElementById(`step-${key}`);
  if (!el) return;
  el.classList.remove('active', 'done');
  if (state !== 'idle') el.classList.add(state);
}

function startPipelineSSE() {
  // reset steps
  STEP_KEYS.forEach(k => setStep(k, 'idle'));

  const evtSource = new EventSource(`/api/pipeline/${APP.sessionId}`);

  evtSource.onmessage = (e) => {
    const msg = JSON.parse(e.data);

    if (msg.type === 'step') {
      const label = document.getElementById('pipeline-current-label');
      if (label) label.textContent = msg.data.label || '';

      const keyMap = {
        1: 'ingest', 2: 'extract', 3: 'resolve', 4: 'review',
      };
      const step = msg.data.step;
      // Mark previous steps done
      for (let i = 1; i < step; i++) {
        if (keyMap[i]) setStep(keyMap[i], 'done');
      }
      if (keyMap[step]) setStep(keyMap[step], 'active');
    }

    if (msg.type === 'complete') {
      STEP_KEYS.forEach(k => setStep(k, 'done'));
      evtSource.close();
      APP.actionItems = msg.data.action_items || [];
      APP.meetingRecord = msg.data.meeting_record || {};
      APP.speakers = msg.data.speakers || [];
      // Initialise all items as pending
      APP.actionItems.forEach(item => {
        APP.itemStatuses[item.id] = 'pending';
      });
      // Advance to mapper
      setTimeout(() => {
        loadCollaborators().then(() => {
          buildMapperPage();
          showPage('page-mapper');
        });
      }, 800);
    }

    if (msg.type === 'error') {
      evtSource.close();
      const errEl = document.getElementById('pipeline-error');
      errEl.textContent = '⚠ ' + (msg.data.message || 'Unknown error');
      errEl.style.display = 'block';
    }

    if (msg.type === 'done') {
      evtSource.close();
    }
  };

  evtSource.onerror = () => {
    evtSource.close();
  };
}


// ─────────────────────────────────────────────
// Page 3 — Participant Mapper
// ─────────────────────────────────────────────

async function loadCollaborators() {
  try {
    const res = await fetch('/api/collaborators');
    const data = await res.json();
    APP.collaborators = data.collaborators || [];
  } catch {
    APP.collaborators = [];
  }
}

function fuzzyMatch(speaker, collabs) {
  if (!collabs.length) return null;
  const sl = speaker.toLowerCase();
  let best = null, bestScore = 0;
  for (const c of collabs) {
    const fields = [c.login, c.name || ''];
    for (const f of fields) {
      const fl = f.toLowerCase();
      // Simple similarity: longest common subsequence ratio
      const score = lcsRatio(sl, fl);
      if (score > bestScore) { bestScore = score; best = c; }
    }
  }
  return bestScore >= 0.45 ? best : null;
}

function lcsRatio(a, b) {
  // Simple LCS-based similarity
  const m = a.length, n = b.length;
  if (!m || !n) return 0;
  const dp = Array.from({ length: m + 1 }, () => new Array(n + 1).fill(0));
  for (let i = 1; i <= m; i++)
    for (let j = 1; j <= n; j++)
      dp[i][j] = a[i - 1] === b[j - 1] ? dp[i - 1][j - 1] + 1 : Math.max(dp[i - 1][j], dp[i][j - 1]);
  return dp[m][n] / Math.max(m, n);
}

function buildMapperPage() {
  const container = document.getElementById('mapper-rows');
  container.innerHTML = '';

  if (!APP.collaborators.length) {
    document.getElementById('mapper-no-github').style.display = 'block';
  }

  const collabOptions = APP.collaborators.map(c =>
    `<option value="${c.login}">${c.name || c.login} (@${c.login})</option>`
  ).join('');

  APP.speakers.forEach(speaker => {
    const matched = fuzzyMatch(speaker, APP.collaborators);
    const initials = speaker.split(/\s+/).map(w => w[0]).join('').slice(0, 2).toUpperCase();

    const row = document.createElement('div');
    row.className = 'mapper-row';
    row.innerHTML = `
      <div class="mapper-speaker">
        <div class="speaker-icon">${initials}</div>
        <div>
          <div>${speaker}</div>
          <span class="mapper-status ${matched ? 'matched' : 'unmatched'}">
            ${matched ? '✓ Auto-matched' : '! No match found'}
          </span>
        </div>
      </div>
      <div>
        <select class="form-select" data-speaker="${speaker}" id="map-select-${encodeURIComponent(speaker)}">
          <option value="">(Not a collaborator)</option>
          ${collabOptions}
        </select>
      </div>
    `;
    container.appendChild(row);

    // Pre-select matched collaborator
    if (matched) {
      const sel = row.querySelector('select');
      sel.value = matched.login;
    }
  });
}

document.getElementById('mapper-confirm-btn').addEventListener('click', async () => {
  const mapping = {};
  document.querySelectorAll('#mapper-rows select').forEach(sel => {
    const speaker = sel.dataset.speaker;
    mapping[speaker] = sel.value || null;
  });

  try {
    await fetch('/api/confirm-mapping', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: APP.sessionId, mapping }),
    });

    // Re-fetch updated action items
    const res = await fetch(`/api/state/${APP.sessionId}`);
    const data = await res.json();
    APP.actionItems = data.action_items || [];
    APP.actionItems.forEach(item => {
      if (!APP.itemStatuses[item.id]) APP.itemStatuses[item.id] = 'pending';
    });
  } catch { /* continue anyway */ }

  buildReviewPage();
  showPage('page-review');
});

document.getElementById('mapper-skip-btn').addEventListener('click', () => {
  buildReviewPage();
  showPage('page-review');
});


// ─────────────────────────────────────────────
// Page 4 — Review & Execute
// ─────────────────────────────────────────────

function buildReviewPage() {
  // Summary
  const mr = APP.meetingRecord;
  document.getElementById('review-summary').textContent = mr.summary || '—';

  // Decisions
  const decisionsEl = document.getElementById('review-decisions');
  decisionsEl.innerHTML = '';
  const decisions = mr.decisions || [];
  if (decisions.length) {
    decisions.forEach(d => {
      const text = typeof d === 'string' ? d : (d.decision || '');
      const el = document.createElement('div');
      el.className = 'decision-item';
      el.textContent = text;
      decisionsEl.appendChild(el);
    });
  } else {
    decisionsEl.innerHTML = '<p style="color:var(--text-muted);font-size:0.85rem;">None recorded.</p>';
  }

  // Risks
  const risksEl = document.getElementById('review-risks');
  risksEl.innerHTML = '';
  const risks = mr.risks || [];
  if (risks.length) {
    risks.forEach(r => {
      const text = typeof r === 'string' ? r : JSON.stringify(r);
      const el = document.createElement('div');
      el.className = 'risk-item';
      el.textContent = text;
      risksEl.appendChild(el);
    });
  } else {
    risksEl.innerHTML = '<p style="color:var(--text-muted);font-size:0.85rem;">None recorded.</p>';
  }

  // Action Items
  const heading = document.getElementById('items-heading');
  heading.textContent = `Action Items (${APP.actionItems.length})`;

  renderActionItems();
}

function renderActionItems() {
  const container = document.getElementById('action-items-container');
  container.innerHTML = '';

  APP.actionItems.forEach((item, idx) => {
    const status = APP.itemStatuses[item.id] || 'pending';
    const priority = (item.priority || 'medium').toLowerCase();
    const owner = (item.resolved_owner?.name) || item.raw_owner || '—';
    const due = item.resolved_date || item.raw_due_date || 'Not set';
    const conf = Math.round((item.confidence || 0) * 100);
    const desc = item.description || '';
    const evidence = item.evidence_quote || '';

    const card = document.createElement('div');
    card.className = `item-card ${status}`;
    card.id = `item-${item.id}`;

    const ghUser = item.resolved_owner?.github_username;
    const ownerDisplay = ghUser ? `${owner} (@${ghUser})` : owner;

    card.innerHTML = `
      <div style="display:flex; justify-content:space-between; align-items:flex-start; gap:12px; margin-bottom:8px;">
        <div class="item-title">${escHtml(item.title || 'Untitled')}</div>
        <div style="display:flex; gap:6px; flex-shrink:0;">
          <span class="badge badge-${priority}">${priority.toUpperCase()}</span>
          <span class="badge badge-${status}" id="badge-${item.id}">${status.toUpperCase()}</span>
        </div>
      </div>
      <div class="item-meta">
        <span>👤 ${escHtml(ownerDisplay)}</span>
        <span>📅 ${escHtml(due)}</span>
      </div>
      ${desc ? `<div class="item-desc">${escHtml(desc)}</div>` : ''}
      <div class="conf-bar-wrap">
        <div class="conf-bar"><div class="conf-fill" style="width:${conf}%"></div></div>
        <div class="conf-label">${conf}% extraction confidence</div>
      </div>
      ${evidence ? `<details><summary class="evidence-block">📝 View transcript evidence</summary>
        <div style="padding:10px 14px; font-size:0.82rem; color:var(--text-muted); font-style:italic;">"${escHtml(evidence)}"</div>
      </details>` : ''}
      <div class="item-controls">
        <button class="btn btn-approve btn-sm ${status === 'approved' ? 'active' : ''}"
                onclick="setItemStatus('${item.id}', 'approved', this)">✓ Approve</button>
        <button class="btn btn-reject btn-sm ${status === 'rejected' ? 'active' : ''}"
                onclick="setItemStatus('${item.id}', 'rejected', this)">✗ Reject</button>
      </div>
    `;
    container.appendChild(card);
  });

  updateTally();
}

function setItemStatus(id, newStatus, clickedBtn) {
  APP.itemStatuses[id] = newStatus;

  const card = document.getElementById(`item-${id}`);
  if (card) {
    card.classList.remove('approved', 'rejected');
    card.classList.add(newStatus);
  }

  const badge = document.getElementById(`badge-${id}`);
  if (badge) {
    badge.className = `badge badge-${newStatus}`;
    badge.textContent = newStatus.toUpperCase();
  }

  // Update button active states
  const controls = card?.querySelector('.item-controls');
  if (controls) {
    controls.querySelectorAll('.btn').forEach(b => b.classList.remove('active'));
    clickedBtn?.classList.add('active');
  }

  updateTally();
}

function updateTally() {
  const statuses = Object.values(APP.itemStatuses);
  const approved = statuses.filter(s => s === 'approved').length;
  const rejected = statuses.filter(s => s === 'rejected').length;
  const pending = statuses.filter(s => s === 'pending').length;

  document.getElementById('tally-approved').textContent = approved;
  document.getElementById('tally-rejected').textContent = rejected;
  document.getElementById('tally-pending').textContent = pending;

  document.getElementById('execute-btn').disabled = approved === 0;
}

document.getElementById('approve-all-btn').addEventListener('click', () => {
  APP.actionItems.forEach(item => {
    APP.itemStatuses[item.id] = 'approved';
  });
  renderActionItems();
});

document.getElementById('reject-all-btn').addEventListener('click', () => {
  APP.actionItems.forEach(item => {
    APP.itemStatuses[item.id] = 'rejected';
  });
  renderActionItems();
});

document.getElementById('execute-btn').addEventListener('click', async () => {
  const btn = document.getElementById('execute-btn');
  const spinner = document.getElementById('execute-spinner');
  const errEl = document.getElementById('execute-error');

  btn.disabled = true;
  spinner.style.display = 'inline-block';
  errEl.style.display = 'none';

  const approved = APP.actionItems.filter(i => APP.itemStatuses[i.id] === 'approved');
  const rejected = APP.actionItems.filter(i => APP.itemStatuses[i.id] === 'rejected');

  try {
    const res = await fetch('/api/execute', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session_id: APP.sessionId,
        approved,
        rejected,
      }),
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || 'Execution failed');
    }

    const data = await res.json();
    buildResultsPage(data.created || [], data.skipped || []);
    showPage('page-results');

  } catch (err) {
    errEl.textContent = '⚠ ' + err.message;
    errEl.style.display = 'block';
    btn.disabled = false;
    spinner.style.display = 'none';
  }
});


// ─────────────────────────────────────────────
// Page 5 — Results
// ─────────────────────────────────────────────

function buildResultsPage(created, skipped) {
  const sub = document.getElementById('results-sub');
  sub.textContent = `${created.length} issue(s) created · ${skipped.length} duplicate(s) skipped`;

  const issuesEl = document.getElementById('results-issues');
  issuesEl.innerHTML = '';

  if (!created.length) {
    issuesEl.innerHTML = '<p style="color:var(--text-muted);font-size:0.85rem;">No issues were created.</p>';
    return;
  }

  created.forEach((issue, i) => {
    const url = issue.task_url || issue.issue_url || '#';
    const title = issue.title || `Issue #${issue.issue_number || i + 1}`;
    const num = issue.task_id || issue.issue_number || '';
    issuesEl.innerHTML += `
      <a class="issue-link-card" href="${url}" target="_blank" rel="noopener">
        <span class="issue-num">#${num}</span>
        <span class="issue-title">${escHtml(title)}</span>
        <span class="issue-arrow">↗</span>
      </a>
    `;
  });
}

document.getElementById('download-report-btn').addEventListener('click', () => {
  if (APP.sessionId) {
    window.location.href = `/api/report/${APP.sessionId}`;
  }
});

document.getElementById('new-meeting-btn').addEventListener('click', () => {
  // Full reset
  APP.sessionId = null;
  APP.meetingRecord = {};
  APP.actionItems = [];
  APP.speakers = [];
  APP.collaborators = [];
  APP.itemStatuses = {};
  showPage('page-upload');
});


// ─────────────────────────────────────────────
// Utility
// ─────────────────────────────────────────────

function escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
