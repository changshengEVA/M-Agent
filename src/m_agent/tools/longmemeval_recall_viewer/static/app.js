async function fetchJson(url) {
  const res = await fetch(url, { headers: { "Accept": "application/json" } });
  const data = await res.json();
  if (!res.ok) {
    throw new Error(data?.error || `HTTP ${res.status}`);
  }
  return data;
}

function pretty(obj) {
  try {
    return JSON.stringify(obj, null, 2);
  } catch (e) {
    return String(obj);
  }
}

function chip(text, cls = "") {
  const el = document.createElement("span");
  el.className = `chip ${cls}`.trim();
  el.textContent = text;
  return el;
}

function statusClass(status) {
  const s = String(status || "").toUpperCase();
  if (s === "SUFFICIENT") return "ok";
  if (s === "INSUFFICIENT") return "warn";
  if (s === "INVALID") return "bad";
  return "";
}

function pickEvidences(roundPayload) {
  // Prefer after_judge, fallback to after_rerank, fallback to before
  const ws =
    roundPayload?.workspace_after_judge ||
    roundPayload?.workspace_after_rerank ||
    roundPayload?.workspace_before ||
    null;
  const evidences = ws?.evidences;
  return Array.isArray(evidences) ? evidences : [];
}

function snippet(text, maxLen = 160) {
  const s = String(text || "").replace(/\s+/g, " ").trim();
  if (s.length <= maxLen) return s;
  return s.slice(0, maxLen) + "…";
}

const state = {
  questions: [],
  currentQid: null,
  rounds: [],
  currentRoundFile: null,
  currentRoundPayload: null,
  currentRecallPayload: null,
  search: "",
};

function setActive(container, selector, key, value) {
  const nodes = Array.from(container.querySelectorAll(selector));
  for (const n of nodes) {
    if (n.dataset[key] === value) n.classList.add("active");
    else n.classList.remove("active");
  }
}

function setupTabs() {
  const tabs = Array.from(document.querySelectorAll(".tab"));
  const panes = {
    round: document.getElementById("pane-round"),
    actions: document.getElementById("pane-actions"),
    judge: document.getElementById("pane-judge"),
    evidence: document.getElementById("pane-evidence"),
    recall: document.getElementById("pane-recall"),
  };
  for (const t of tabs) {
    t.addEventListener("click", () => {
      for (const x of tabs) x.classList.remove("active");
      t.classList.add("active");
      const tab = t.dataset.tab;
      for (const [k, p] of Object.entries(panes)) {
        p.classList.toggle("active", k === tab);
      }
    });
  }
}

function renderQuestions() {
  const list = document.getElementById("qid-list");
  list.innerHTML = "";
  const q = state.search.trim().toLowerCase();
  const filtered = state.questions.filter((x) => {
    if (!q) return true;
    return String(x.question_id || "").toLowerCase().includes(q);
  });
  document.getElementById("qid-count").textContent = `${filtered.length} / ${state.questions.length}`;
  for (const item of filtered) {
    const el = document.createElement("div");
    el.className = "item";
    el.dataset.qid = item.question_id;
    const title = document.createElement("div");
    title.className = "item-title";
    title.textContent = item.question_id;
    const meta = document.createElement("div");
    meta.className = "item-meta";
    meta.appendChild(chip(item.has_workspace_rounds ? "Workspace ✓" : "Workspace ×", item.has_workspace_rounds ? "ok" : "bad"));
    el.appendChild(title);
    el.appendChild(meta);
    el.addEventListener("click", () => selectQuestion(item.question_id));
    list.appendChild(el);
  }
  if (state.currentQid) setActive(list, ".item", "qid", state.currentQid);
}

function renderRounds() {
  const list = document.getElementById("round-list");
  list.innerHTML = "";
  const q = state.search.trim().toLowerCase();
  const filtered = state.rounds.filter((r) => {
    if (!q) return true;
    const file = String(r.file || "").toLowerCase();
    const cur = String(r.summary?.cur_query || "").toLowerCase();
    const st = String(r.summary?.status || "").toLowerCase();
    return file.includes(q) || cur.includes(q) || st.includes(q);
  });
  document.getElementById("round-count").textContent = `${filtered.length} / ${state.rounds.length}`;
  for (const r of filtered) {
    const el = document.createElement("div");
    el.className = "item";
    el.dataset.round = r.file;

    const title = document.createElement("div");
    title.className = "item-title";
    title.textContent = r.file;
    const meta = document.createElement("div");
    meta.className = "item-meta";
    const status = r.summary?.status || r.summary?.judge_status || "";
    if (status) meta.appendChild(chip(String(status), statusClass(status)));
    if (r.summary?.gap_type) meta.appendChild(chip(`gap:${r.summary.gap_type}`));
    if (typeof r.summary?.evidence_count_after_rerank === "number") {
      meta.appendChild(chip(`ev:${r.summary.evidence_count_after_rerank}`));
    }
    el.appendChild(title);
    el.appendChild(meta);
    el.addEventListener("click", () => selectRound(r.file));
    list.appendChild(el);
  }
  if (state.currentRoundFile) setActive(list, ".item", "round", state.currentRoundFile);
}

