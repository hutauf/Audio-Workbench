const state = { recordings: [], selectedId: null, detail: null, pollTimer: null, resizeObserver: null,
  offset: 0, total: 0, limit: 50, listRequest: 0, detailRequest: 0, visualRequest: 0,
  viewStart: 0, viewSeconds: 30, visual: null, signature: null, pendingSeek: null };

const $ = (selector) => document.querySelector(selector);

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatDuration(seconds) {
  if (seconds == null) return "wird berechnet";
  const total = Math.max(0, Math.round(Number(seconds)));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
}

function formatBytes(bytes) {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const exponent = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / 1024 ** exponent).toFixed(exponent ? 1 : 0)} ${units[exponent]}`;
}

function formatDate(value) {
  if (!value) return "";
  return new Intl.DateTimeFormat("de-DE", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}

function statusText(status) {
  return ({ queued: "wartet", normalizing: "normalisiert", processing: "analysiert", ready: "bereit", partial: "teilweise", failed: "fehler" })[status] || status || "unbekannt";
}

function showToast(message) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.classList.add("visible");
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => toast.classList.remove("visible"), 3500);
}

async function api(url, options = {}) {
  const response = await fetch(url, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

async function refreshList() {
  const request = ++state.listRequest;
  const params = new URLSearchParams({offset: state.offset, limit: state.limit});
  for (const key of ['q','source','sort','status','tag','after','before','confidence']) {
    const element = $(key === 'q' ? '#library-query' : `#library-${key}`);
    if (element?.value) params.set(key, element.value);
  }
  const page = await api(`/api/recordings?${params}`);
  if (request !== state.listRequest) return;
  state.recordings = page.items;
  state.offset = page.offset;
  state.total = page.total;
  renderList();
  if (!state.selectedId) renderEmpty();
  window.clearTimeout(state.listTimer);
  if (page.pending) state.listTimer = window.setTimeout(() => refreshList().catch(e => showToast(e.message)), 4000);
}

function renderList() {
  const list = $("#recording-list");
  $('#page-info').textContent = `${state.total ? state.offset+1 : 0}–${Math.min(state.offset+state.limit, state.total)} / ${state.total}`;
  $('#page-prev').disabled = state.offset === 0;
  $('#page-next').disabled = state.offset+state.limit >= state.total;
  if (!state.recordings.length) {
    list.innerHTML = `<div class="empty-list">Keine passenden Aufnahmen. Filter ändern oder Dateien importieren.</div>`;
    return;
  }
  list.innerHTML = state.recordings.map((recording) => `
    <article class="library-entry"><button class="recording-item ${recording.id === state.selectedId ? "is-selected" : ""}" data-recording-id="${recording.id}">
      <span class="recording-icon">◒</span>
      <span>
        <span class="recording-name">${escapeHtml(recording.original_name)}</span>
        <span class="recording-meta"><span class="recording-status ${escapeHtml(recording.status)}"></span>${formatDuration(recording.duration)} · ${formatDate(recording.created_at)}</span>
      </span>
    </button>
    ${recording.tags.length ? `<div class="recording-tags">${recording.tags.map(escapeHtml).join(' · ')}</div>` : ''}
    ${recording.matches.map(match => `<button class="search-match" data-match-id="${recording.id}" data-start="${match.start ?? ''}"><small>${escapeHtml(match.source)} ${match.start != null ? formatDuration(match.start) : ''}</small>${escapeHtml(match.text)}</button>`).join('')}
    </article>`).join("");
  list.querySelectorAll("[data-recording-id]").forEach((button) => {
    button.addEventListener("click", () => selectRecording(button.dataset.recordingId));
  });
  list.querySelectorAll('[data-match-id]').forEach(button => button.addEventListener('click', () => selectRecording(button.dataset.matchId, button.dataset.start === '' ? null : Number(button.dataset.start))));
}

function renderEmpty() {
  $("#main-content").innerHTML = `
    <div class="content-width empty-state">
      <div class="empty-inner">
        <div class="eyebrow">Audio ist das Primärobjekt</div>
        <h1>Alles hören.<br /><span class="green-text">Mehr sehen.</span></h1>
        <p>Importiere eine Aufnahme und die Workbench legt automatisch eine lokale Arbeitskopie an. Danach laufen die ersten Analyzer über dieselbe Datei.</p>
        <div class="empty-architecture">
          <div class="architecture-card"><div class="architecture-number">01</div><strong>Import</strong><span>M4A, MP3, WAV, FLAC, OGG und mehr</span></div>
          <div class="architecture-card"><div class="architecture-number">02</div><strong>Analyse</strong><span>Unabhängige Jobs, eine lokale Queue</span></div>
          <div class="architecture-card"><div class="architecture-number">03</div><strong>Ergebnisse</strong><span>Zeitbezogene Kacheln und Ereignisse</span></div>
        </div>
      </div>
    </div>`;
}

