// NeuroLens dashboard. Chart + playhead code is carried over from the prototype;
// account, upload (direct to S3), polling, history and billing are new.

let analysisData = null, charts = [], samplesData = null, me = null, appConfig = null;
let currentJobId = null, pollTimer = null;
const $ = id => document.getElementById(id);
const video = $('videoPlayer');
const POLL_MS = 5000;

const STAGE_TEXT = {
  DOWNLOADING: 'Fetching your video',
  VALIDATING: 'Checking length and format',
  PASS_FULL: 'Predicting brain response (with audio)',
  PASS_SILENT: 'Silent baseline (audio removed)',
  ROI: 'Extracting brain regions',
  WRITING: 'Saving results',
};

// ─── Small helpers ──────────────────────────────────────────────────────────
function esc(s) { return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

let toastTimer = null;
function toast(msg, isError = false) {
  const t = $('toast');
  t.textContent = msg; t.className = 'toast' + (isError ? ' error' : ''); t.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.hidden = true; }, 6000);
}

async function api(path, opts = {}) {
  const res = await fetch(path, { credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, ...opts });
  const body = res.headers.get('content-type')?.includes('application/json') ? await res.json() : null;
  if (!res.ok) { const e = new Error(body?.error?.message || `Request failed (${res.status})`); e.status = res.status; e.body = body; throw e; }
  return body;
}

// ─── Chart.js setup ─────────────────────────────────────────────────────────
Chart.defaults.color = '#94A3B8';
Chart.defaults.borderColor = 'rgba(15,22,41,0.06)';
Chart.defaults.font.family = "'Plus Jakarta Sans', sans-serif";
Chart.defaults.font.size = 11;
Chart.defaults.animation.duration = 600;

const playheadPlugin = {
  id: 'playhead',
  afterDraw(chart) {
    if (!analysisData || !video.duration) return;
    const x = chart.scales.x.getPixelForValue(video.currentTime);
    if (x < chart.scales.x.left || x > chart.scales.x.right) return;
    const ctx = chart.ctx;
    ctx.save();
    ctx.beginPath(); ctx.moveTo(x, chart.chartArea.top); ctx.lineTo(x, chart.chartArea.bottom);
    ctx.strokeStyle = 'rgba(15,22,41,0.3)'; ctx.lineWidth = 1.5; ctx.setLineDash([4,3]); ctx.stroke();
    ctx.fillStyle = '#0F1629'; ctx.font = "500 10px 'JetBrains Mono', monospace"; ctx.textAlign = 'center';
    ctx.fillText(Math.floor(video.currentTime) + 's', x, chart.chartArea.top - 4);
    ctx.restore();
  }
};
Chart.register(playheadPlugin);

// ─── Account ────────────────────────────────────────────────────────────────
async function loadAccount() {
  try { appConfig = await api('/api/config'); } catch { appConfig = {}; }
  if (appConfig.max_video_seconds) $('uploadSubtitle').textContent = `MP4, MOV, or WebM — max ${appConfig.max_video_seconds} seconds`;
  try {
    me = await api('/api/me');
  } catch (e) {
    me = null;
  }
  renderAccount();
}

function renderAccount() {
  const signedIn = !!me;
  $('signedIn').hidden = !signedIn;
  $('signedOut').hidden = signedIn;
  $('signinHint').hidden = signedIn;
  $('googleLogin').hidden = !appConfig?.google_login;
  $('devLoginForm').hidden = !appConfig?.dev_login;
  $('btnBuy').hidden = !appConfig?.billing;
  if (signedIn) {
    $('creditsPill').textContent = `${me.user.credits_available} credits`;
    if (me.user.avatar_url) { $('avatar').src = me.user.avatar_url; $('avatar').hidden = false; }
    if (!currentJobId && !analysisData) $('uploadZone').classList.remove('hidden');
    $('headerMeta').textContent = 'Upload a video or select a sample';
  } else {
    $('uploadZone').classList.add('hidden');
  }
}

async function refreshCredits() {
  try { me = await api('/api/me'); $('creditsPill').textContent = `${me.user.credits_available} credits`; } catch { /* signed out */ }
}

