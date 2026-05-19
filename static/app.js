/* global state */
let senders = [];
let currentMode = 'count';
let currentJobId = null;
let pollInterval = null;
let currentDownloadJobId = null;
let lastSummary = null;
let lastOutputPath = null;

/* ── Init ──────────────────────────────────────────────────────────────── */
document.addEventListener('DOMContentLoaded', async () => {
  await loadSenders();
  await checkSession();
  bindEvents();
});

/* ── Session ───────────────────────────────────────────────────────────── */
async function checkSession() {
  const res = await fetch('/api/session').then(r => r.json());
  setSessionUI(res.has_session);
}

function setSessionUI(loggedIn) {
  const dot       = document.getElementById('session-dot');
  const label     = document.getElementById('session-label');
  const loginBtn  = document.getElementById('login-btn');
  const logoutBtn = document.getElementById('logout-btn');

  if (loggedIn) {
    dot.className = 'session-dot online';
    label.textContent = 'Signed in';
    loginBtn.style.display  = 'none';
    logoutBtn.style.display = 'inline-flex';
  } else {
    dot.className = 'session-dot offline';
    label.textContent = 'Not signed in';
    loginBtn.style.display  = 'inline-flex';
    logoutBtn.style.display = 'none';
  }
}

/* ── Senders persistence ───────────────────────────────────────────────── */
async function loadSenders() {
  try {
    const data = await fetch('/api/senders').then(r => r.json());
    senders = Array.isArray(data) ? data : [];
  } catch (_) { senders = []; }
  renderChips();
}

async function saveSenders() {
  await fetch('/api/senders', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(senders),
  });
}

/* ── Chip rendering ────────────────────────────────────────────────────── */
function renderChips() {
  const wrap = document.getElementById('chips-wrap');
  const placeholder = document.getElementById('chips-placeholder');
  wrap.querySelectorAll('.chip').forEach(c => c.remove());
  if (senders.length === 0) {
    placeholder.style.display = '';
  } else {
    placeholder.style.display = 'none';
    senders.forEach((email, idx) => {
      const chip = document.createElement('span');
      chip.className = 'chip';
      chip.innerHTML = `${email} <button class="chip-remove" data-idx="${idx}" title="Remove">×</button>`;
      wrap.appendChild(chip);
    });
  }
}

function addSender(raw) {
  const emails = raw.split(/[\n,]+/).map(s => s.trim().toLowerCase()).filter(Boolean);
  let added = 0;
  emails.forEach(email => {
    if (email && !senders.includes(email)) { senders.push(email); added++; }
  });
  if (added > 0) { renderChips(); saveSenders(); }
}

/* ── Bind events ───────────────────────────────────────────────────────── */
function bindEvents() {
  /* Sender input */
  const input = document.getElementById('sender-input');
  document.getElementById('add-btn').addEventListener('click', () => {
    const val = input.value.trim();
    if (val) { addSender(val); input.value = ''; }
  });
  input.addEventListener('keydown', e => {
    if (e.key === 'Enter') { const val = input.value.trim(); if (val) { addSender(val); input.value = ''; } }
  });
  input.addEventListener('paste', () => {
    setTimeout(() => {
      const val = input.value.trim();
      if (val.includes(',') || val.includes('\n')) { addSender(val); input.value = ''; }
    }, 50);
  });

  /* Remove chip */
  document.getElementById('chips-wrap').addEventListener('click', e => {
    const btn = e.target.closest('.chip-remove');
    if (!btn) return;
    senders.splice(parseInt(btn.dataset.idx, 10), 1);
    renderChips(); saveSenders();
  });

  /* Mode toggle */
  document.querySelectorAll('.mode-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.mode-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      currentMode = btn.dataset.mode;
      document.getElementById('count-panel').classList.toggle('hidden', currentMode !== 'count');
      document.getElementById('months-panel').classList.toggle('hidden', currentMode !== 'months');
    });
  });

  /* Number inc/dec */
  const numInput = document.getElementById('max-emails');
  document.getElementById('inc-btn').addEventListener('click', () => {
    numInput.value = Math.min(9999, (parseInt(numInput.value) || 50) + 10);
  });
  document.getElementById('dec-btn').addEventListener('click', () => {
    numInput.value = Math.max(1, (parseInt(numInput.value) || 50) - 10);
  });

  /* Preset pills */
  document.querySelectorAll('.preset-pill').forEach(pill => {
    pill.addEventListener('click', () => {
      document.querySelectorAll('.preset-pill').forEach(p => p.classList.remove('active'));
      pill.classList.add('active');
      document.getElementById('lookback-months').value = pill.dataset.months;
    });
  });

  /* Login / logout */
  document.getElementById('login-btn').addEventListener('click', startLogin);
  document.getElementById('login-cancel-btn').addEventListener('click', () => {
    document.getElementById('login-overlay').style.display = 'none';
  });
  document.getElementById('logout-btn').addEventListener('click', async () => {
    await fetch('/api/logout', { method: 'POST' });
    setSessionUI(false);
  });

  document.getElementById('quit-app-btn').addEventListener('click', async () => {
    if (!confirm('Quit Outlook Scraper completely? (Closing the browser alone does not stop it.)')) return;
    await fetch('/api/quit', { method: 'POST' });
  });

  /* Run */
  document.getElementById('run-btn').addEventListener('click', startScrape);

  /* Download (below progress) */
  document.getElementById('download-btn').addEventListener('click', () => {
    if (currentDownloadJobId) window.location.href = `/api/download/${currentDownloadJobId}`;
  });

  /* Results panel toggle (header button) */
  document.getElementById('results-toggle-btn').addEventListener('click', () => {
    showResultsPanel(lastSummary, lastOutputPath);
  });

  /* Hide panel */
  document.getElementById('panel-hide-btn').addEventListener('click', hideResultsPanel);

  /* Panel download */
  document.getElementById('panel-download-btn').addEventListener('click', () => {
    if (currentDownloadJobId) window.location.href = `/api/download/${currentDownloadJobId}`;
  });
}