async function selectRecording(id, start = null) {
  clearTimeout(state.viewTimer);
  clearTimeout(state.pollTimer);
  if (id !== state.selectedId) {
    state.detail = null;
    $('#main-content').innerHTML = '<div class="content-width empty-state">Aufnahme wird geladen …</div>';
  }
  state.selectedId = id;
  state.pendingSeek = start;
  state.viewStart = Math.max(0, (start ?? 0)-2);
  state.visual = null;
  state.signature = null;
  state.visualRequest++;
  renderList();
  await refreshDetail();
}

async function refreshDetail() {
  if (!state.selectedId) return renderEmpty();
  try {
    const id = state.selectedId;
    const request = ++state.detailRequest;
    const detail = await api(`/api/recordings/${id}`);
    if (id !== state.selectedId || request !== state.detailRequest) return;
    const signature = JSON.stringify(detail);
    state.detail = detail;
    if (signature !== state.signature) {
      state.signature = signature;
      renderDetail();
      await loadVisual();
    }
    schedulePolling();
  } catch (error) {
    showToast(error.message);
  }
}

function getResult(id) {
  return state.detail?.results?.find((item) => item.analyzer_id === id)?.payload;
}

function renderDetail() {
  const item = state.detail;
  const previousPlayer = $('#audio-player');
  const keepPlayer = previousPlayer?.dataset.recordingId === item.id;
  const wasPlaying = keepPlayer && !previousPlayer.paused;
  const tagDraft = keepPlayer ? $('#recording-tags')?.value : null;
  const profile = getResult("audio_profile") || {};
  const vadId = getResult("vad_silero") ? "vad_silero" : "vad_energy";
  const vad = getResult(vadId) || {};
  const waveform = getResult("waveform") || {};
  const activeJobs = (item.jobs || []).filter((job) => job.status !== "done");
  const readyCount = (item.jobs || []).filter((job) => job.status === "done").length;
  const totalJobs = item.jobs?.length || 3;
  const eventCount = vad.events?.length || 0;
  const vadLabel = vad.method === "sherpa-onnx/silero-vad" ? "Silero VAD / sherpa-onnx" : "Energie-Baseline, noch kein ML-Modell";
  $("#main-content").innerHTML = `
    <div class="content-width">
      <div class="topline">
        <div class="breadcrumb">Aufnahmen <span>/</span> ${escapeHtml(item.original_name)}</div>
        <div class="header-actions"><button class="small-button" id="copy-id">ID kopieren</button><button class="small-button" id="reload-detail">↻</button></div>
      </div>
      <header class="recording-header">
        <div>
          <div class="eyebrow">Aufnahme / ${escapeHtml(formatDate(item.created_at))}</div>
          <h1 title="${escapeHtml(item.original_name)}">${escapeHtml(item.original_name)}</h1>
          <div class="recording-subline"><span>${formatBytes(item.size_bytes)}</span><span>${item.channels || "?"} Kanal</span><span>${item.sample_rate ? `${item.sample_rate / 1000} kHz Arbeitskopie` : "Arbeitskopie wird erstellt"}</span></div>
        </div>
        <div class="state-pill ${escapeHtml(item.status)}">${escapeHtml(statusText(item.status))}</div>
      </header>
      ${item.error ? `<div class="error-panel">${escapeHtml(item.error)}</div>` : ""}
      ${activeJobs.length && item.status !== "ready" && item.status !== "partial" ? `<div class="queue-panel"><span class="queue-spinner"></span><span><strong>${readyCount}/${totalJobs} Analyzer fertig</strong><br />Die nächsten Jobs laufen lokal in der Reihenfolge der Queue.</span></div>` : ""}
      <section class="player-panel">
        <div><div class="player-caption">Mono-Arbeitskopie</div><audio id="audio-player" data-recording-id="${item.id}" controls preload="metadata" src="${item.audio_url}"></audio></div>
        <div class="player-note"><strong>Original geschützt</strong>${formatDuration(profile.duration_seconds ?? item.duration)} · ${formatBytes(item.size_bytes)} Import</div>
      </section>
      <form id="tag-editor" class="tag-editor"><label>Tags (durch Komma getrennt)<input id="recording-tags" value="${escapeHtml((item.tags || []).join(', '))}" placeholder="Garten, Interview, Projekt …" /></label><button class="small-button">Speichern</button></form>

      <div class="section-title"><h2>Audio-Timeline</h2><span>${eventCount} Sprachbereiche erkannt</span></div>
      <section class="timeline-panel">
        <div class="timeline-navigation"><label>Zeitfenster <select id="view-seconds"><option value="0">Gesamte Aufnahme</option><option value="5">5 Sekunden</option><option value="15">15 Sekunden</option><option value="30">30 Sekunden</option><option value="60">60 Sekunden</option></select></label><button class="small-button" id="view-prev">←</button><button class="small-button" id="view-player">Zur Abspielposition</button><button class="small-button" id="view-next">→</button><span id="view-range"></span></div>
        <input id="view-position" type="range" min="0" max="${Math.max(0, (item.duration || 0)-state.viewSeconds)}" step="0.1" value="${state.viewStart}" aria-label="Zeitfenster verschieben" />
        <div class="timeline-toolbar"><span>Amplitude / Zeit</span><div class="timeline-legend"><span class="legend-item"><i class="legend-swatch speech"></i>Sprache</span><span class="legend-item"><i class="legend-swatch playhead"></i>Position</span></div></div>
        <div class="waveform-wrap"><canvas id="waveform-canvas"></canvas><div class="timeline-labels"><span id="view-label-start">0:00</span><span id="view-label-end">${formatDuration(item.duration)}</span></div></div>
        <div class="spectrogram-wrap"><canvas id="spectrogram-canvas"></canvas><span class="spectrogram-label">Frequenz / Spektrogramm</span></div>
        <div class="timeline-help" id="view-help">Zeitfenster werden nachgeladen. Klicke auf Waveform oder Ereignis zum Springen.</div>
      </section>

      <div class="section-title"><h2>Analyse-Layer</h2><span>${readyCount} von ${totalJobs} aktiv</span></div>
      <section class="metrics-grid">
        <div class="metric-card"><div class="metric-label">Dauer</div><div class="metric-value">${formatDuration(profile.duration_seconds ?? item.duration)}</div><div class="metric-note">normalisiert</div></div>
        <div class="metric-card"><div class="metric-label">RMS</div><div class="metric-value">${profile.rms_dbfs != null ? `${profile.rms_dbfs} dBFS` : "…"}</div><div class="metric-note">mittlerer Pegel</div></div>
        <div class="metric-card"><div class="metric-label">Peak</div><div class="metric-value">${profile.peak_dbfs != null ? `${profile.peak_dbfs} dBFS` : "…"}</div><div class="metric-note">höchster Sample-Pegel</div></div>
        <div class="metric-card"><div class="metric-label">Sprache</div><div class="metric-value">${vad.active_seconds != null ? formatDuration(vad.active_seconds) : "…"}</div><div class="metric-note">${escapeHtml(vadLabel)}</div></div>
        <div class="metric-card"><div class="metric-label">Dynamik</div><div class="metric-value">${profile.crest_factor != null ? `${profile.crest_factor}×` : "…"}</div><div class="metric-note">Crest Factor</div></div>
      </section>

      <div class="section-title"><h2>Analyzer</h2><span>Plugins liefern zeitbezogene Ergebnisse</span></div>
      <section class="analysis-grid">${renderAnalysisCards(item, profile, vad, waveform, vadId)}</section>

      <div class="section-title"><h2>Sprachaktivität</h2><span>${escapeHtml(vadLabel)}</span></div>
      <section class="event-list">${renderEvents(vad.events || [], "Noch keine Sprachbereiche. Sobald der VAD-Job fertig ist, erscheinen sie hier.")}</section>

      ${renderTranscriptSection()}
      ${renderChatSection()}
      ${renderSpeakerSection()}
      ${renderSoundEventsSection()}
    </div>`;
  if (keepPlayer) $('#audio-player').replaceWith(previousPlayer);
  if (wasPlaying) previousPlayer.play().catch(() => {});
  if (tagDraft != null) $('#recording-tags').value = tagDraft;
  $('#view-seconds').value = String(state.viewSeconds);
  if (state.pendingSeek != null) {
    const player = $('#audio-player');
    const seek = state.pendingSeek;
    const apply = () => { player.currentTime = seek; };
    if (player.readyState) apply(); else player.addEventListener('loadedmetadata', apply, {once: true});
    state.pendingSeek = null;
  }
  bindDetailEvents();
  drawVisuals();
}

