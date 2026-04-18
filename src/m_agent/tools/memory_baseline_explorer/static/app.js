/* global fetch, document */

function $(id) {
  return document.getElementById(id);
}

async function fetchJson(url) {
  const res = await fetch(url);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const msg = data.error || res.statusText || String(res.status);
    throw new Error(msg);
  }
  return data;
}

function esc(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

let state = {
  dialogues: [],
  overview: null,
  selectedId: null,
  dialoguePayload: null,
  episodesPayload: null,
  narrativePayload: null,
  dialogueToScenes: {},
};

function setTab(name) {
  document.querySelectorAll(".tab").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.tab === name);
  });
  document.querySelectorAll(".tab-pane").forEach((pane) => {
    pane.classList.toggle("active", pane.id === `pane-${name}`);
  });
}

function renderDialogueList(filter) {
  const box = $("dlg-list");
  const q = (filter || "").trim().toLowerCase();
  box.innerHTML = "";
  state.dialogues
    .filter((d) => !q || String(d.dialogue_id).toLowerCase().includes(q))
    .forEach((d) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "dlg-item" + (d.dialogue_id === state.selectedId ? " active" : "");
      btn.innerHTML = `<span>${esc(d.dialogue_id)}</span><small>${esc(
        d.relative_path || ""
      )} · ${d.turn_count} 轮</small>`;
      btn.addEventListener("click", () => selectDialogue(d.dialogue_id));
      box.appendChild(btn);
    });
}

function renderTurns(payload) {
  const meta = $("dlg-meta");
  const turnsBox = $("turns");
  if (!payload || typeof payload !== "object") {
    meta.textContent = "";
    turnsBox.innerHTML = '<p class="err">无法加载对话</p>';
    return;
  }
  const participants = Array.isArray(payload.participants) ? payload.participants : [];
  meta.textContent = `参与者: ${participants.join(", ") || "—"} · dialogue_id: ${payload.dialogue_id || "—"}`;
  const turns = Array.isArray(payload.turns) ? payload.turns : [];
  turnsBox.innerHTML = "";
  turns.forEach((t, i) => {
    const speaker = t.speaker != null ? String(t.speaker) : "";
    const text = t.text != null ? String(t.text) : "";
    const side = i % 2 === 0 ? "left" : "right";
    const div = document.createElement("div");
    div.className = `turn ${side}`;
    div.innerHTML = `<div class="speaker">${esc(speaker)}</div><div>${esc(text)}</div>`;
    turnsBox.appendChild(div);
  });
}

function renderFactItem(f) {
  const atomic = f["Atomic fact"] != null ? String(f["Atomic fact"]) : "";
  const evs = f.evidence_sentence != null ? String(f.evidence_sentence) : "";
  const li = document.createElement("li");
  li.innerHTML = `<span class="fact-atomic">${esc(atomic)}</span>${
    evs ? `<span class="fact-evidence">证据句: ${esc(evs)}</span>` : ""
  }`;
  return li;
}