$('devLoginForm').addEventListener('submit', async ev => {
  ev.preventDefault();
  try { await api('/auth/dev-login', { method: 'POST', body: JSON.stringify({ email: $('devEmail').value }) }); await loadAccount(); }
  catch (e) { toast(e.message, true); }
});
$('btnLogout').addEventListener('click', async () => { await api('/auth/logout', { method: 'POST' }); me = null; resetUI(); renderAccount(); });

// ─── Samples ────────────────────────────────────────────────────────────────
async function loadSamples() {
  try {
    samplesData = await api('/api/samples');
    renderSamples();
  } catch (e) {
    console.warn('Could not load samples:', e);
  }
}

function renderSamples() {
  const grid = $('samplesGrid');
  if (!samplesData || !samplesData.samples || samplesData.samples.length === 0) {
    $('samplesSection').style.display = 'none';
    return;
  }
  grid.innerHTML = '';
  samplesData.samples.forEach((s, idx) => {
    const card = document.createElement('div');
    card.className = 'sample-card';
    card.dataset.idx = idx;
    card.tabIndex = 0;
    card.innerHTML = `
      <div class="sample-thumb">
        ${s.thumbnail_url ? `<img src="${esc(s.thumbnail_url)}" alt="" onerror="this.parentElement.innerHTML='<div class=sample-thumb-placeholder>▶</div>'">` : '<div class="sample-thumb-placeholder">▶</div>'}
      </div>
      <div class="sample-info">
        <div class="sample-title">${esc(s.title)}</div>
        <div class="sample-dur">${esc(s.duration_seconds)}s</div>
      </div>`;
    card.addEventListener('click', () => loadSample(idx));
    card.addEventListener('keydown', ev => { if (ev.key === 'Enter') loadSample(idx); });
    grid.appendChild(card);
  });
}

function loadSample(idx) {
  const s = samplesData.samples[idx];
  document.querySelectorAll('.sample-card').forEach(c => c.classList.remove('active'));
  document.querySelector(`.sample-card[data-idx="${idx}"]`).classList.add('active');
  showResult({ filename: s.video_file, duration_seconds: s.duration_seconds, timesteps: s.timesteps },
             s.video_url, s.title, null);
}

// ─── Upload: reserve credits → direct S3 upload (GPU warms up in parallel) → poll ──
const uploadZone = $('uploadZone'), fileInput = $('fileInput');
['dragenter','dragover'].forEach(e => uploadZone.addEventListener(e, ev => { ev.preventDefault(); uploadZone.classList.add('dragover'); }));
['dragleave','drop'].forEach(e => uploadZone.addEventListener(e, ev => { ev.preventDefault(); uploadZone.classList.remove('dragover'); }));
uploadZone.addEventListener('drop', ev => { const f = ev.dataTransfer.files; if (f.length && f[0].type.startsWith('video/')) handleFile(f[0]); });
fileInput.addEventListener('change', ev => { if (ev.target.files.length) handleFile(ev.target.files[0]); });
$('btnNew').addEventListener('click', resetUI);

function readDuration(file) {
  return new Promise((resolve, reject) => {
    const v = document.createElement('video');
    v.preload = 'metadata';
    v.onloadedmetadata = () => { URL.revokeObjectURL(v.src); resolve(v.duration); };
    v.onerror = () => reject(new Error('This file could not be read as a video'));
    v.src = URL.createObjectURL(file);
  });
}

async function handleFile(file) {
  const pricing = me?.pricing || {};
  if (pricing.allowed_types && !pricing.allowed_types.includes(file.type)) return toast('Upload an MP4, MOV or WebM video', true);
  if (pricing.max_upload_bytes && file.size > pricing.max_upload_bytes) return toast(`Videos must be under ${Math.round(pricing.max_upload_bytes / 1048576)} MB`, true);
  let duration;
  try { duration = await readDuration(file); } catch (e) { return toast(e.message, true); }
  if (pricing.max_video_seconds && duration > pricing.max_video_seconds) return toast(`This video is ${Math.round(duration)} s; the limit is ${pricing.max_video_seconds} s`, true);

  let job;
  try {
    job = await api('/api/jobs', { method: 'POST', body: JSON.stringify({
      filename: file.name, content_type: file.type, size_bytes: file.size, est_duration_s: duration }) });
  } catch (e) {
    if (e.status === 402 && appConfig?.billing) { toast(e.message, true); openBuy(); } else toast(e.message, true);
    fileInput.value = '';
    return;
  }

  currentJobId = job.job_id;
  $('creditsPill').textContent = `${job.credits_available} credits`;
  showProcessing(file.name);
  $('processingCost').textContent = `${job.credits_reserved} credits reserved — unused credits are returned`;
  $('btnCancel').hidden = false;
  setGpuChip(job.capacity, job.prewarm);
  const localUrl = URL.createObjectURL(file);
  video.src = localUrl;
  startProcView(localUrl, duration);

  try {
    await uploadToS3(job.upload, file, pct => { $('uploadBar').style.width = pct + '%'; });
  } catch (e) {
    toast('Upload failed: ' + e.message, true);
    api(`/api/jobs/${currentJobId}`, { method: 'DELETE' }).catch(() => {});
    refreshCredits();
    return resetUI();
  }
  $('btnCancel').hidden = true;
  $('uploadBar').style.width = '100%';
  $('processingText').textContent = 'Uploaded — queued for analysis';
  setProcStage('QUEUED');
  api(`/api/jobs/${currentJobId}/uploaded`, { method: 'POST' }).catch(() => {});
  poll();
}