function mergeChatTranscript() {
  const transcript = getResult("whisper_cpp") || {};
  const diarization = getResult("diarization") || {};

  if (!transcript.segments || transcript.segments.length === 0) return null;

  const segments = transcript.segments;
  const speakers = diarization.events || [];

  // Match each transcript segment with speaker based on MAXIMUM OVERLAP
  const chatMessages = segments.map(seg => {
    let bestSpeaker = null;
    let maxOverlap = 0;

    // Calculate overlap with each speaker event
    speakers.forEach(sp => {
      const overlapStart = Math.max(seg.start, sp.start);
      const overlapEnd = Math.min(seg.end, sp.end);
      const overlap = Math.max(0, overlapEnd - overlapStart);

      if (overlap > maxOverlap) {
        maxOverlap = overlap;
        bestSpeaker = sp;
      }
    });

    return {
      start: seg.start,
      end: seg.end,
      text: seg.text,
      speaker: bestSpeaker ? bestSpeaker.label : "UNKNOWN",
      speakerIndex: bestSpeaker ? bestSpeaker.speaker_index : -1,
      overlap: maxOverlap
    };
  });

  // Keep individual segments (no grouping) for transparency
  return chatMessages.map(msg => ({
    speaker: msg.speaker,
    speakerIndex: msg.speakerIndex,
    start: msg.start,
    end: msg.end,
    texts: [msg.text],
    overlap: msg.overlap
  }));
}