function renderNarrative(nar) {
  const root = $("narrative-root");
  root.innerHTML = "";
  if (!nar || !nar.scenes) {
    root.innerHTML = '<p class="err">无叙事数据（缺少 scene 或接口错误）</p>';
    return;
  }
  if (!nar.scenes.length) {
    root.innerHTML = "<p>未找到关联 scene，无法展示 facts。</p>";
    return;
  }
  nar.scenes.forEach((sc) => {
    const block = document.createElement("div");
    block.className = "scene-block";
    const h2 = document.createElement("h2");
    h2.textContent = `${sc.scene_id || ""} · ${sc.file || ""} · facts ${sc.facts_total_in_scene ?? 0}`;
    block.appendChild(h2);
    if (sc.theme) {
      const th = document.createElement("div");
      th.className = "subtle";
      th.textContent = `主题: ${sc.theme}`;
      block.appendChild(th);
    }
    if (sc.diary) {
      const diary = document.createElement("div");
      diary.className = "diary-box";
      diary.textContent = sc.diary;
      block.appendChild(diary);
    }
    (sc.episodes || []).forEach((ep) => {
      const et = document.createElement("div");
      et.className = "episode-title";
      et.textContent = `${ep.episode_id || ""} — ${ep.topic || ""} · turn_span ${JSON.stringify(
        ep.turn_span || []
      )}`;
      block.appendChild(et);
      (ep.segments || []).forEach((seg) => {
        const card = document.createElement("div");
        card.className = "segment-card";
        const h4 = document.createElement("h4");
        h4.textContent = `${seg.segment_id || ""} · ${seg.topic || ""} · turn_span ${JSON.stringify(
          seg.turn_span || []
        )}`;
        card.appendChild(h4);
        const tbox = document.createElement("div");
        tbox.className = "segment-turns";
        (seg.turns || []).forEach((t, idx) => {
          const side = idx % 2 === 0 ? "left" : "right";
          const div = document.createElement("div");
          div.className = `turn ${side}`;
          div.innerHTML = `<div class="speaker">${esc(String(t.speaker || ""))}</div><div>${esc(
            String(t.text || "")
          )}</div>`;
          tbox.appendChild(div);
        });
        card.appendChild(tbox);
        const facts = seg.facts || [];
        if (facts.length) {
          const ul = document.createElement("ul");
          ul.className = "fact-list";
          facts.forEach((f) => ul.appendChild(renderFactItem(f)));
          card.appendChild(ul);
        }
        block.appendChild(card);
      });
      const orphans = ep.orphan_facts || [];
      if (orphans.length) {
        const of = document.createElement("div");
        of.className = "orphan-facts";
        of.innerHTML = `<strong>本集未挂到 segment 的 facts</strong><ul class="fact-list"></ul>`;
        const ul = of.querySelector("ul");
        orphans.forEach((f) => ul.appendChild(renderFactItem(f)));
        block.appendChild(of);
      }
    });
    root.appendChild(block);
  });
  const loose = nar.unattached_facts || [];
  if (loose.length) {
    const wrap = document.createElement("div");
    wrap.className = "orphan-facts";
    wrap.innerHTML = `<strong>无法对齐到 episodes 文件的 facts</strong><ul class="fact-list"></ul>`;
    const ul = wrap.querySelector("ul");
    loose.forEach((f) => ul.appendChild(renderFactItem(f)));
    root.appendChild(wrap);
  }
}

function renderOverviewStats(ov) {
  const box = $("overview-stats");
  if (!box || !ov) return;
  const items = [
    ["对话 JSON 数", ov.dialogue_file_count],
    ["Scene 数", ov.scene_file_count],
    ["by_dialogue 目录数", ov.by_dialogue_dir_count],
    ["Scene 内 facts 条数(估算)", ov.facts_in_scenes_total],
    ["facts/*.json 文件", ov.fact_json_file_count],
    ["entity_statement JSON", ov.entity_statement_json_file_count],
    ["facts_situation.json", ov.facts_situation_file_exists ? "存在" : "无"],
  ];
  box.innerHTML = "";
  items.forEach(([k, v]) => {
    const d = document.createElement("div");
    d.innerHTML = `<strong>${esc(k)}</strong><br />${esc(String(v))}`;
    box.appendChild(d);
  });
}

function renderEpisodes(episodesPayload) {
  const box = $("episodes-tree");
  box.innerHTML = "";
  if (!episodesPayload || typeof episodesPayload !== "object") {
    box.innerHTML = '<p class="err">无 episodes 数据</p>';
    return;
  }
  if (episodesPayload.episodes_error === "file_missing") {
    box.innerHTML = `<p class="err">未找到 episodes_v1.json</p>`;
    return;
  }
  const doc = episodesPayload.episodes;
  const pre = document.createElement("pre");
  pre.className = "json-block";
  pre.textContent = JSON.stringify(doc != null ? doc : {}, null, 2);
  box.appendChild(pre);
}