function uploadToS3(upload, file, onProgress) {
  return new Promise((resolve, reject) => {
    const fd = new FormData();
    Object.entries(upload.fields).forEach(([k, v]) => fd.append(k, v));
    fd.append('file', file);                              // must be the last field
    const xhr = new XMLHttpRequest();
    xhr.open('POST', upload.url);
    xhr.upload.onprogress = ev => { if (ev.lengthComputable) onProgress(Math.round(100 * ev.loaded / ev.total)); };
    xhr.onload = () => (xhr.status >= 200 && xhr.status < 300) ? resolve() : reject(new Error(`storage returned ${xhr.status}`));
    xhr.onerror = () => reject(new Error('network error'));
    xhr.send(fd);
  });
}

$('btnCancel').addEventListener('click', async () => {
  if (!currentJobId) return;
  try { await api(`/api/jobs/${currentJobId}`, { method: 'DELETE' }); toast('Cancelled — credits returned'); }
  catch (e) { toast(e.message, true); }
  refreshCredits();
  resetUI();
});

function setGpuChip(cap, prewarm) {
  const chip = $('gpuChip');
  if (!cap) { chip.className = 'chip'; chip.textContent = 'GPU: —'; return; }
  if (cap.workers_ready > 0) { chip.className = 'chip ready'; chip.textContent = 'GPU ready'; }
  else if (cap.workers_starting > 0 || (prewarm || '').startsWith('scaled')) { chip.className = 'chip starting'; chip.textContent = 'GPU starting up — usually 1–3 min'; }
  else { chip.className = 'chip'; chip.textContent = 'Waiting for a GPU'; }
}

async function poll() {
  clearTimeout(pollTimer);
  if (!currentJobId) return;
  let s;
  try { s = await api(`/api/jobs/${currentJobId}`); }
  catch (e) { pollTimer = setTimeout(poll, POLL_MS); return; }

  setGpuChip(s.capacity);
  if (s.status === 'SUCCEEDED') return openJob(currentJobId);
  if (['FAILED', 'EXPIRED', 'CANCELLED'].includes(s.status)) {
    toast(`Analysis did not complete: ${s.error?.message || s.status}. Your credits were returned.`, true);
    refreshCredits();
    return resetUI();
  }
  if (s.status === 'PROCESSING') {
    setProcStage(s.progress_stage);
    $('processingText').textContent = STAGE_TEXT[s.progress_stage] || 'Analysing…';
    $('analysisBar').style.width = (s.progress_pct || 0) + '%';
    $('processingDetail').textContent = s.attempts > 1 ? `Retry ${s.attempts - 1} — a GPU was replaced` : '';
  } else {
    setProcStage('QUEUED');
    $('processingText').textContent = 'Queued for analysis';
    $('processingDetail').textContent = s.queue_position ? `${s.queue_position} video(s) ahead of yours` : '';
  }
  pollTimer = setTimeout(poll, POLL_MS);
}