function renderTranscriptSection() {
  const transcript = getResult("whisper_cpp") || {};
  if (!transcript.segments || transcript.segments.length === 0) return "";

  const fullText = transcript.text || transcript.segments.map(s => s.text).join(" ");

  return `
    <div class="section-title"><h2>Transkript</h2><span>${transcript.segments.length} Segmente · ${transcript.language || "de"}</span></div>
    <section class="transcript-panel">
      <div class="transcript-full">${escapeHtml(fullText)}</div>
      <div class="transcript-segments">${transcript.segments.map(seg => `
        <div class="transcript-segment" data-start="${seg.start}">
          <span class="transcript-time">${formatDuration(seg.start)} – ${formatDuration(seg.end)}</span>
          <span class="transcript-text">${escapeHtml(seg.text)}</span>
        </div>
      `).join("")}</div>
    </section>`;
}

function renderChatSection() {
  const transcript = getResult("whisper_cpp") || {};
  const diarization = getResult("diarization") || {};

  // Only show chat if we have both transcript and diarization
  if (!transcript.segments || transcript.segments.length === 0) return "";
  if (!diarization.events || diarization.events.length === 0) return "";

  const chatMessages = mergeChatTranscript();
  if (!chatMessages) return "";

  const speakers = [...new Set(diarization.events.map(e => e.label))];

  return `
    <div class="section-title"><h2>Konversation</h2><span>${speakers.length} Sprecher · ${chatMessages.length} Nachrichten</span></div>
    <section class="chat-panel">
      ${chatMessages.map(msg => {
        const speakerColor = ["blue", "green", "amber", "purple", "pink"][msg.speakerIndex % 5] || "blue";
        const alignment = msg.speakerIndex % 2 === 0 ? "left" : "right";
        return `
          <div class="chat-message ${alignment}" data-start="${msg.start}">
            <div class="chat-bubble ${speakerColor}">
              <div class="chat-speaker">${escapeHtml(msg.speaker)}</div>
              ${msg.texts.map(text => `<div class="chat-text">${escapeHtml(text)}</div>`).join("")}
              <div class="chat-time">${formatDuration(msg.start)}</div>
            </div>
          </div>
        `;
      }).join("")}
    </section>`;
}

function renderSpeakerSection() {
  const diarization = getResult("diarization") || {};

  if (!diarization.events || diarization.events.length === 0) return "";
  const speakers = [...new Set(diarization.events.map(e => e.label))];
  return `
    <div class="section-title"><h2>Sprecher (Raw)</h2><span>${speakers.length} erkannte Sprecher · ${diarization.events.length} Segmente</span></div>
    <section class="event-list">${renderEvents(diarization.events, "Keine Sprecher erkannt.")}</section>`;
}

function renderSoundEventsSection() {
  const yamnet = getResult("yamnet") || {};
  const birdnet = getResult("birdnet") || {};
  const hasYamnet = yamnet.events && yamnet.events.length > 0;
  const hasBirdnet = birdnet.events && birdnet.events.length > 0;
  if (!hasYamnet && !hasBirdnet) return "";

  let html = `<div class="section-title"><h2>Sound Events</h2><span>`;
  if (hasYamnet) html += `YAMNet: ${yamnet.events.length} · `;
  if (hasBirdnet) html += `BirdNET: ${birdnet.events.length}`;
  html += `</span></div>`;

  if (hasYamnet) {
    const topLabels = yamnet.top_labels?.slice(0, 10) || [];
    html += `<section class="sound-events-panel">
      <h3>YAMNet / AudioSet</h3>
      ${topLabels.length ? `<div class="top-labels">${topLabels.map(l => `<span class="label-tag">${escapeHtml(l.label)} <em>${Math.round(l.confidence * 100)}%</em></span>`).join("")}</div>` : ""}
      <div class="event-list compact">${renderEvents(yamnet.events, "Keine Events.")}</div>
    </section>`;
  }

  if (hasBirdnet) {
    const topLabels = birdnet.top_labels?.slice(0, 10) || [];
    html += `<section class="sound-events-panel">
      <h3>BirdNET / Vogelstimmen</h3>
      ${topLabels.length ? `<div class="top-labels">${topLabels.map(l => `<span class="label-tag bird">${escapeHtml(l.label)} <em>${Math.round(l.confidence * 100)}%</em></span>`).join("")}</div>` : ""}
      <div class="event-list compact">${renderEvents(birdnet.events, "Keine Vögel erkannt.")}</div>
    </section>`;
  }

  return html;
}

function renderEvents(events, emptyMessage = "Noch keine Ereignisse.") {
  if (!events.length) return `<div class="no-events">${emptyMessage}</div>`;
  return events.map((event) => {
    const confidence = event.confidence == null ? "Modell" : `${Math.round(event.confidence * 100)} %`;
    return `<div class="event-row" data-start="${event.start}"><span class="event-time">${formatDuration(event.start)}–${formatDuration(event.end)}</span><span class="event-label">${escapeHtml(event.label)}</span><span class="event-confidence">${confidence}</span></div>`;
  }).join("");
}