function renderEligibility(episodesBundle) {
  const box = $("eligibility-table");
  box.innerHTML = "";
  const el = episodesBundle && episodesBundle.eligibility;
  if (!el || typeof el !== "object") {
    box.innerHTML = `<p class="err">无 eligibility 数据 (${esc(
      episodesBundle && episodesBundle.eligibility_error
        ? episodesBundle.eligibility_error
        : "missing"
    )})</p>`;
    return;
  }
  const rows = Array.isArray(el.results) ? el.results : [];
  if (!rows.length) {
    box.innerHTML = "<p>eligibility.results 为空</p>";
    return;
  }
  const keys = Object.keys(rows[0] || {});
  const table = document.createElement("table");
  table.className = "data";
  const thead = document.createElement("thead");
  thead.innerHTML = `<tr>${keys.map((k) => `<th>${esc(k)}</th>`).join("")}</tr>`;
  table.appendChild(thead);
  const tbody = document.createElement("tbody");
  rows.forEach((r) => {
    const tr = document.createElement("tr");
    tr.innerHTML = keys.map((k) => `<td>${esc(JSON.stringify(r[k]))}</td>`).join("");
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  box.appendChild(table);
}

function renderRelatedScenes(dialogueId) {
  const rel = $("related-scenes");
  const jsonBox = $("scene-json");
  rel.innerHTML = "";
  jsonBox.textContent = "";
  const hits = state.dialogueToScenes[dialogueId] || [];
  if (!hits.length) {
    rel.innerHTML = "<p>未索引到关联 scene（可检查 scene JSON 内 dialogue_id）</p>";
    return;
  }
  hits.forEach((h) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "scene-link";
    b.textContent = `${h.scene_id || h.file}`;
    b.addEventListener("click", () => loadSceneDetail(h.file));
    rel.appendChild(b);
  });
}

async function loadSceneDetail(fileOrStem) {
  const jsonBox = $("scene-json");
  jsonBox.textContent = "加载中…";
  try {
    const stem = String(fileOrStem).replace(/\.json$/i, "");
    const data = await fetchJson(`/api/scenes/${encodeURIComponent(stem)}`);
    jsonBox.textContent = JSON.stringify(data.data, null, 2);
  } catch (e) {
    jsonBox.textContent = "";
    jsonBox.innerHTML = `<span class="err">${esc(e.message)}</span>`;
  }
}

async function selectDialogue(dialogueId) {
  state.selectedId = dialogueId;
  renderDialogueList($("dlg-filter").value);
  try {
    const [d, ep, nar] = await Promise.all([
      fetchJson(`/api/dialogues/${encodeURIComponent(dialogueId)}`),
      fetchJson(`/api/dialogues/${encodeURIComponent(dialogueId)}/episodes`),
      fetchJson(`/api/dialogues/${encodeURIComponent(dialogueId)}/narrative`),
    ]);
    state.dialoguePayload = d;
    state.episodesPayload = ep;
    state.narrativePayload = nar;
    renderTurns(d);
    renderNarrative(nar);
    renderEpisodes(ep);
    renderEligibility(ep);
    renderRelatedScenes(dialogueId);
  } catch (e) {
    state.dialoguePayload = null;
    state.episodesPayload = null;
    state.narrativePayload = null;
    $("dlg-meta").textContent = "";
    $("turns").innerHTML = `<p class="err">${esc(e.message)}</p>`;
    $("narrative-root").innerHTML = "";
    $("episodes-tree").innerHTML = "";
    $("eligibility-table").innerHTML = "";
    $("related-scenes").innerHTML = "";
    $("scene-json").textContent = "";
  }
}

async function boot() {
  try {
    const [meta, overview, dlgList, scenesMeta] = await Promise.all([
      fetchJson("/api/meta"),
      fetchJson("/api/overview"),
      fetchJson("/api/dialogues"),
      fetchJson("/api/scenes"),
    ]);
    state.overview = overview;
    state.dialogues = dlgList.dialogues || [];
    state.dialogueToScenes = scenesMeta.dialogue_to_scenes || {};
    $("header-meta").textContent = `${meta.workflow_id} · ${meta.memory_root}`;
    renderOverviewStats(overview);
    $("overview-json").textContent = JSON.stringify(overview, null, 2);
  } catch (e) {
    $("header-meta").textContent = "加载失败: " + e.message;
  }
  renderDialogueList("");
}

document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => setTab(btn.dataset.tab));
});

$("dlg-filter").addEventListener("input", (ev) => {
  renderDialogueList(ev.target.value);
});

boot();