async function openJob(jobId) {
  clearTimeout(pollTimer);
  stopProcView();
  try {
    const r = await api(`/api/jobs/${jobId}/result`);
    currentJobId = jobId;
    const t0 = performance.now();
    showResult(r.result, r.video_url, r.result.filename, r.result.processing);
    requestAnimationFrame(() => {
      const ms = Math.round(performance.now() - t0);
      api(`/api/jobs/${jobId}/metrics`, { method: 'POST', body: JSON.stringify({ client_render_ms: ms }) }).catch(() => {});
    });
    $('btnCsv').href = `/api/jobs/${jobId}/export.csv`;
    $('btnCsv').hidden = false;
    startReport(jobId);
    refreshCredits();
  } catch (e) { toast(e.message, true); }
}

// ─── Processing view: the video scrubs along with the analysis ──────────────
// Each model pass watches the whole video once (pass 1 with audio, pass 2 silent), so the preview
// sweeps from start to end per pass. The server reports stages, not frames; within a pass the playhead
// advances on an estimate from local benchmarks (~5 s of GPU per video-second) and waits near the end
// until the next stage arrives.
const procVideo = $('procVideo');
const GPU_SECONDS_PER_VIDEO_SECOND = 5;
const PASS_LABEL = { PASS_FULL: 'Pass 1 of 2 · with audio', PASS_SILENT: 'Pass 2 of 2 · audio removed' };
const TIPS = {
  waiting: [
    'Waking a GPU for your video…',
    'Loading the TRIBE v2 brain model…',
    'Your video is safely uploaded. Hang tight.',
  ],
  PASS_FULL: [
    'Looking for faces (fusiform face area)…',
    'Tracking bodies, hands and gestures…',
    'Reading the scenes and settings…',
    'Transcribing dialogue and listening to the soundtrack…',
    'Predicting responses at 20,484 points across the cortex…',
    'Following gaze and social cues…',
  ],
  PASS_SILENT: [
    'Muting the soundtrack for a silent comparison…',
    'Re-watching your ad without sound…',
    'Measuring what the audio adds, second by second…',
  ],
  finishing: [
    'Summarising five brain regions…',
    'Finding peaks and drop-off moments…',
    'Building your charts…',
  ],
};
const proc = { stage: null, stageStart: 0, duration: 0, ticker: null, tipTimer: null, tipSet: null, tipIdx: 0 };

function startProcView(objectUrl, duration) {
  stopProcView();
  proc.duration = duration || 0;
  procVideo.src = objectUrl;
  procVideo.currentTime = 0;
  proc.stage = 'UPLOADING';
  proc.stageStart = performance.now();
  setTips('waiting');
  proc.ticker = setInterval(renderProcFrame, 250);
  renderProcFrame();
}

function stopProcView() {
  clearInterval(proc.ticker); clearInterval(proc.tipTimer);
  proc.ticker = proc.tipTimer = null; proc.tipSet = null; proc.stage = null;
}

function setProcStage(stage) {
  if (stage === proc.stage) return;
  proc.stage = stage;
  proc.stageStart = performance.now();
  setTips(stage in PASS_LABEL ? stage : (stage === 'ROI' || stage === 'WRITING') ? 'finishing' : 'waiting');
}

function setTips(set) {
  if (set === proc.tipSet) return;
  proc.tipSet = set; proc.tipIdx = 0;
  clearInterval(proc.tipTimer);
  showTip(TIPS[set][0]);
  proc.tipTimer = setInterval(() => {
    proc.tipIdx = (proc.tipIdx + 1) % TIPS[set].length;
    showTip(TIPS[set][proc.tipIdx]);
  }, 3500);
}

function showTip(text) {
  const el = $('procTip');
  el.classList.add('fading');
  setTimeout(() => { el.textContent = text; el.classList.remove('fading'); }, 350);
}

function passFraction() {
  if (proc.stage in PASS_LABEL) {
    const expectedMs = Math.max(15, proc.duration * GPU_SECONDS_PER_VIDEO_SECOND) * 1000;
    return Math.min(0.97, (performance.now() - proc.stageStart) / expectedMs);
  }
  return (proc.stage === 'ROI' || proc.stage === 'WRITING') ? 1 : 0;
}

function renderProcFrame() {
  const dur = proc.duration || procVideo.duration || 0;
  const frac = passFraction();
  const inPass = proc.stage in PASS_LABEL;
  const t = frac * dur;
  if (dur && Math.abs(procVideo.currentTime - t) > 0.2) procVideo.currentTime = Math.min(t, Math.max(0, dur - 0.05));
  $('procShade').style.width = `${(1 - frac) * 100}%`;
  $('procScan').hidden = !inPass;
  $('procScan').style.left = `calc(${frac * 100}% - 1px)`;
  $('procPass').textContent = PASS_LABEL[proc.stage] || (frac >= 1 ? 'Analysed' : proc.stage === 'UPLOADING' ? 'Uploading' : 'Queued');
  $('procTime').textContent = dur ? `${t.toFixed(1)}s / ${dur.toFixed(1)}s` : '';
}