function renderAnalysisCards(item, profile, vad, waveform, vadId = "vad_energy") {
  const job = (id) => item.jobs?.find((entry) => entry.analyzer_id === id) || {};
  const statusIcon = (id) => {
    const st = job(id).status;
    if (st === "done") return '<span class="status-icon done">✓</span>';
    if (st === "failed") return '<span class="status-icon failed">✗</span>';
    if (st === "running") return '<span class="status-icon running">→</span>';
    if (st === "queued") return '<span class="status-icon queued">⋯</span>';
    return '<span class="status-icon">○</span>';
  };
  const resultButton = (id, result) => {
    if (!result || job(id).status !== "done") return "";
    let preview = "Details →";
    if (result.segments?.length) preview = `${result.segments.length} Segmente →`;
    else if (result.events?.length) preview = `${result.events.length} Events →`;
    else if (result.waveform?.length) preview = `${result.waveform.length} Bins →`;
    return `<button class="result-button" data-analyzer-id="${id}">${preview}</button>`;
  };
  const readyCard = (icon, title, description, id) => {
    const result = getResult(id);
    return `
      <article class="analysis-card ready">
        <div class="analysis-top">
          <div class="analysis-icon">${icon}</div>
          <div class="analysis-actions">
            <button class="rerun-button" data-analyzer-id="${id}" title="Neu ausführen">↻</button>
            ${statusIcon(id)}
          </div>
        </div>
        <h3>${title}</h3>
        <p>${description}</p>
        <div class="analysis-footer">
          ${resultButton(id, result)}
        </div>
      </article>`;
  };
  const planned = (icon, title, description, technology) => `<article class="analysis-card planned"><div class="analysis-top"><div class="analysis-icon">${icon}</div><div class="analysis-badge">später</div></div><h3>${title}</h3><p>${description}</p><div class="analysis-footer"><span>${technology}</span><span>Plugin-Slot</span></div></article>`;
  const configuredOrPlanned = (id, icon, readyTitle, readyDescription, plannedTitle, plannedDescription, technology) => (
    job(id).status ? readyCard(icon, readyTitle, readyDescription, id) : planned(icon, plannedTitle, plannedDescription, technology)
  );
  return [
    readyCard("∿", "Audio-Profil", "Pegel, Kanalzahl und Arbeitskopie", "audio_profile"),
    readyCard("⌁", "Waveform & Spektrum", "Downsampled für schnelle Navigation", "waveform"),
    readyCard("◌", vadId === "vad_silero" ? "Sprachaktivität / Silero" : "Sprachaktivität", vadId === "vad_silero" ? "ML-Segmente über sherpa-onnx" : "Zeitbereiche über Energie-Schwelle", vadId),
    configuredOrPlanned("whisper_cpp", "Aa", "Transkript / whisper.cpp", "Lokale Speech-to-Text-Ausgabe", "Transkript", "Speech-to-Text nur auf erkannte Sprachbereiche", "sherpa-onnx / Whisper"),
    configuredOrPlanned("diarization", "••", "Sprecher / Diarisierung", "Lokale Sprechersegmente", "Sprecher", "Diarisierung und Identität über mehrere Aufnahmen", "Embeddings"),
    configuredOrPlanned("yamnet", "✣", "Sound Events / YAMNet", "AudioSet-Klassen auf Zeitfenstern", "Sound Events", "Geräusche, Musik, Tiere und Umgebung", "YAMNet / PANNs"),
    configuredOrPlanned("birdnet", "♧", "Vogelstimmen / BirdNET", "BirdNET v2.4 Artenerkennung", "Vogelstimmen", "Arten und Zeitbereiche im Audio", "BirdNET lokal"),
    planned("⌂", "Ort / Szene", "Akustische Umgebung wie Wald oder U-Bahn", "DCASE ASC"),
  ].join("");
}


function showModal(analyzerId, result) {
  const modalHtml = `
    <div class="modal-overlay" id="modal-overlay">
      <div class="modal-panel">
        <div class="modal-header">
          <h2>${analyzerId}</h2>
          <button class="modal-close" id="modal-close">✕</button>
        </div>
        <div class="modal-body">${renderModalContent(analyzerId, result)}</div>
      </div>
    </div>`;
  document.body.insertAdjacentHTML("beforeend", modalHtml);
  $("#modal-close").addEventListener("click", closeModal);
  $("#modal-overlay").addEventListener("click", (e) => { if (e.target.id === "modal-overlay") closeModal(); });
}

function closeModal() {
  $("#modal-overlay")?.remove();
}