/* ── Login flow ────────────────────────────────────────────────────────── */
async function startLogin() {
  document.getElementById('login-overlay').style.display = 'flex';
  document.getElementById('login-overlay-msg').textContent =
    'A browser window is opening. Sign in to your Outlook account, then come back here.';
  await fetch('/api/login', { method: 'POST' });
  pollLoginStatus();
}

function pollLoginStatus() {
  const interval = setInterval(async () => {
    const res = await fetch('/api/login-status').then(r => r.json());
    document.getElementById('login-overlay-msg').textContent = res.message || '…';
    if (res.status === 'done') {
      clearInterval(interval);
      document.getElementById('login-overlay').style.display = 'none';
      setSessionUI(true);
    } else if (res.status === 'error') {
      clearInterval(interval);
      document.getElementById('login-overlay-msg').textContent =
        '❌ ' + (res.message || 'Login failed. Please try again.');
      setTimeout(() => { document.getElementById('login-overlay').style.display = 'none'; }, 3000);
    }
  }, 1500);
}

/* ── Scrape ────────────────────────────────────────────────────────────── */
async function startScrape() {
  if (senders.length === 0) {
    alert('Please add at least one sender email address.');
    return;
  }

  // Reset UI
  const resultsSection = document.getElementById('results-section');
  const statusLog      = document.getElementById('status-log');
  const downloadWrap   = document.getElementById('download-wrap');
  const runBtn         = document.getElementById('run-btn');
  const progressFill   = document.getElementById('progress-fill');
  const progressPct    = document.getElementById('progress-pct');

  statusLog.innerHTML = '';
  downloadWrap.classList.add('hidden');
  hideResultsPanel();
  document.getElementById('results-toggle-btn').classList.add('hidden');
  resultsSection.classList.remove('hidden');
  runBtn.disabled = true;
  runBtn.classList.add('running');
  document.getElementById('run-label').textContent = 'Scraping…';
  progressFill.style.width = '2%';   // small nudge so user sees it start
  progressPct.textContent = '0%';

  const body = {
    senders,
    mode: currentMode,
    max_emails: parseInt(document.getElementById('max-emails').value) || 50,
    lookback_months: parseInt(document.getElementById('lookback-months').value) || 3,
  };

  let res;
  try {
    res = await fetch('/api/scrape', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(r => r.json());
  } catch (err) {
    addStatusLine('error', '❌', 'Failed to start — server not responding.');
    resetRunBtn();
    return;
  }

  if (res.error) {
    addStatusLine('error', '❌', res.error);
    resetRunBtn();
    return;
  }

  currentJobId = res.job_id;
  currentDownloadJobId = res.job_id;
  addStatusLine('dim', '▶', `Starting search for ${senders.length} sender${senders.length !== 1 ? 's' : ''}…`);

  let seenLogs = 0;
  pollInterval = setInterval(async () => {
    const job = await fetch(`/api/status/${currentJobId}`).then(r => r.json());

    // Render new log lines as clean status entries
    const newLines = (job.logs || []).slice(seenLogs);
    seenLogs += newLines.length;
    newLines.forEach(line => renderStatusLine(line));

    // Progress bar — interpolate smoothly during scraping
    const pct = job.progress || 0;
    progressFill.style.width = pct + '%';
    progressPct.textContent  = pct + '%';

    if (job.status === 'done') {
      clearInterval(pollInterval);
      resetRunBtn();
      progressFill.style.width = '100%';
      progressPct.textContent  = '100%';
      if (job.output_path) downloadWrap.classList.remove('hidden');
      // Show results panel
      lastSummary    = job.summary || [];
      lastOutputPath = job.output_path;
      showResultsPanel(lastSummary, lastOutputPath);
      document.getElementById('results-toggle-btn').classList.remove('hidden');
    } else if (job.status === 'error') {
      clearInterval(pollInterval);
      resetRunBtn();
      addStatusLine('err', '❌', job.error || 'An error occurred.');
      if (job.error && job.error.toLowerCase().includes('session')) await checkSession();
    }
  }, 2500);
}

/* ── Status log helpers ────────────────────────────────────────────────── */
function renderStatusLine(text) {
  // Classify and humanise log lines coming from the scraper
  if (!text || !text.trim()) return;

  const t = text.trim();

  if (t.startsWith('---') || t.includes('Searching:')) {
    // "--- Searching: sender@domain.com ---"
    const match = t.match(/Searching:\s*(.+?)\s*---/i) || t.match(/Searching:\s*(.+)/i);
    const who = match ? match[1] : t.replace(/---/g, '').replace('Searching:', '').trim();
    addStatusLine('dim', '🔍', `Searching inbox of ${who}…`);

  } else if (t.includes('email(s) found') || t.includes('emails found')) {
    addStatusLine('ok', '✅', t.replace(/^\s*[✅✓]\s*/, ''));

  } else if (t.includes('Collected 0')) {
    addStatusLine('warn', '⚠️', `No emails found for ${t.split('from').pop().trim()}`);

  } else if (t.includes('Collected')) {
    addStatusLine('ok', '📬', t.replace(/^\s*/, ''));

  } else if (t.includes('Saved') && t.includes('emails')) {
    addStatusLine('ok', '💾', `Report saved successfully.`);

  } else if (t.startsWith('⚠️') || t.toLowerCase().includes('excluded') || t.toLowerCase().includes('older than')) {
    addStatusLine('warn', '⚠️', t.replace(/^⚠️\s*/, ''));

  } else if (t.startsWith('✅') || t.startsWith('Done')) {
    addStatusLine('ok', '✅', t.replace(/^✅\s*/, ''));

  } else if (t.startsWith('❌') || t.toLowerCase().includes('error') || t.toLowerCase().includes('failed')) {
    addStatusLine('err', '❌', t.replace(/^❌\s*/, ''));

  } else if (t.startsWith('Total per sender') || t.includes('sender_searched') || t.includes('Empty DataFrame')) {
    // Skip internal pandas output
  } else if (/^\s*\[?\d+\]/.test(t)) {
    // "[12] Subject line…" — show as a small email preview
    const subject = t.replace(/^\s*\[\d+\]\s*/, '');
    addStatusLine('dim', '📩', subject);
  } else if (t.length > 1) {
    addStatusLine('dim', '', t);
  }
}

function addStatusLine(cls, icon, text) {
  const log  = document.getElementById('status-log');
  const line = document.createElement('div');
  line.className = `status-line ${cls}`;
  line.innerHTML = icon
    ? `<span class="status-icon">${icon}</span><span class="status-text">${text}</span>`
    : `<span class="status-text">${text}</span>`;
  log.appendChild(line);
  log.scrollTop = log.scrollHeight;
}

/* ── Run button ────────────────────────────────────────────────────────── */
function resetRunBtn() {
  const runBtn = document.getElementById('run-btn');
  runBtn.disabled = false;
  runBtn.classList.remove('running');
  document.getElementById('run-label').textContent = 'Run Scraper';
}

/* ── Results side panel ────────────────────────────────────────────────── */
function showResultsPanel(summary, outputPath) {
  const panel  = document.getElementById('results-panel');
  const dlBtn  = document.getElementById('panel-download-btn');
  const tbody  = document.getElementById('results-tbody');
  const empty  = document.getElementById('panel-empty');
  const table  = document.getElementById('results-table');

  tbody.innerHTML = '';

  const total = (summary || []).reduce((acc, r) => acc + r.count, 0);
  document.getElementById('panel-subtitle').textContent =
    summary && summary.length > 0
      ? `${total} email${total !== 1 ? 's' : ''} across ${summary.length} sender${summary.length !== 1 ? 's' : ''}`
      : 'No results found';

  if (!summary || summary.length === 0) {
    table.style.display = 'none';
    empty.classList.remove('hidden');
  } else {
    table.style.display = '';
    empty.classList.add('hidden');

    summary.forEach(({ sender, count }) => {
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td class="sender-cell" title="${sender}">${sender}</td>
        <td class="col-count"><span class="count-badge">${count}</span></td>
      `;
      tbody.appendChild(tr);
    });
  }

  dlBtn.style.display = outputPath ? 'flex' : 'none';
  panel.classList.remove('hidden');
}

function hideResultsPanel() {
  document.getElementById('results-panel').classList.add('hidden');
}