// ─── AI report tab (step after TRIBE: LLM summary → PDF) ─────────────────────
const REPORT_TIPS = [
  'Picking the key moments from your brain-response curves…',
  'Grabbing frames from those moments…',
  'Reading what changes on screen at each peak and dip…',
  'Writing what works and what to improve…',
  'Laying out your PDF…',
];
const report = { timer: null, tipTimer: null, tipIdx: 0, loadedFor: null };

function showTab(name) {
  const isReport = name === 'report';
  $('chartsPane').hidden = isReport;
  $('reportPane').hidden = !isReport;
  $('tabCharts').classList.toggle('active', !isReport);
  $('tabReport').classList.toggle('active', isReport);
  $('tabCharts').setAttribute('aria-selected', String(!isReport));
  $('tabReport').setAttribute('aria-selected', String(isReport));
  if (!isReport) charts.forEach(c => c.resize());          // charts were sized while hidden
}
$('tabCharts').addEventListener('click', () => showTab('charts'));
$('tabReport').addEventListener('click', () => showTab('report'));

function resetReport() {
  clearTimeout(report.timer); clearInterval(report.tipTimer);
  report.loadedFor = null;
  $('resultTabs').hidden = true;
  $('reportFrame').removeAttribute('src');
  showTab('charts');
}

function startReport(jobId) {
  resetReport();
  $('resultTabs').hidden = false;
  pollReport(jobId);
}

async function pollReport(jobId) {
  clearTimeout(report.timer);
  if (jobId !== currentJobId) return;
  let s;
  try { s = await api(`/api/jobs/${jobId}`); }
  catch { report.timer = setTimeout(() => pollReport(jobId), POLL_MS); return; }
  const r = s.report || {};
  if (r.status === 'READY') return reportReady(jobId, r.model);
  if (r.status === 'FAILED') return reportFailed(r.error);
  if (r.status !== 'PENDING') { $('resultTabs').hidden = true; return; }   // OFF / older jobs: no report
  reportWaiting();
  report.timer = setTimeout(() => pollReport(jobId), POLL_MS);
}

function reportWaiting() {
  $('reportWait').hidden = false; $('reportReady').hidden = true; $('reportFailed').hidden = true;
  $('reportBadge').textContent = '· writing…'; $('reportBadge').className = 'tab-badge';
  if (!report.tipTimer) {
    $('reportTip').textContent = REPORT_TIPS[0];
    report.tipTimer = setInterval(() => {
      report.tipIdx = (report.tipIdx + 1) % REPORT_TIPS.length;
      $('reportTip').textContent = REPORT_TIPS[report.tipIdx];
    }, 3500);
  }
}

function reportReady(jobId, model) {
  clearInterval(report.tipTimer); report.tipTimer = null;
  const url = `/api/jobs/${jobId}/report.pdf`;
  $('reportWait').hidden = true; $('reportFailed').hidden = true; $('reportReady').hidden = false;
  if (report.loadedFor !== jobId) { $('reportFrame').src = url; report.loadedFor = jobId; }
  $('reportOpen').href = url;
  $('reportDownload').href = `${url}?download=1`;
  $('reportModel').textContent = model ? `Written by ${model} from the TRIBE v2 results` : '';
  $('reportBadge').textContent = '· ready'; $('reportBadge').className = 'tab-badge ready';
}

function reportFailed(message) {
  clearInterval(report.tipTimer); report.tipTimer = null;
  $('reportWait').hidden = true; $('reportReady').hidden = true; $('reportFailed').hidden = false;
  $('reportFailed').textContent = `The AI report could not be written: ${message || 'unknown error'}. ` +
    'Your charts and CSV export are unaffected.';
  $('reportBadge').textContent = '· unavailable'; $('reportBadge').className = 'tab-badge';
}