function renderModalContent(analyzerId, result) {
  if (analyzerId === "whisper_cpp" && result.segments) {
    // Check if we have diarization for chat view
    const diarization = getResult("diarization") || {};
    const hasSpeakers = diarization.events && diarization.events.length > 0;
    const chatMessages = hasSpeakers ? mergeChatTranscript() : null;

    if (hasSpeakers && chatMessages) {
      return `<div class="modal-chat">
        ${chatMessages.map(msg => {
          const speakerColor = ["blue", "green", "amber", "purple", "pink"][msg.speakerIndex % 5] || "blue";
          const alignment = msg.speakerIndex % 2 === 0 ? "left" : "right";
          return `
            <div class="chat-message ${alignment}">
              <div class="chat-bubble ${speakerColor}">
                <div class="chat-speaker">${escapeHtml(msg.speaker)}</div>
                ${msg.texts.map(text => `<div class="chat-text">${escapeHtml(text)}</div>`).join("")}
                <div class="chat-time">${formatDuration(msg.start)}</div>
              </div>
            </div>
          `;
        }).join("")}
      </div>`;
    }

    return `<div class="modal-transcript">
      <div class="modal-full-text">${escapeHtml(result.text || result.segments.map(s => s.text).join(" "))}</div>
      <div class="modal-segments">${result.segments.map(seg => `
        <div class="modal-segment">
          <span class="modal-time">${formatDuration(seg.start)} – ${formatDuration(seg.end)}</span>
          <span class="modal-text">${escapeHtml(seg.text)}</span>
        </div>
      `).join("")}</div>
    </div>`;
  }
  if ((analyzerId === "yamnet" || analyzerId === "birdnet") && result.events) {
    const topLabels = result.top_labels?.slice(0, 15) || [];
    return `<div class="modal-events">
      ${topLabels.length ? `<div class="modal-top-labels">${topLabels.map(l => `<span class="modal-label-tag ${analyzerId === "birdnet" ? "bird" : ""}">${escapeHtml(l.label)} <em>${Math.round(l.confidence * 100)}%</em></span>`).join("")}</div>` : ""}
      <div class="modal-event-list">${result.events.map(e => `
        <div class="modal-event-row">
          <span class="modal-event-time">${formatDuration(e.start)} – ${formatDuration(e.end)}</span>
          <span class="modal-event-label">${escapeHtml(e.label)}</span>
          <span class="modal-event-conf">${Math.round((e.confidence || 0) * 100)}%</span>
        </div>
      `).join("")}</div>
    </div>`;
  }
  if ((analyzerId === "diarization" || analyzerId.startsWith("vad")) && result.events) {
    return `<div class="modal-events">
      <div class="modal-event-list">${result.events.map(e => `
        <div class="modal-event-row">
          <span class="modal-event-time">${formatDuration(e.start)} – ${formatDuration(e.end)}</span>
          <span class="modal-event-label">${escapeHtml(e.label)}</span>
          <span class="modal-event-conf">${e.confidence != null ? Math.round(e.confidence * 100) + "%" : "—"}</span>
        </div>
      `).join("")}</div>
    </div>`;
  }
  return `<pre class="modal-json">${escapeHtml(JSON.stringify(result, null, 2))}</pre>`;
}

function bindDetailEvents() {
  $('#tag-editor').addEventListener('submit', async event => {
    event.preventDefault();
    const id = state.detail.id;
    try {
      const result = await api(`/api/recordings/${id}/tags`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({tags: $('#recording-tags').value.split(',').map(t => t.trim()).filter(Boolean)})});
      if (state.detail?.id === id) {
        state.detail.tags = result.tags;
        $('#recording-tags').value = result.tags.join(', ');
      }
      showToast('Tags gespeichert');
      await refreshList();
      await loadTags();
    } catch (error) { showToast(error.message); }
  });
  $('#view-seconds').addEventListener('change', event => { state.viewSeconds = Number(event.target.value); loadVisual(); });
  $('#view-position').addEventListener('input', event => {
    state.viewStart = Number(event.target.value);
    clearTimeout(state.viewTimer);
    state.viewTimer = setTimeout(loadVisual, 150);
  });
  $('#view-prev').onclick = () => { state.viewStart -= state.viewSeconds; loadVisual(); };
  $('#view-next').onclick = () => { state.viewStart += state.viewSeconds; loadVisual(); };
  $('#view-player').onclick = () => { state.viewStart = $('#audio-player').currentTime; if (!state.viewSeconds) state.viewSeconds = 30; loadVisual(); };
  $("#reload-detail")?.addEventListener("click", refreshDetail);
  $("#copy-id")?.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(state.detail.id); showToast("Aufnahme-ID kopiert"); } catch { showToast(state.detail.id); }
  });
  if ($('#audio-player')) $('#audio-player').ontimeupdate = drawVisuals;
  $("#waveform-canvas")?.addEventListener("click", (event) => {
    const canvas = event.currentTarget;
    const bounds = canvas.getBoundingClientRect();
    const duration = Number(state.visual?.end_seconds ?? state.detail.duration ?? 0) - Number(state.visual?.start_seconds ?? 0);
    const player = $("#audio-player");
    if (player && duration) player.currentTime = Number(state.visual?.start_seconds || 0) + Math.max(0, Math.min(duration, ((event.clientX - bounds.left) / bounds.width) * duration));
  });
  document.querySelectorAll(".event-row[data-start], .transcript-segment[data-start], .chat-message[data-start]").forEach((row) => row.addEventListener("click", () => {
    const player = $("#audio-player");
    if (player) {
      player.currentTime = Number(row.dataset.start);
      state.viewStart = Math.max(0, player.currentTime-2);
      if (!state.viewSeconds) state.viewSeconds = 30;
      loadVisual();
      player.play().catch(() => {});
    }
  }));
  document.querySelectorAll(".rerun-button[data-analyzer-id]").forEach((btn) => btn.addEventListener("click", async (event) => {
    event.stopPropagation();
    const analyzerId = btn.dataset.analyzerId;
    btn.disabled = true;
    btn.textContent = "…";
    try {
      await api(`/api/recordings/${state.detail.id}/rerun?analyzer_id=${analyzerId}`, { method: "POST" });
      showToast(`${analyzerId} neu gestartet`);
      await refreshDetail();
    } catch (error) {
      showToast(error.message);
      btn.disabled = false;
      btn.textContent = "↻";
    }
  }));
  document.querySelectorAll(".result-button[data-analyzer-id]").forEach((btn) => btn.addEventListener("click", () => {
    const analyzerId = btn.dataset.analyzerId;
    const result = getResult(analyzerId);
    if (result) showModal(analyzerId, result);
  }));
}