function renderDetailHeader() {
  const title = document.getElementById("detail-title");
  title.textContent = state.currentQid
    ? `${state.currentQid}${state.currentRoundFile ? " / " + state.currentRoundFile : ""}`
    : "Detail";
  const chips = document.getElementById("detail-chips");
  chips.innerHTML = "";
  const round = state.currentRoundPayload;
  if (round) {
    const st = round.workspace_status || round.status;
    if (st) chips.appendChild(chip(String(st), statusClass(st)));
    const judge = round.judge_result || round.judge;
    if (judge?.gap_type) chips.appendChild(chip(`gap:${judge.gap_type}`));
    if (judge?.status) chips.appendChild(chip(`judge:${judge.status}`, statusClass(judge.status)));
  }
}

function renderRoundPayload() {
  document.getElementById("round-json").textContent = roundJsonFiltered(state.currentRoundPayload);
  document.getElementById("actions-json").textContent = pretty(state.currentRoundPayload?.actions || []);
  const judge = state.currentRoundPayload?.judge_result || state.currentRoundPayload?.judge || null;
  document.getElementById("judge-json").textContent = pretty(judge);
  renderEvidenceTab();
  renderDetailHeader();
}

function roundJsonFiltered(payload) {
  // Keep full json but allow search highlight by filtering via text.
  const raw = pretty(payload || {});
  const q = state.search.trim().toLowerCase();
  if (!q) return raw;
  // naive filter: show only lines containing the query, but keep some context.
  const lines = raw.split("\n");
  const out = [];
  for (let i = 0; i < lines.length; i++) {
    const hit = lines[i].toLowerCase().includes(q);
    if (hit) {
      const start = Math.max(0, i - 2);
      const end = Math.min(lines.length, i + 3);
      for (let j = start; j < end; j++) out.push(lines[j]);
      out.push("  ...");
    }
  }
  return out.length ? out.join("\n") : raw;
}

function renderRecallPayload() {
  document.getElementById("recall-json").textContent = pretty(state.currentRecallPayload || {});
}

function renderEvidenceTab() {
  const list = document.getElementById("evidence-list");
  const detail = document.getElementById("evidence-detail");
  list.innerHTML = "";
  detail.textContent = "";

  const evidences = pickEvidences(state.currentRoundPayload);
  const q = state.search.trim().toLowerCase();
  const filtered = evidences.filter((e) => {
    if (!q) return true;
    const id = String(e.evidence_id || "").toLowerCase();
    const content = String(e.content || "").toLowerCase();
    return id.includes(q) || content.includes(q);
  });

  if (!filtered.length) {
    const empty = document.createElement("div");
    empty.className = "subtle";
    empty.textContent = evidences.length ? "No evidence matched filter." : "No evidences in this round.";
    list.appendChild(empty);
    return;
  }

  filtered.forEach((e, idx) => {
    const card = document.createElement("div");
    card.className = "evidence-card";
    card.dataset.eid = String(e.evidence_id || idx);
    const id = document.createElement("div");
    id.className = "evidence-id";
    id.textContent = String(e.evidence_id || "(no id)");
    const sn = document.createElement("div");
    sn.className = "evidence-snippet";
    sn.textContent = snippet(e.content || "");
    card.appendChild(id);
    card.appendChild(sn);
    card.addEventListener("click", () => {
      Array.from(list.querySelectorAll(".evidence-card")).forEach((n) => n.classList.remove("active"));
      card.classList.add("active");
      detail.textContent = pretty(e);
    });
    list.appendChild(card);
    if (idx === 0) {
      card.classList.add("active");
      detail.textContent = pretty(e);
    }
  });
}

async function selectQuestion(qid) {
  state.currentQid = qid;
  state.currentRoundFile = null;
  state.currentRoundPayload = null;
  state.currentRecallPayload = null;
  renderQuestions();

  const roundsResp = await fetchJson(`/api/questions/${encodeURIComponent(qid)}/rounds`);
  state.rounds = roundsResp.rounds || [];
  renderRounds();

  try {
    state.currentRecallPayload = await fetchJson(`/api/questions/${encodeURIComponent(qid)}/recall`);
  } catch (e) {
    state.currentRecallPayload = { error: String(e) };
  }
  renderRecallPayload();

  if (state.rounds.length) {
    await selectRound(state.rounds[0].file);
  } else {
    renderDetailHeader();
    document.getElementById("round-json").textContent = "{}";
    document.getElementById("actions-json").textContent = "[]";
    document.getElementById("judge-json").textContent = "{}";
    renderEvidenceTab();
  }
}

async function selectRound(roundFile) {
  if (!state.currentQid) return;
  state.currentRoundFile = roundFile;
  renderRounds();
  const payload = await fetchJson(
    `/api/questions/${encodeURIComponent(state.currentQid)}/rounds/${encodeURIComponent(roundFile)}`
  );
  state.currentRoundPayload = payload;
  renderRoundPayload();
}

async function bootstrap() {
  setupTabs();
  const meta = await fetchJson("/api/meta");
  document.getElementById("meta-line").textContent = `recall_root: ${meta.recall_root}`;

  const q = await fetchJson("/api/questions");
  state.questions = q.questions || [];
  renderQuestions();

  const defaultQid = meta.default_question_id || (state.questions[0] && state.questions[0].question_id) || null;
  if (defaultQid) {
    await selectQuestion(defaultQid);
  }

  const search = document.getElementById("search");
  search.addEventListener("input", () => {
    state.search = search.value || "";
    renderQuestions();
    renderRounds();
    renderRoundPayload();
    renderRecallPayload();
  });
}

bootstrap().catch((e) => {
  document.getElementById("meta-line").textContent = `Failed to load: ${String(e)}`;
});