// ─── Views ──────────────────────────────────────────────────────────────────
function showProcessing(name) {
  $('uploadZone').classList.add('hidden');
  $('samplesSection').style.display = 'none';
  $('videoContainer').classList.remove('active');
  $('processing').classList.add('active');
  $('uploadBar').style.width = '0%';
  $('analysisBar').style.width = '0%';
  $('processingText').textContent = 'Uploading…';
  $('processingDetail').textContent = '';
  $('headerMeta').textContent = `Processing: ${name}`;
}

function showResult(data, videoUrl, title, processing) {
  resetReport();
  analysisData = data;
  video.src = videoUrl || '';
  $('videoFilename').textContent = data.filename;
  $('videoDuration').textContent = `${data.duration_seconds}s`;
  $('videoProcessTime').textContent = processing ? `GPU ${(processing.pass_full_s + processing.pass_silent_s).toFixed(0)}s` : '';
  $('uploadZone').classList.add('hidden');
  $('processing').classList.remove('active');
  $('videoContainer').classList.add('active');
  $('chartsEmpty').style.display = 'none';
  $('chartsContainer').classList.add('active');
  $('btnCsv').hidden = true;
  $('headerMeta').textContent = `${title} — ${data.duration_seconds}s`;
  renderStats();
  buildCharts();
}

function renderStats() {
  const ts = analysisData.timesteps;
  const eng = ts.map(t => t.engagement_overall);
  const peak = eng.indexOf(Math.max(...eng));
  const avg = (eng.reduce((a,b) => a+b, 0) / eng.length).toFixed(2);
  $('resultStats').innerHTML = `
    <div class="stat-card"><div class="stat-value">${analysisData.duration_seconds}s</div><div class="stat-label">Duration</div></div>
    <div class="stat-card"><div class="stat-value">${avg}</div><div class="stat-label">Avg Engagement</div></div>
    <div class="stat-card"><div class="stat-value">${peak}s</div><div class="stat-label">Peak Moment</div></div>`;
}