async function loadVisual() {
  if (!state.detail || !$('#view-seconds')) return;
  const id = state.selectedId;
  const request = ++state.visualRequest;
  const duration = Number(state.detail.duration || 0);
  state.viewStart = Math.max(0, Math.min(state.viewStart, Math.max(0, duration-state.viewSeconds)));
  if (!state.viewSeconds) state.viewStart = 0;
  $('#view-seconds').value = String(state.viewSeconds);
  const slider = $('#view-position');
  slider.max = Math.max(0, duration-state.viewSeconds);
  slider.value = state.viewStart;
  slider.disabled = !state.viewSeconds || duration <= state.viewSeconds;
  try {
    const visual = state.viewSeconds && duration ? await api(`/api/recordings/${id}/visualization?start=${state.viewStart}&end=${Math.min(duration, state.viewStart+state.viewSeconds)}`) : getResult('waveform');
    if (id !== state.selectedId || request !== state.visualRequest) return;
    state.visual = visual || null;
    const start = visual?.start_seconds || 0;
    const end = visual?.end_seconds ?? duration;
    $('#view-range').textContent = `${formatDuration(start)} – ${formatDuration(end)}`;
    $('#view-label-start').textContent = formatDuration(start);
    $('#view-label-end').textContent = formatDuration(end);
    $('#view-help').textContent = visual?.visualization_version !== 2 ? 'Alte Übersicht: Waveform-Analyzer erneut ausführen. Zeitfenster funktionieren bereits.' :
      (visual?.spectrogram?.sampled ? 'Spektrum: zeitliche Stichproben; für Details auf 5–15 Sekunden zoomen. Waveform enthält alle Peaks.' : 'Zeitfenster verschieben oder auf ein Ereignis klicken.');
    state.specCache = null;
    drawVisuals();
  } catch (error) { if (id === state.selectedId) showToast(error.message); }
}

function setupCanvas(canvas) {
  const bounds = canvas.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.round(bounds.width * ratio));
  canvas.height = Math.max(1, Math.round(bounds.height * ratio));
  const ctx = canvas.getContext("2d");
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  return { ctx, width: bounds.width, height: bounds.height };
}

function drawVisuals() {
  const waveformCanvas = $("#waveform-canvas");
  const spectrogramCanvas = $("#spectrogram-canvas");
  if (!waveformCanvas || !spectrogramCanvas || !state.detail) return;
  const waveform = state.visual || {};
  const vad = getResult("vad_silero") || getResult("vad_energy") || {};
  const { ctx, width, height } = setupCanvas(waveformCanvas);
  ctx.clearRect(0, 0, width, height);
  ctx.strokeStyle = "rgba(213,227,234,0.07)";
  ctx.lineWidth = 1;
  for (let y = 0.5; y < height; y += height / 4) { ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(width, y); ctx.stroke(); }
  const viewStart = Number(waveform.start_seconds || 0);
  const duration = Math.max(0.001, Number(waveform.end_seconds ?? state.detail.duration ?? 1) - viewStart);
  (vad.events || []).forEach((event) => {
    if (event.end <= viewStart || event.start >= viewStart+duration) return;
    const x = ((Math.max(viewStart, event.start)-viewStart) / duration) * width;
    const w = ((Math.min(viewStart+duration, event.end) - Math.max(viewStart,event.start)) / duration) * width;
    ctx.fillStyle = "rgba(184,248,106,0.12)";
    ctx.fillRect(x, 0, Math.max(w, 2), height);
  });
  const bins = waveform.waveform || [];
  if (bins.length) {
    const center = height / 2;
    ctx.strokeStyle = "#b8f86a";
    ctx.globalAlpha = 0.82;
    ctx.lineWidth = Math.max(1, width / bins.length * 0.55);
    bins.forEach((bin, index) => {
      const x = (index + 0.5) / bins.length * width;
      const min = center - Number(bin.max || 0) * center * 0.93;
      const max = center - Number(bin.min || 0) * center * 0.93;
      ctx.beginPath(); ctx.moveTo(x, min); ctx.lineTo(x, max); ctx.stroke();
    });
    ctx.globalAlpha = 1;
  }
  const player = $("#audio-player");
  if (player && Number.isFinite(player.currentTime)) {
    const x = ((player.currentTime-viewStart) / duration) * width;
    ctx.strokeStyle = "#f2bd70"; ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, height); ctx.stroke();
  }

  const spec = waveform.spectrogram;
  const specBounds = spectrogramCanvas.getBoundingClientRect();
  const cacheKey = `${specBounds.width}:${specBounds.height}:${window.devicePixelRatio}`;
  if (state.specCache?.canvas === spectrogramCanvas && state.specCache?.spec === spec && state.specCache?.key === cacheKey) return;
  state.specCache = {canvas: spectrogramCanvas, spec, key: cacheKey};
  const { ctx: specCtx, width: specWidth, height: specHeight } = setupCanvas(spectrogramCanvas);
  specCtx.clearRect(0, 0, specWidth, specHeight);
  if (!spec?.values?.length) { specCtx.fillStyle = "rgba(143,160,169,0.3)"; specCtx.font = "11px sans-serif"; specCtx.fillText("Spektrogramm wird mit NumPy erzeugt", 3, 18); return; }
  const rows = spec.values;
  const cellW = specWidth / rows.length;
  const cellH = specHeight / rows[0].length;
  // Render without gaps by using floor and adding 1px overlap
  rows.forEach((column, xIndex) => column.forEach((value, yIndex) => {
    const x = Math.floor(xIndex * cellW);
    const y = Math.floor(specHeight - (yIndex + 1) * cellH);
    const w = Math.ceil((xIndex + 1) * cellW) - x + 1;
    const h = Math.ceil(specHeight - yIndex * cellH) - y + 1;
    const hue = 190 - Number(value) * 155;
    specCtx.fillStyle = `hsla(${hue}, 75%, ${20 + Number(value) * 58}%, ${0.18 + Number(value) * 0.82})`;
    specCtx.fillRect(x, y, w, h);
  }));
}