// ─── Charts ─────────────────────────────────────────────────────────────────
function buildCharts() {
  charts.forEach(c => c.destroy()); charts = [];
  const ts = analysisData.timesteps, labels = ts.map(t => t.t);
  function peakInfo(d) { const m = Math.max(...d); return `peak ${m.toFixed(2)} @ ${d.indexOf(m)}s`; }

  function make(id, data, color, fill = true) {
    const ctx = $(id).getContext('2d');
    const g = ctx.createLinearGradient(0, 0, 0, ctx.canvas.parentElement.offsetHeight);
    g.addColorStop(0, color + '20'); g.addColorStop(1, color + '02');
    const c = new Chart(ctx, {
      type: 'line',
      data: { labels, datasets: [{ data, borderColor: color, backgroundColor: fill ? g : 'transparent', fill, borderWidth: 2, pointRadius: 0, pointHoverRadius: 4, pointHoverBackgroundColor: color, pointHoverBorderColor: '#FFF', pointHoverBorderWidth: 2, tension: 0.35 }] },
      options: {
        responsive: true, maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        onClick: (e) => { const x = c.scales.x.getValueForPixel(e.x); if (x >= 0 && x < analysisData.duration_seconds) video.currentTime = x; },
        plugins: { legend: { display: false }, tooltip: { backgroundColor: '#0F1629', titleColor: '#E2E8F0', bodyColor: '#FFF', borderColor: '#1E293B', borderWidth: 1, cornerRadius: 8, padding: 10, titleFont: { family: "'JetBrains Mono', monospace", size: 11 }, bodyFont: { family: "'JetBrains Mono', monospace", size: 12, weight: '500' }, callbacks: { title: i => `t = ${i[0].label}s`, label: i => ` ${i.parsed.y.toFixed(3)}` } } },
        scales: { x: { grid: { display: false }, ticks: { maxTicksLimit: 15, font: { family: "'JetBrains Mono', monospace", size: 10 }, color: '#94A3B8' } }, y: { min: 0, max: 1.05, grid: { color: 'rgba(15,22,41,0.04)' }, ticks: { display: false } } }
      }
    });
    charts.push(c); return c;
  }

  make('chartOverall', ts.map(t => t.engagement_overall), '#EA580C'); $('peakOverall').textContent = peakInfo(ts.map(t => t.engagement_overall));
  make('chartFfa', ts.map(t => t.regions.ffa_faces), '#DC2626'); $('peakFfa').textContent = peakInfo(ts.map(t => t.regions.ffa_faces));
  make('chartEba', ts.map(t => t.regions.eba_bodies), '#2563EB'); $('peakEba').textContent = peakInfo(ts.map(t => t.regions.eba_bodies));
  make('chartPpa', ts.map(t => t.regions.ppa_scenes), '#16A34A'); $('peakPpa').textContent = peakInfo(ts.map(t => t.regions.ppa_scenes));
  make('chartSts', ts.map(t => t.regions.sts_social), '#D97706'); $('peakSts').textContent = peakInfo(ts.map(t => t.regions.sts_social));

  const awD = ts.map(t => t.auditory_with_audio).filter(v => v !== null);
  const aoD = ts.map(t => t.auditory_without_audio).filter(v => v !== null);
  const aL = labels.slice(0, awD.length);
  const actx = $('chartAuditory').getContext('2d');
  const ag = actx.createLinearGradient(0, 0, 0, 150); ag.addColorStop(0, '#9333EA18'); ag.addColorStop(1, '#9333EA02');
  const ac = new Chart(actx, {
    type: 'line',
    data: { labels: aL, datasets: [
      { label: 'With audio', data: awD, borderColor: '#9333EA', backgroundColor: ag, fill: '+1', borderWidth: 2, pointRadius: 0, pointHoverRadius: 4, tension: 0.35 },
      { label: 'Without audio', data: aoD, borderColor: '#9333EA50', borderDash: [5,4], backgroundColor: 'transparent', fill: false, borderWidth: 1.5, pointRadius: 0, pointHoverRadius: 4, tension: 0.35 }
    ] },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      onClick: (e) => { const x = ac.scales.x.getValueForPixel(e.x); if (x >= 0 && x < analysisData.duration_seconds) video.currentTime = x; },
      plugins: { legend: { display: true, position: 'top', align: 'end', labels: { boxWidth: 14, boxHeight: 2, padding: 12, color: '#475569', font: { size: 11, family: "'Plus Jakarta Sans', sans-serif" } } }, tooltip: { backgroundColor: '#0F1629', titleColor: '#E2E8F0', bodyColor: '#FFF', cornerRadius: 8, titleFont: { family: "'JetBrains Mono', monospace", size: 11 }, bodyFont: { family: "'JetBrains Mono', monospace", size: 11 }, callbacks: { title: i => `t = ${i[0].label}s` } } },
      scales: { x: { grid: { display: false }, ticks: { maxTicksLimit: 15, font: { family: "'JetBrains Mono', monospace", size: 10 }, color: '#94A3B8' } }, y: { min: 0, max: 1.05, grid: { color: 'rgba(15,22,41,0.04)' }, ticks: { display: false } } }
    }
  });
  charts.push(ac); $('peakAud').textContent = peakInfo(awD);
}

// ─── Video sync ─────────────────────────────────────────────────────────────
let rafId = null;
function syncPlayhead() { charts.forEach(c => c.update('none')); if (!video.paused) rafId = requestAnimationFrame(syncPlayhead); }
video.addEventListener('play', () => { rafId = requestAnimationFrame(syncPlayhead); });
video.addEventListener('pause', () => { cancelAnimationFrame(rafId); charts.forEach(c => c.update('none')); });
video.addEventListener('seeked', () => { charts.forEach(c => c.update('none')); });

// ─── Dialogs: buy credits, history ──────────────────────────────────────────
document.querySelectorAll('dialog [data-close]').forEach(b => b.addEventListener('click', () => b.closest('dialog').close()));