function schedulePolling() {
  window.clearTimeout(state.pollTimer);
  const pending = state.detail?.jobs?.some((job) => ["queued", "running"].includes(job.status)) || ["queued", "normalizing", "processing"].includes(state.detail?.status);
  if (pending) state.pollTimer = window.setTimeout(refreshDetail, 1200);
}

async function uploadFiles(files) {
  if (state.importing) return showToast('Ein Import läuft bereits.');
  state.importing = true;
  let done = 0, failed = 0;
  for (const file of Array.from(files)) {
    $('#import-progress').textContent = `Import ${done+1}/${files.length}: ${file.name}`;
    const form = new FormData();
    form.append('file', file, file.name);
    try {
      await api('/api/import', {method: 'POST', body: form, headers: {'X-File-Modified': String(file.lastModified)}});
    } catch (error) { failed++; showToast(`${file.name}: ${error.message}`); }
    done++;
    if (done % 20 === 0) await refreshList();
  }
  state.importing = false;
  $('#file-input').value = '';
  $('#import-progress').textContent = `${done-failed} importiert, ${failed} fehlgeschlagen.`;
  await refreshList();
}

async function loadTags() {
  const tags = await api('/api/tags');
  $('#known-tags').innerHTML = tags.map(t => `<option value="${escapeHtml(t.tag)}"></option>`).join('');
}

function setupUpload() {
  const input = $('#file-input'), dropzone = $('#dropzone');
  input.addEventListener('change', () => uploadFiles(input.files).catch(e => { state.importing = false; showToast(e.message); }));
  ['dragenter','dragover'].forEach(type => dropzone.addEventListener(type, event => { event.preventDefault(); dropzone.classList.add('is-dragging'); }));
  ['dragleave','drop'].forEach(type => dropzone.addEventListener(type, event => { event.preventDefault(); dropzone.classList.remove('is-dragging'); }));
  dropzone.addEventListener('drop', event => uploadFiles(event.dataTransfer.files).catch(e => { state.importing = false; showToast(e.message); }));
}

function setupLibrary() {
  const form = $('#library-filters');
  form.addEventListener('submit', e => e.preventDefault());
  const refresh = () => { state.offset = 0; refreshList().catch(e => showToast(e.message)); };
  form.addEventListener('input', () => { clearTimeout(state.searchTimer); state.searchTimer = setTimeout(refresh, 300); });
  form.addEventListener('reset', () => setTimeout(refresh, 0));
  $('#page-prev').onclick = () => { state.offset = Math.max(0, state.offset-state.limit); refreshList().catch(e => showToast(e.message)); };
  $('#page-next').onclick = () => { state.offset += state.limit; refreshList().catch(e => showToast(e.message)); };
}

window.addEventListener("resize", () => { window.clearTimeout(state.resizeTimer); state.resizeTimer = window.setTimeout(drawVisuals, 100); });
window.addEventListener("DOMContentLoaded", async () => {
  setupUpload();
  setupLibrary();
  loadTags().catch(error => showToast(error.message));
  $("#refresh-button").addEventListener("click", () => refreshList().catch(e => showToast(e.message)));
  try { await refreshList(); } catch (error) { showToast(error.message); renderEmpty(); }
});