async function openBuy() {
  const list = $('packList');
  list.innerHTML = '<div class="hist-meta">Loading…</div>';
  $('buyDialog').showModal();
  try {
    const { packs, subscription } = await api('/api/billing/packs');
    list.innerHTML = '';
    packs.forEach(p => {
      const row = document.createElement('div');
      row.className = 'pack';
      row.innerHTML = `<div><div class="pack-name">${p.credits} credits</div>
        <div class="pack-meta">≈ ${Math.floor(p.credits / 30)} × 30 s ads · $${(p.usd_cents / 100).toFixed(2)}</div></div>`;
      const btn = document.createElement('button');
      btn.className = 'btn-primary'; btn.type = 'button'; btn.textContent = `Buy for $${(p.usd_cents / 100).toFixed(2)}`;
      btn.addEventListener('click', () => checkout('/api/billing/checkout', { pack_id: p.id }, btn));
      row.appendChild(btn); list.appendChild(row);
    });
    if (subscription.available) {
      const row = document.createElement('div');
      row.className = 'pack';
      row.innerHTML = `<div><div class="pack-name">Monthly plan</div><div class="pack-meta">${subscription.monthly_credits} credits every month</div></div>`;
      const btn = document.createElement('button');
      btn.className = 'btn-primary'; btn.type = 'button'; btn.textContent = 'Subscribe';
      btn.addEventListener('click', () => checkout('/api/billing/subscribe', {}, btn));
      row.appendChild(btn); list.appendChild(row);
    }
    const note = document.createElement('div');
    note.className = 'hist-meta';
    note.textContent = 'Payments are handled by Stripe Checkout (test mode). Card details never reach NeuroLens.';
    list.appendChild(note);
  } catch (e) { list.innerHTML = `<div class="hist-meta">${esc(e.message)}</div>`; }
}

async function checkout(path, body, btn) {
  btn.disabled = true;
  try { const r = await api(path, { method: 'POST', body: JSON.stringify(body) }); window.location.href = r.checkout_url; }
  catch (e) { toast(e.message, true); btn.disabled = false; }
}

async function openHistory() {
  const list = $('historyList');
  list.innerHTML = '<div class="hist-meta">Loading…</div>';
  $('historyDialog').showModal();
  try {
    const { jobs } = await api('/api/jobs?limit=50');
    list.innerHTML = jobs.length ? '' : '<div class="hist-meta">No analyses yet. Upload a video to get started.</div>';
    jobs.forEach(j => {
      const row = document.createElement('div');
      row.className = 'hist-row';
      const when = new Date(j.created_at).toLocaleString();
      const secs = Math.round(j.actual_duration_s ?? j.est_duration_s);
      row.innerHTML = `<div style="min-width:0"><div class="hist-name">${esc(j.original_filename)}</div>
          <div class="hist-meta">${esc(when)} · ${secs}s${j.credits_captured != null ? ` · ${j.credits_captured} credits` : ''}</div></div>
        <span class="status ${esc(j.status)}">${esc(j.status.replace('_', ' '))}</span>`;
      if (j.status === 'SUCCEEDED') {
        const btn = document.createElement('button');
        btn.className = 'btn-new'; btn.type = 'button'; btn.textContent = 'Open';
        btn.addEventListener('click', () => { $('historyDialog').close(); openJob(j.id); });
        row.appendChild(btn);
      } else {
        row.appendChild(document.createElement('span'));
      }
      list.appendChild(row);
    });
  } catch (e) { list.innerHTML = `<div class="hist-meta">${esc(e.message)}</div>`; }
}

$('btnBuy').addEventListener('click', openBuy);
$('btnHistory').addEventListener('click', openHistory);

// ─── Reset ──────────────────────────────────────────────────────────────────
function resetUI() {
  clearTimeout(pollTimer);
  stopProcView();
  resetReport();
  procVideo.removeAttribute('src'); procVideo.load();
  analysisData = null; currentJobId = null; video.removeAttribute('src'); video.load();
  charts.forEach(c => c.destroy()); charts = [];
  if (me) $('uploadZone').classList.remove('hidden');
  $('samplesSection').style.display = '';
  $('processing').classList.remove('active');
  $('videoContainer').classList.remove('active');
  $('chartsEmpty').style.display = '';
  $('chartsContainer').classList.remove('active');
  $('btnCsv').hidden = true;
  $('btnCancel').hidden = true;
  $('headerMeta').textContent = me ? 'Upload a video or select a sample' : 'Select a video to view analysis';
  fileInput.value = '';
  document.querySelectorAll('.sample-card').forEach(c => c.classList.remove('active'));
}

// ─── Boot ───────────────────────────────────────────────────────────────────
(async function boot() {
  const params = new URLSearchParams(window.location.search);
  if (params.get('checkout') === 'success') toast('Payment received — your credits will appear in a few seconds.');
  if (params.get('checkout') === 'cancel') toast('Checkout cancelled — you were not charged.');
  if (params.has('checkout')) history.replaceState(null, '', '/');
  await loadAccount();
  loadSamples();
  if (params.get('checkout') === 'success') setTimeout(refreshCredits, 4000);
})();
