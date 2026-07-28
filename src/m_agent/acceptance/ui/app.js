"use strict";

const API_ROOT = "/api";
const EXPECTED_INVARIANT_COUNT = 14;
const POLL_INTERVAL_MS = 900;
const REQUEST_TIMEOUT_MS = 15_000;
const LAST_RUN_STORAGE_KEY = "m-agent-acceptance-last-run";

const DOMAIN_CONFIG = [
  {
    id: "outer_runtime",
    order: 1,
    title: "外层入口与调度",
    subtitle: "刺激 admission、单 drainer 与安全抢占",
    fallbackIds: ["INV-01", "INV-02", "INV-12"],
  },
  {
    id: "transaction_state",
    order: 2,
    title: "事务归因与状态",
    subtitle: "feedback 归属、双键校验与 transaction 隔离",
    fallbackIds: ["INV-03", "INV-04", "INV-05"],
  },
  {
    id: "reasoning_execution",
    order: 3,
    title: "规划、执行与完成",
    subtitle: "Thinking / Execution 边界、工具审计与完成门禁",
    fallbackIds: ["INV-07", "INV-08", "INV-09", "INV-10", "INV-11"],
  },
  {
    id: "scene_memory",
    order: 4,
    title: "Scene 与记忆生命周期",
    subtitle: "单一时间线与安全 flush watermark",
    fallbackIds: ["INV-06", "INV-14"],
  },
  {
    id: "recovery_idempotency",
    order: 5,
    title: "恢复与幂等",
    subtitle: "feedback 重放与副作用去重",
    fallbackIds: ["INV-13"],
  },
];

const DOMAIN_IDS = new Set(DOMAIN_CONFIG.map((item) => item.id));
const FALLBACK_DOMAIN_BY_INVARIANT = new Map(
  DOMAIN_CONFIG.flatMap((group) =>
    group.fallbackIds.map((invariantId) => [invariantId, group.id]),
  ),
);

const ACTIVE_STATUSES = new Set([
  "queued",
  "pending",
  "starting",
  "running",
  "cancelling",
  "canceling",
]);
const TERMINAL_STATUSES = new Set([
  "completed",
  "complete",
  "passed",
  "success",
  "succeeded",
  "failed",
  "error",
  "cancelled",
  "canceled",
  "timed_out",
  "timeout",
  "interrupted",
  "known_gaps",
  "incomplete",
]);
const GAP_STATUSES = new Set(["known_gap", "known_gaps", "xfailed"]);
const FAILURE_STATUSES = new Set([
  "failed",
  "error",
  "xpassed",
  "timed_out",
  "timeout",
  "incomplete",
]);
const DONE_CASE_STATUSES = new Set([
  "passed",
  "success",
  "succeeded",
  "failed",
  "error",
  "skipped",
  "xfailed",
  "xpassed",
  "cancelled",
  "canceled",
  "timed_out",
  "timeout",
  "known_gap",
  "not_run",
]);

const STATUS_LABELS = {
  idle: "待运行",
  queued: "已排队",
  pending: "等待中",
  starting: "启动中",
  running: "运行中",
  cancelling: "取消中",
  canceling: "取消中",
  completed: "已完成",
  complete: "已完成",
  passed: "通过",
  success: "通过",
  succeeded: "通过",
  failed: "失败",
  error: "错误",
  skipped: "跳过",
  xfailed: "Known Gap",
  xpassed: "XPASS",
  cancelled: "已取消",
  canceled: "已取消",
  timed_out: "超时",
  timeout: "超时",
  interrupted: "已中断",
  known_gap: "Known Gap",
  known_gaps: "存在 Known Gap",
  incomplete: "结果不完整",
  not_run: "未运行",
};

const RISK_LABELS = {
  critical: "极高",
  high: "高",
  medium: "中",
  low: "低",
  unknown: "未标注",
  极高: "极高",
  高: "高",
  中: "中",
  低: "低",
};

const ARTIFACT_LABELS = {
  summary: "Summary JSON",
  pytest: "Pytest JSON",
  junit: "JUnit XML",
  stdout: "stdout",
  stderr: "stderr",
};

const state = {
  catalog: null,
  groups: [],
  invariants: [],
  selected: new Set(),
  profile: "gate",
  run: null,
  loadingCatalog: false,
  startingRun: false,
  cancelRequested: false,
  pollGeneration: 0,
  pollFailures: 0,
  toastTimer: null,
  expandedGroups: new Set(["outer_runtime"]),
  openInvariants: new Set(),
  arrangedRunId: "",
  filters: {
    problemsOnly: false,
    status: "all",
    group: "all",
  },
};

const elements = {};

document.addEventListener("DOMContentLoaded", () => {
  captureElements();
  bindEvents();
  loadCatalog();
});

function captureElements() {
  [
    "verdictPanel",
    "verdictKicker",
    "verdictTitle",
    "verdictBadge",
    "verdictDescription",
    "metricSemantics",
    "metricCases",
    "metricCasesLabel",
    "metricPassed",
    "metricAttention",
    "catalogState",
    "suiteTitle",
    "suiteMeta",
    "profileGate",
    "profileFull",
    "gateProfileCount",
    "fullProfileCount",
    "selectionCount",
    "selectedCaseCount",
    "selectAllButton",
    "clearSelectionButton",
    "runButton",
    "runButtonLabel",
    "cancelButton",
    "controlHint",
    "runState",
    "idleRunState",
    "activeRunState",
    "runIdValue",
    "copyRunIdButton",
    "progressValue",
    "progressMeta",
    "progressTrack",
    "progressBar",
    "runProfileValue",
    "completedCasesValue",
    "runUpdatedAt",
    "artifactLinks",
    "matrixSubtitle",
    "visibleSummary",
    "problemFilter",
    "statusFilter",
    "groupFilter",
    "clearFiltersButton",
    "retryCatalogButton",
    "catalogError",
    "matrixSkeleton",
    "invariantList",
    "noFilterResults",
    "diagnosticsCount",
    "diagnosticsEmpty",
    "diagnosticsList",
    "diagnosticsSection",
    "toast",
  ].forEach((id) => {
    elements[id] = document.getElementById(id);
  });
}

function bindEvents() {
  elements.profileGate.addEventListener("change", handleProfileChange);
  elements.profileFull.addEventListener("change", handleProfileChange);
  elements.selectAllButton.addEventListener("click", selectAll);
  elements.clearSelectionButton.addEventListener("click", clearSelection);
  elements.runButton.addEventListener("click", () => startRun());
  elements.cancelButton.addEventListener("click", cancelRun);
  elements.retryCatalogButton.addEventListener("click", loadCatalog);
  elements.copyRunIdButton.addEventListener("click", copyRunId);
  elements.problemFilter.addEventListener("change", () => {
    state.filters.problemsOnly = elements.problemFilter.checked;
    renderDomainTree();
  });
  elements.statusFilter.addEventListener("change", () => {
    state.filters.status = elements.statusFilter.value;
    renderDomainTree();
  });
  elements.groupFilter.addEventListener("change", () => {
    state.filters.group = elements.groupFilter.value;
    if (state.filters.group !== "all") {
      state.expandedGroups.add(state.filters.group);
    }
    renderDomainTree();
  });
  elements.clearFiltersButton.addEventListener("click", clearFilters);
}

async function requestJson(path, options = {}) {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  const headers = {
    Accept: "application/json",
    ...(options.headers || {}),
  };
  if (options.body !== undefined) headers["Content-Type"] = "application/json";

  try {
    const response = await fetch(`${API_ROOT}${path}`, {
      ...options,
      headers,
      signal: controller.signal,
      credentials: "same-origin",
      cache: "no-store",
    });
    const raw = await response.text();
    let payload = {};
    if (raw) {
      try {
        payload = JSON.parse(raw);
      } catch {
        payload = { message: raw };
      }
    }
    if (!response.ok) {
      const message =
        payload.detail ||
        payload.error ||
        payload.message ||
        `${response.status} ${response.statusText}`;
      throw new Error(String(message));
    }
    return payload;
  } catch (error) {
    if (error && error.name === "AbortError") {
      throw new Error("请求超时，请确认本地验收服务仍在运行。");
    }
    throw error;
  } finally {
    window.clearTimeout(timeout);
  }
}

async function loadCatalog() {
  if (state.loadingCatalog) return;
  state.loadingCatalog = true;
  elements.catalogError.hidden = true;
  elements.retryCatalogButton.hidden = true;
  elements.matrixSkeleton.hidden = false;
  elements.invariantList.replaceChildren();
  elements.catalogState.textContent = "读取目录";
  elements.catalogState.className = "quiet-badge";
  updateControls();

  try {
    const payload = await requestJson("/catalog");
    state.catalog = payload;
    state.invariants = asArray(payload.invariants)
      .map(normalizeInvariant)
      .sort((left, right) => left.order - right.order);
    state.groups = normalizeGroups(payload.groups);
    state.selected = new Set(state.invariants.map((item) => item.id));
    elements.catalogState.textContent = "目录就绪";
    elements.catalogState.className = "quiet-badge quiet-badge-success";
    populateGroupFilter();
    renderCatalogMetadata();
    renderAll();
    restoreLastRun();
  } catch (error) {
    state.catalog = null;
    state.groups = [];
    state.invariants = [];
    state.selected.clear();
    elements.catalogState.textContent = "读取失败";
    elements.catalogState.className = "quiet-badge quiet-badge-danger";
    elements.catalogError.textContent = `无法读取验收目录：${messageOf(error)}`;
    elements.catalogError.hidden = false;
    elements.retryCatalogButton.hidden = false;
    elements.suiteTitle.textContent = "语义目录不可用";
    elements.suiteMeta.textContent = "请确认本地验收 API 已启动";
    renderVerdict();
    showToast(messageOf(error), "error");
  } finally {
    state.loadingCatalog = false;
    elements.matrixSkeleton.hidden = true;
    updateSelectionSummary();
    updateControls();
  }
}

function normalizeGroups(rawGroups) {
  const backend = new Map();
  asArray(rawGroups).forEach((raw, index) => {
    if (!isObject(raw)) return;
    const id = cleanText(raw.id || raw.group_id || raw.domain_id);
    if (!DOMAIN_IDS.has(id)) return;
    backend.set(id, {
      id,
      order: finiteNumber(raw.order, index + 1),
      title: cleanText(raw.title || raw.name),
      subtitle: cleanText(raw.subtitle || raw.description),
      counts: isObject(raw.counts) ? raw.counts : {},
    });
  });
  return DOMAIN_CONFIG.map((fallback) => ({
    ...fallback,
    ...(backend.get(fallback.id) || {}),
    title: backend.get(fallback.id)?.title || fallback.title,
    subtitle: backend.get(fallback.id)?.subtitle || fallback.subtitle,
  })).sort((left, right) => left.order - right.order);
}

function normalizeInvariant(item, index) {
  const source = isObject(item) ? item : {};
  const id = cleanText(source.id || source.invariant_id) ||
    `INV-${String(index + 1).padStart(2, "0")}`;
  const genericTests = asArray(source.tests).map((test, testIndex) =>
    normalizeEvidence(test, testIndex, "gate"),
  );
  let gateTests = asArray(source.gate_tests).map((test, testIndex) =>
    normalizeEvidence(test, testIndex, "gate"),
  );
  let supportingTests = asArray(source.supporting_tests).map((test, testIndex) =>
    normalizeEvidence(test, testIndex, "supporting"),
  );
  if (gateTests.length === 0 && genericTests.length) {
    gateTests = genericTests.filter(
      (test) => !["full", "supporting"].includes(test.profile),
    );
    supportingTests = uniqueEvidence([
      ...supportingTests,
      ...genericTests.filter((test) =>
        ["full", "supporting"].includes(test.profile),
      ),
    ]);
  }
  if (gateTests.length === 0 && genericTests.length) gateTests = genericTests;

  const layer = cleanText(source.layer) || "runtime";
  const requestedGroup = cleanText(
    source.group_id || source.domain_id || source.domain,
  );
  const groupId = DOMAIN_IDS.has(requestedGroup)
    ? requestedGroup
    : fallbackGroupFor(id, layer);

  return {
    ...source,
    id,
    order: finiteNumber(source.order, index + 1),
    groupId,
    title: cleanText(source.title) || "未命名语义不变量",
    principle: descriptionOf(source.principle) || "尚未提供原则说明。",
    acceptance:
      descriptionOf(source.acceptance || source.acceptance_criteria) ||
      "尚未提供验收条件。",
    layer,
    risk: cleanText(source.risk_level || source.risk).toLowerCase() || "unknown",
    riskDescription:
      descriptionOf(source.risk_description) ||
      (RISK_LABELS[cleanText(source.risk)] ? "" : descriptionOf(source.risk)),
    knownGap: descriptionOf(source.known_gap),
    gateTests: uniqueEvidence(gateTests),
    supportingTests: uniqueEvidence(supportingTests),
    counts: isObject(source.counts) ? source.counts : {},
  };
}

function normalizeEvidence(test, index, defaultProfile) {
  if (typeof test === "string") {
    return {
      nodeid: test,
      proves: humanizeNodeid(test),
      profile: defaultProfile,
      invariantIds: [],
    };
  }
  const source = isObject(test) ? test : {};
  const nodeid = cleanText(source.nodeid || source.id || source.name) ||
    `case-${index + 1}`;
  return {
    ...source,
    nodeid,
    proves:
      descriptionOf(source.proves || source.title || source.label) ||
      humanizeNodeid(nodeid),
    profile: cleanText(source.profile).toLowerCase() || defaultProfile,
    invariantIds: asArray(source.invariant_ids).map(cleanText).filter(Boolean),
  };
}

function uniqueEvidence(items) {
  const seen = new Set();
  return items.filter((item) => {
    if (seen.has(item.nodeid)) return false;
    seen.add(item.nodeid);
    return true;
  });
}

function fallbackGroupFor(invariantId, layer) {
  const mapped = FALLBACK_DOMAIN_BY_INVARIANT.get(invariantId);
  if (mapped) return mapped;
  const value = cleanText(layer).toLowerCase();
  if (/(recover|idempot|重放|幂等)/.test(value)) return "recovery_idempotency";
  if (/(scene|flush|memory|记忆)/.test(value)) return "scene_memory";
  if (/(think|execution|completion|规划|执行)/.test(value)) {
    return "reasoning_execution";
  }
  if (/(attribution|transaction|归因|事务)/.test(value)) {
    return "transaction_state";
  }
  return "outer_runtime";
}

function renderAll() {
  renderProfileCounts();
  updateSelectionSummary();
  renderVerdict();
  renderRun();
  renderDomainTree();
  renderDiagnostics();
  updateControls();
}

function renderCatalogMetadata() {
  const count = state.invariants.length;
  elements.suiteTitle.textContent =
    cleanText(state.catalog && (state.catalog.title || state.catalog.suite)) ||
    "Phase 0 语义基线";
  elements.suiteMeta.textContent =
    descriptionOf(state.catalog && state.catalog.description) ||
    `${count} 项运行时语义不变量`;
  elements.matrixSubtitle.textContent =
    count === EXPECTED_INVARIANT_COUNT
      ? "5 个领域组覆盖全部 14 项关键语义；展开不变量可查看验收契约、证据与 trace。"
      : `目录返回 ${count} 项，和计划要求的 ${EXPECTED_INVARIANT_COUNT} 项不一致。`;
}

function renderProfileCounts() {
  const gate = profileSummary("gate");
  const full = profileSummary("full");
  elements.gateProfileCount.textContent =
    `${gate.invariants} 语义 / ${gate.cases} 用例`;
  elements.fullProfileCount.textContent =
    `${full.invariants} 语义 / ${full.cases} 用例`;
}

function profileSummary(profile) {
  const derivedCases = uniqueProfileNodeids(state.invariants, profile).size;
  const raw = isObject(state.catalog && state.catalog.profile_counts)
    ? state.catalog.profile_counts[profile]
    : null;
  const cases = readCount(raw, ["cases", "case_count", "tests", "test_count"]);
  const invariants = readCount(raw, [
    "invariants",
    "invariant_count",
    "semantics",
    "semantic_count",
  ]);
  return {
    invariants: invariants ?? state.invariants.length,
    cases: cases ?? derivedCases,
  };
}

function populateGroupFilter() {
  const current = state.filters.group;
  const options = [createOption("all", "全部领域")];
  state.groups.forEach((group) => {
    options.push(createOption(group.id, group.title));
  });
  elements.groupFilter.replaceChildren(...options);
  elements.groupFilter.value = state.groups.some((group) => group.id === current)
    ? current
    : "all";
}

function createOption(value, label) {
  const option = document.createElement("option");
  option.value = value;
  option.textContent = label;
  return option;
}

function handleProfileChange(event) {
  if (!event.target.checked || isRunActive()) return;
  applyProfile(event.target.value);
  renderAll();
}

function applyProfile(profile) {
  state.profile = profile === "full" ? "full" : "gate";
  elements.profileGate.checked = state.profile === "gate";
  elements.profileFull.checked = state.profile === "full";
  elements.controlHint.textContent =
    state.profile === "full"
      ? "Semantic Full 包含 Gate 与 Supporting 证据，适合里程碑验收。"
      : "Gate 聚焦 14 项关键路径语义的最小阻断证据。";
  elements.runButtonLabel.textContent =
    state.profile === "full" ? "运行 Semantic Full" : "运行 Gate";
}

function selectAll() {
  if (isRunActive()) return;
  state.selected = new Set(state.invariants.map((item) => item.id));
  selectionChanged();
}

function clearSelection() {
  if (isRunActive()) return;
  state.selected.clear();
  selectionChanged();
}

function selectOnly(invariantIds) {
  if (isRunActive()) return;
  state.selected = new Set(asArray(invariantIds));
  selectionChanged();
}

function toggleGroupSelection(groupId, checked) {
  if (isRunActive()) return;
  invariantsInGroup(groupId).forEach((invariant) => {
    if (checked) state.selected.add(invariant.id);
    else state.selected.delete(invariant.id);
  });
  selectionChanged();
}

function selectionChanged() {
  updateSelectionSummary();
  renderVerdict();
  renderDomainTree();
  updateControls();
}

function updateSelectionSummary() {
  const total = state.invariants.length || EXPECTED_INVARIANT_COUNT;
  const selectedInvariants = state.invariants.filter((item) =>
    state.selected.has(item.id),
  );
  const cases = selectedProfileCaseCount(selectedInvariants, state.profile);
  const profileLabel = state.profile === "full" ? "Semantic Full" : "Gate";
  elements.selectionCount.textContent =
    `${state.selected.size} / ${total} 项语义`;
  elements.selectedCaseCount.textContent = `${cases} 个 ${profileLabel} 用例`;
}

async function runGroup(groupId) {
  if (isRunActive()) return;
  const ids = invariantsInGroup(groupId).map((item) => item.id);
  state.selected = new Set(ids);
  selectionChanged();
  await startRun(ids);
}

async function startRun(explicitInvariantIds = null) {
  if (state.startingRun || isRunActive()) return;
  const ids = explicitInvariantIds
    ? asArray(explicitInvariantIds)
    : Array.from(state.selected);
  if (ids.length === 0) {
    showToast("请至少选择一项语义不变量。", "warning");
    return;
  }

  state.startingRun = true;
  state.cancelRequested = false;
  elements.runButtonLabel.textContent = "正在创建运行…";
  updateControls();

  try {
    const payload = await requestJson("/runs", {
      method: "POST",
      body: JSON.stringify({
        invariant_ids: ids,
        profile: state.profile,
      }),
    });
    const runId = cleanText(payload.run_id);
    if (!runId) throw new Error("运行器未返回 run_id。");
    state.run = {
      ...payload,
      run_id: runId,
      profile: payload.profile || state.profile,
      status: payload.status || "queued",
    };
    state.arrangedRunId = "";
    storeLastRunId(runId);
    state.pollFailures = 0;
    renderAll();
    beginPolling(runId);
    showToast(`验收运行 ${runId} 已创建。`, "success");
  } catch (error) {
    showToast(`启动失败：${messageOf(error)}`, "error");
  } finally {
    state.startingRun = false;
    elements.runButtonLabel.textContent =
      state.profile === "full" ? "运行 Semantic Full" : "运行 Gate";
    updateControls();
  }
}

function beginPolling(runId) {
  const generation = ++state.pollGeneration;
  pollRun(runId, generation);
}

async function pollRun(runId, generation) {
  if (generation !== state.pollGeneration) return;
  try {
    const payload = await requestJson(`/runs/${encodeURIComponent(runId)}`);
    if (generation !== state.pollGeneration) return;
    if (cleanText(payload.run_id) && cleanText(payload.run_id) !== runId) {
      throw new Error("运行器返回了不匹配的 run_id。");
    }
    state.run = { ...state.run, ...payload, run_id: runId };
    state.pollFailures = 0;
    renderAll();

    if (!isTerminalStatus(statusOf(state.run))) {
      window.setTimeout(() => pollRun(runId, generation), POLL_INTERVAL_MS);
    } else {
      state.pollGeneration += 1;
      state.cancelRequested = false;
      updateControls();
    }
  } catch (error) {
    if (generation !== state.pollGeneration) return;
    state.pollFailures += 1;
    if (state.pollFailures <= 3) {
      elements.progressMeta.textContent =
        `状态同步暂时中断，正在重试（${state.pollFailures}/3）`;
      window.setTimeout(
        () => pollRun(runId, generation),
        POLL_INTERVAL_MS * state.pollFailures,
      );
    } else {
      state.pollGeneration += 1;
      elements.progressMeta.textContent = "状态同步已停止";
      showToast(`无法读取运行状态：${messageOf(error)}`, "error");
      updateControls();
    }
  }
}

async function cancelRun() {
  const runId = cleanText(state.run && state.run.run_id);
  if (!runId || !isRunActive() || state.cancelRequested) return;
  state.cancelRequested = true;
  elements.cancelButton.textContent = "正在取消…";
  updateControls();

  try {
    const payload = await requestJson(
      `/runs/${encodeURIComponent(runId)}/cancel`,
      { method: "POST" },
    );
    state.run = {
      ...state.run,
      ...payload,
      run_id: runId,
      status: payload.status || "cancelling",
    };
    renderAll();
    showToast("取消请求已提交，等待运行器确认。", "warning");
  } catch (error) {
    state.cancelRequested = false;
    showToast(`取消失败：${messageOf(error)}`, "error");
  } finally {
    elements.cancelButton.textContent = "取消运行";
    updateControls();
  }
}

async function restoreLastRun() {
  let runId = "";
  try {
    runId = cleanText(window.sessionStorage.getItem(LAST_RUN_STORAGE_KEY));
  } catch {
    return;
  }
  if (!runId || state.run) return;

  try {
    const payload = await requestJson(`/runs/${encodeURIComponent(runId)}`);
    state.run = { ...payload, run_id: runId };
    applyProfile(payload.profile);
    const restoredIds = asArray(payload.invariant_ids).filter((id) =>
      state.invariants.some((invariant) => invariant.id === id),
    );
    if (restoredIds.length) state.selected = new Set(restoredIds);
    renderAll();
    if (!isTerminalStatus(statusOf(state.run))) beginPolling(runId);
  } catch {
    try {
      window.sessionStorage.removeItem(LAST_RUN_STORAGE_KEY);
    } catch {
      // Storage is an optional convenience only.
    }
  }
}

function storeLastRunId(runId) {
  try {
    window.sessionStorage.setItem(LAST_RUN_STORAGE_KEY, runId);
  } catch {
    // Storage is an optional convenience only.
  }
}

function renderVerdict() {
  if (!state.catalog) {
    setVerdict(
      "ready",
      "Phase 0 · Catalog",
      state.loadingCatalog ? "载入关键语义目录…" : "语义目录不可用",
      state.loadingCatalog ? "准备中" : "读取失败",
      "结果会按“领域 → 语义不变量 → 测试证据”逐层汇总。",
    );
    setVerdictMetrics("—", "—", "—", "—", "Gate 用例");
    return;
  }

  const run = state.run;
  if (!run) {
    const selected = state.invariants.filter((item) =>
      state.selected.has(item.id),
    );
    const cases = selectedProfileCaseCount(selected, state.profile);
    const profileLabel = state.profile === "full" ? "Semantic Full" : "Gate";
    setVerdict(
      "ready",
      `Phase 0 · ${profileLabel}`,
      "关键语义验收已就绪",
      "READY",
      `已选择 ${selected.length}/${state.invariants.length} 项语义、${cases} 个用例。运行后将给出是否阻断 M0 的明确结论。`,
    );
    setVerdictMetrics(
      String(selected.length),
      String(cases),
      "—",
      "—",
      `${profileLabel} 用例`,
    );
    return;
  }

  const rawStatus = statusOf(run);
  const runProfile = cleanText(run.profile).toLowerCase() === "full"
    ? "Semantic Full"
    : "Gate";
  const invariantResults = getInvariantResults();
  const cases = getCases();
  const invariantCounts = categoryCounts(invariantResults);
  const caseCounts = categoryCounts(cases);
  const runScope = asArray(run.invariant_ids);
  const expectedSemantics = runScope.length || invariantResults.length;
  const passedCases = caseCounts.passed;
  const attention = `${invariantCounts.gap}/${invariantCounts.failed}`;

  if (isRunActive()) {
    setVerdict(
      "running",
      `Phase 0 · ${runProfile}`,
      "语义证据正在采集",
      "RUNNING",
      "pytest 子进程完成前进度为不确定状态；结果归档后会自动展开异常领域。",
    );
    setVerdictMetrics(
      String(expectedSemantics),
      cases.length ? `${passedCases}/${cases.length}` : "采集中",
      String(invariantCounts.passed),
      attention,
      "用例通过",
    );
    return;
  }

  if (invariantCounts.failed > 0 || FAILURE_STATUSES.has(rawStatus)) {
    setVerdict(
      "failed",
      `Phase 0 · ${runProfile}`,
      "验收出现新增失败",
      "FAILED",
      verdictResultLine(invariantCounts, caseCounts, cases.length),
    );
  } else if (invariantCounts.gap > 0 || GAP_STATUSES.has(rawStatus)) {
    setVerdict(
      "blocked",
      `Phase 0 · ${runProfile}`,
      "M0 BLOCKED",
      "KNOWN GAPS",
      verdictResultLine(invariantCounts, caseCounts, cases.length),
    );
  } else if (
    ["cancelled", "canceled", "interrupted"].includes(rawStatus)
  ) {
    setVerdict(
      "ready",
      `Phase 0 · ${runProfile}`,
      "运行未完成",
      "CANCELLED",
      "本次运行被取消或中断，不能作为 M0 验收证据。",
    );
  } else {
    const completeScope = expectedSemantics === EXPECTED_INVARIANT_COUNT;
    setVerdict(
      "passed",
      `Phase 0 · ${runProfile}`,
      completeScope ? "关键语义验收通过" : "所选语义范围通过",
      completeScope ? "M0 READY" : "SCOPE PASSED",
      verdictResultLine(invariantCounts, caseCounts, cases.length),
    );
  }
  setVerdictMetrics(
    String(expectedSemantics),
    cases.length ? `${passedCases}/${cases.length}` : "—",
    String(invariantCounts.passed),
    attention,
    "用例通过",
  );
}

function verdictResultLine(invariants, cases, caseTotal) {
  return [
    `${invariants.passed}/${invariants.total} 语义通过`,
    `${cases.passed}/${caseTotal || cases.total} 用例通过`,
    `${invariants.gap} Known Gap`,
    `${invariants.failed} 新增失败`,
  ].join(" · ");
}

function setVerdict(tone, kicker, title, badge, description) {
  elements.verdictPanel.className = `verdict-panel verdict-${tone}`;
  elements.verdictKicker.textContent = kicker;
  elements.verdictTitle.textContent = title;
  elements.verdictBadge.textContent = badge;
  elements.verdictDescription.textContent = description;
}

function setVerdictMetrics(semantics, cases, passed, attention, caseLabel) {
  elements.metricSemantics.textContent = semantics;
  elements.metricCases.textContent = cases;
  elements.metricPassed.textContent = passed;
  elements.metricAttention.textContent = attention;
  elements.metricCasesLabel.textContent = caseLabel;
}

function renderRun() {
  const run = state.run;
  if (!run) {
    elements.idleRunState.hidden = false;
    elements.activeRunState.hidden = true;
    setStatusChip(elements.runState, "idle");
    return;
  }

  const rawStatus = statusOf(run) || "queued";
  const progress = deriveProgress(run);
  elements.idleRunState.hidden = true;
  elements.activeRunState.hidden = false;
  elements.runIdValue.textContent = cleanText(run.run_id) || "—";
  elements.runProfileValue.textContent =
    cleanText(run.profile).toLowerCase() === "full" ? "Semantic Full" : "Gate";
  elements.completedCasesValue.textContent =
    `${progress.completed} / ${progress.total || "?"}`;
  elements.progressValue.textContent = progress.indeterminate
    ? "···"
    : `${progress.percent}%`;
  elements.progressMeta.textContent = progress.label;
  elements.progressBar.style.width = `${progress.percent}%`;
  elements.progressTrack.setAttribute("aria-valuenow", String(progress.percent));
  elements.progressTrack.classList.toggle(
    "is-indeterminate",
    progress.indeterminate,
  );
  const result = runResultPayload();
  const duration = durationText(result);
  elements.runUpdatedAt.textContent =
    duration ||
    formatTimestamp(
      result.finished_at ||
      result.started_at ||
      run.updated_at ||
      run.created_at,
    );
  setStatusChip(elements.runState, rawStatus);
  renderArtifacts();
}

function deriveProgress(run) {
  const cases = getCases(run);
  const invariantResults = getInvariantResults(run);
  const source = cases.length ? cases : invariantResults;
  const total = source.length;
  const completed = source.filter((item) =>
    DONE_CASE_STATUSES.has(statusOf(item)),
  ).length;
  const status = statusOf(run);
  const terminal = isTerminalStatus(status);
  const percent =
    total > 0
      ? Math.round((completed / total) * 100)
      : terminal
        ? 100
        : 12;
  let label = total > 0
    ? `${completed} / ${total} 条结果已归档`
    : "等待 pytest 子进程返回结果";
  if (terminal) label = `运行${STATUS_LABELS[status] || "已结束"}`;
  if (["cancelling", "canceling"].includes(status)) {
    label = "正在等待子进程安全退出";
  }
  return {
    total,
    completed,
    percent: Math.max(0, Math.min(100, percent)),
    label,
    indeterminate: !terminal && total === 0,
  };
}

function renderArtifacts() {
  const result = runResultPayload();
  const artifacts = isObject(result.artifacts) ? result.artifacts : {};
  const runId = cleanText(state.run && state.run.run_id);
  if (!runId || Object.keys(artifacts).length === 0) {
    elements.artifactLinks.hidden = true;
    elements.artifactLinks.replaceChildren();
    return;
  }
  const links = [];
  Object.entries(artifacts).forEach(([key, value]) => {
    const artifactName = cleanText(value || key);
    if (!artifactName) return;
    const link = createElement("a", "artifact-link");
    link.href =
      `${API_ROOT}/runs/${encodeURIComponent(runId)}/artifacts/` +
      encodeURIComponent(artifactName);
    link.textContent = ARTIFACT_LABELS[key] || key;
    link.setAttribute("download", "");
    links.push(link);
  });
  elements.artifactLinks.replaceChildren(...links);
  elements.artifactLinks.hidden = links.length === 0;
}

function renderDomainTree() {
  if (!state.catalog) {
    elements.invariantList.replaceChildren();
    elements.visibleSummary.textContent = "目录不可用";
    return;
  }
  autoArrangeProblemGroups();
  renderMatrixContext();
  const fragment = document.createDocumentFragment();
  let visibleGroups = 0;
  let visibleInvariants = 0;
  let visibleCases = 0;

  state.groups.forEach((group) => {
    if (state.filters.group !== "all" && state.filters.group !== group.id) return;
    const allMembers = invariantsInGroup(group.id);
    const visibleMembers = allMembers.filter(invariantMatchesFilters);
    if (visibleMembers.length === 0) return;
    visibleGroups += 1;
    visibleInvariants += visibleMembers.length;
    visibleCases += selectedProfileCaseCount(visibleMembers, state.profile);
    fragment.appendChild(createDomainGroup(group, allMembers, visibleMembers));
  });

  elements.invariantList.replaceChildren(fragment);
  elements.noFilterResults.hidden = visibleInvariants !== 0;
  const profileLabel = state.profile === "full" ? "Semantic Full" : "Gate";
  elements.visibleSummary.textContent =
    `${visibleGroups} 领域 · ${visibleInvariants} 语义 · ` +
    `${visibleCases} ${profileLabel} 用例`;
}

function renderMatrixContext() {
  const profileLabel = state.profile === "full" ? "Semantic Full" : "Gate";
  const profileCases = selectedProfileCaseCount(state.invariants, state.profile);
  const runId = cleanText(state.run && state.run.run_id);
  if (!runId) {
    elements.matrixSubtitle.textContent =
      `当前按 ${profileLabel} 展示 ${profileCases} 个证据；展开不变量可查看验收契约与 trace。`;
    return;
  }
  const runProfile =
    cleanText(state.run.profile).toLowerCase() === "full"
      ? "Semantic Full"
      : "Gate";
  elements.matrixSubtitle.textContent =
    `当前配置：${profileLabel}（${profileCases} 用例）；` +
    `页面结果来自 ${runProfile} · ${runId}。`;
}

function autoArrangeProblemGroups() {
  const runId = cleanText(state.run && state.run.run_id);
  if (
    !runId ||
    state.arrangedRunId === runId ||
    !isTerminalStatus(statusOf(state.run))
  ) {
    return;
  }
  const resultMap = getInvariantResultMap();
  state.groups.forEach((group) => {
    const members = invariantsInGroup(group.id);
    const hasProblem = members.some((invariant) => {
      const category = statusCategory(statusOf(resultMap.get(invariant.id)));
      return category === "gap" || category === "failed";
    });
    if (hasProblem) state.expandedGroups.add(group.id);
    else state.expandedGroups.delete(group.id);
  });
  // Keep individual contracts collapsed so a run with several known gaps
  // remains scannable. The diagnostics shortcuts open the exact invariant
  // when the user asks for its evidence.
  state.arrangedRunId = runId;
}

function createDomainGroup(group, allMembers, visibleMembers) {
  const article = createElement("article", "domain-group");
  article.dataset.groupId = group.id;
  const expanded = state.expandedGroups.has(group.id);
  const resultMap = getInvariantResultMap();
  const resultCounts = categoryCounts(
    allMembers.map((item) => resultMap.get(item.id)).filter(Boolean),
  );
  const selectedCount = allMembers.filter((item) =>
    state.selected.has(item.id),
  ).length;
  const profileCases = selectedProfileCaseCount(allMembers, state.profile);
  const selectedCases = selectedProfileCaseCount(
    allMembers.filter((item) => state.selected.has(item.id)),
    state.profile,
  );
  const profileLabel = state.profile === "full" ? "Full" : "Gate";
  const tone = resultCounts.failed
    ? "failed"
    : resultCounts.gap
      ? "gap"
      : resultCounts.total && resultCounts.passed === resultCounts.total
        ? "passed"
        : "idle";
  article.classList.add(`domain-${tone}`);

  const header = createElement("div", "domain-header");
  const selectWrap = createElement("label", "domain-select");
  const checkbox = document.createElement("input");
  checkbox.type = "checkbox";
  checkbox.checked = selectedCount === allMembers.length && allMembers.length > 0;
  checkbox.indeterminate = selectedCount > 0 && selectedCount < allMembers.length;
  checkbox.disabled = isRunActive();
  checkbox.setAttribute("aria-label", `选择 ${group.title} 全部语义`);
  checkbox.addEventListener("change", () =>
    toggleGroupSelection(group.id, checkbox.checked),
  );
  selectWrap.append(checkbox, createElement("span", "visually-hidden", "选择本组"));

  const toggle = createElement("button", "domain-toggle");
  toggle.type = "button";
  toggle.setAttribute("aria-expanded", String(expanded));
  toggle.setAttribute("aria-controls", `group-body-${group.id}`);
  const groupIndex = createElement(
    "span",
    "domain-index",
    String(group.order).padStart(2, "0"),
  );
  const copy = createElement("span", "domain-copy");
  copy.append(
    createElement("strong", "", group.title),
    createElement("small", "", group.subtitle),
  );
  const chevron = createElement("span", "domain-chevron", expanded ? "−" : "+");
  toggle.append(groupIndex, copy, chevron);
  toggle.addEventListener("click", () => {
    if (state.expandedGroups.has(group.id)) state.expandedGroups.delete(group.id);
    else state.expandedGroups.add(group.id);
    renderDomainTree();
  });

  const counts = createElement("div", "domain-counts");
  counts.appendChild(
    createElement(
      "span",
      "domain-case-count",
      `${selectedCount}/${allMembers.length} 语义 · ` +
        `${selectedCases}/${profileCases} ${profileLabel} 用例`,
    ),
  );
  if (state.run) {
    counts.append(
      createCountPill("passed", resultCounts.passed, "通过"),
      createCountPill("gap", resultCounts.gap, "缺口"),
      createCountPill("failed", resultCounts.failed, "失败"),
    );
  }

  const actions = createElement("div", "domain-actions");
  const onlyButton = createElement("button", "small-button", "仅选本组");
  onlyButton.type = "button";
  onlyButton.disabled = isRunActive();
  onlyButton.addEventListener("click", () =>
    selectOnly(allMembers.map((item) => item.id)),
  );
  const runButton = createElement("button", "small-button small-button-accent", "运行本组");
  runButton.type = "button";
  runButton.disabled = isRunActive() || state.startingRun;
  runButton.addEventListener("click", () => runGroup(group.id));
  actions.append(onlyButton, runButton);
  header.append(selectWrap, toggle, counts, actions);

  const body = createElement("div", "domain-body");
  body.id = `group-body-${group.id}`;
  body.hidden = !expanded;
  visibleMembers.forEach((invariant) => {
    body.appendChild(createInvariantDetails(invariant));
  });
  article.append(header, body);
  return article;
}

function createCountPill(category, count, label) {
  const pill = createElement(
    "span",
    `count-pill count-${category}`,
    `${count} ${label}`,
  );
  pill.setAttribute("aria-label", `${label} ${count} 项`);
  return pill;
}

function createInvariantDetails(invariant) {
  const result = getInvariantResultMap().get(invariant.id);
  const status = statusOf(result) || "not_run";
  const category = statusCategory(status);
  const details = createElement(
    "details",
    `invariant-card invariant-${category}`,
  );
  details.id = `invariant-${invariant.id.toLowerCase()}`;
  details.open = state.openInvariants.has(invariant.id);
  details.addEventListener("toggle", () => {
    if (details.open) state.openInvariants.add(invariant.id);
    else state.openInvariants.delete(invariant.id);
  });

  const summary = document.createElement("summary");
  const checkboxWrap = createElement("span", "invariant-select");
  const checkbox = document.createElement("input");
  checkbox.type = "checkbox";
  checkbox.checked = state.selected.has(invariant.id);
  checkbox.disabled = isRunActive();
  checkbox.setAttribute(
    "aria-label",
    `选择 ${invariant.id} ${invariant.title}`,
  );
  checkbox.addEventListener("click", (event) => event.stopPropagation());
  checkbox.addEventListener("change", () => {
    if (checkbox.checked) state.selected.add(invariant.id);
    else state.selected.delete(invariant.id);
    selectionChanged();
  });
  checkboxWrap.appendChild(checkbox);

  const identity = createElement("span", "invariant-summary-copy");
  const titleLine = createElement("span", "invariant-title-line");
  titleLine.append(
    createElement("code", "invariant-id", invariant.id),
    createElement("strong", "", invariant.title),
  );
  if (invariant.knownGap) {
    titleLine.appendChild(createElement("span", "known-gap-badge", "KNOWN GAP"));
  }
  const metaLine = createElement("span", "invariant-meta-line");
  const profileCount = invariantProfileCaseCount(invariant, state.profile);
  metaLine.append(
    createElement("span", "layer-badge", invariant.layer),
    createRiskBadge(invariant.risk),
    createElement(
      "span",
      "case-count-label",
      `${profileCount} ${state.profile === "full" ? "Full" : "Gate"} 用例`,
    ),
  );
  identity.append(titleLine, metaLine);

  const resultWrap = createElement("span", "invariant-result");
  resultWrap.append(
    createStatusChip(status),
    createElement("span", "details-chevron", "⌄"),
  );
  summary.append(checkboxWrap, identity, resultWrap);

  const body = createElement("div", "invariant-body");
  const contract = createElement("div", "contract-grid");
  contract.append(
    createContractCard("原则", invariant.principle),
    createContractCard("验收条件", invariant.acceptance),
    createContractCard(
      "风险",
      invariant.riskDescription || "目录未提供额外风险说明。",
      "risk",
    ),
  );
  body.appendChild(contract);

  if (invariant.knownGap) {
    const gap = createElement("aside", "known-gap-callout");
    gap.append(
      createElement("strong", "", "当前已知缺口"),
      createElement("p", "", invariant.knownGap),
    );
    body.appendChild(gap);
  }

  const evidenceArea = createElement("div", "evidence-area");
  evidenceArea.append(
    createEvidenceLane(
      invariant,
      "Gate 证据",
      invariant.gateTests,
      "gate",
      true,
    ),
    createEvidenceLane(
      invariant,
      "Supporting 证据",
      invariant.supportingTests,
      "supporting",
      state.profile === "full",
    ),
  );
  body.appendChild(evidenceArea);

  const actions = createElement("div", "invariant-actions");
  const only = createElement("button", "small-button", "仅选此项");
  only.type = "button";
  only.disabled = isRunActive();
  only.addEventListener("click", () => selectOnly([invariant.id]));
  const run = createElement("button", "small-button small-button-accent", "运行此项");
  run.type = "button";
  run.disabled = isRunActive() || state.startingRun;
  run.addEventListener("click", async () => {
    state.selected = new Set([invariant.id]);
    selectionChanged();
    await startRun([invariant.id]);
  });
  actions.append(only, run);
  body.appendChild(actions);

  details.append(summary, body);
  return details;
}

function createContractCard(label, value, tone = "") {
  const card = createElement("div", `contract-card ${tone ? `contract-${tone}` : ""}`);
  card.append(
    createElement("span", "field-label", label),
    createElement("p", "", value),
  );
  return card;
}

function createEvidenceLane(invariant, title, tests, kind, activeForProfile) {
  const lane = createElement(
    "section",
    `evidence-lane ${activeForProfile ? "" : "evidence-inactive"}`,
  );
  const heading = createElement("div", "evidence-heading");
  heading.append(
    createElement("h4", "", title),
    createElement(
      "span",
      activeForProfile ? "profile-pill profile-active" : "profile-pill",
      activeForProfile ? `${tests.length} 本次纳入` : `${tests.length} Full only`,
    ),
  );
  lane.appendChild(heading);

  if (tests.length === 0) {
    lane.appendChild(
      createElement(
        "div",
        "evidence-empty",
        kind === "supporting" ? "当前没有 Supporting 证据。" : "未映射 Gate 证据。",
      ),
    );
    return lane;
  }

  const resultMap = getCaseResultMap(invariant.id);
  tests.forEach((test) => {
    lane.appendChild(
      createEvidenceCard(test, resultMap.get(test.nodeid), invariant),
    );
  });
  return lane;
}

function createEvidenceCard(test, result, invariant) {
  const status = statusOf(result) || "not_run";
  const category = statusCategory(status);
  const card = createElement("article", `evidence-card evidence-${category}`);
  const top = createElement("div", "evidence-top");
  const copy = createElement("div", "evidence-copy");
  const proves =
    descriptionOf(result && result.proves) ||
    descriptionOf(test.proves) ||
    humanizeNodeid(test.nodeid);
  const nodeid = createElement("code", "evidence-nodeid", test.nodeid);
  nodeid.title = test.nodeid;
  copy.append(createElement("strong", "evidence-proves", proves), nodeid);
  const statusWrap = createElement("div", "evidence-status");
  const duration = durationText(result);
  if (duration) statusWrap.appendChild(createElement("span", "duration", duration));
  statusWrap.appendChild(createStatusChip(status));
  top.append(copy, statusWrap);
  card.appendChild(top);

  const trace = traceOf(result);
  const message = resultMessage(result);
  if (trace.length || message) {
    const technical = createElement("details", "technical-details");
    const summary = document.createElement("summary");
    const labels = [];
    if (trace.length) labels.push(`Trace ${trace.length} events`);
    if (message) labels.push(category === "gap" ? "Gap 证据" : "诊断详情");
    summary.textContent = labels.join(" · ");
    const technicalBody = createElement("div", "technical-body");
    if (trace.length) technicalBody.appendChild(createTraceTimeline(trace));
    if (message) {
      const messageBlock = createElement("div", "message-block");
      messageBlock.append(
        createElement("span", "field-label", "Pytest message"),
        createElement("pre", "", message),
      );
      technicalBody.appendChild(messageBlock);
    }
    technical.append(summary, technicalBody);
    card.appendChild(technical);
  } else if (result && invariantIdsOf(result).length === 0) {
    card.title = `结果通过 nodeid 映射到 ${invariant.id}`;
  }
  return card;
}

function createTraceTimeline(events) {
  const timeline = createElement("ol", "trace-timeline");
  events.forEach((event, index) => {
    const item = createElement("li", "trace-event");
    const rail = createElement(
      "span",
      "trace-seq",
      String(event.seq ?? index + 1).padStart(2, "0"),
    );
    const content = createElement("div", "trace-content");
    const type = cleanText(event.type || event.event_type) || "event";
    content.appendChild(createElement("strong", "", type));
    const meta = [
      cleanText(event.phase),
      cleanText(event.source),
    ].filter(Boolean);
    if (meta.length) {
      content.appendChild(createElement("span", "trace-meta", meta.join(" · ")));
    }
    if (isObject(event.data) && Object.keys(event.data).length) {
      content.appendChild(
        createElement("pre", "trace-data", safeJson(event.data)),
      );
    }
    item.append(rail, content);
    timeline.appendChild(item);
  });
  return timeline;
}

function renderDiagnostics() {
  if (!state.run) {
    elements.diagnosticsCount.textContent = "暂无结果";
    elements.diagnosticsCount.className = "quiet-badge";
    elements.diagnosticsEmpty.hidden = false;
    elements.diagnosticsList.hidden = true;
    elements.diagnosticsList.replaceChildren();
    return;
  }
  const resultMap = getInvariantResultMap();
  const problems = state.invariants
    .map((invariant) => ({ invariant, result: resultMap.get(invariant.id) }))
    .filter(({ result }) =>
      ["gap", "failed"].includes(statusCategory(statusOf(result))),
    );
  const runnerError = descriptionOf(
    (state.run && state.run.error) || runResultPayload().error,
  );
  const count = problems.length + (runnerError ? 1 : 0);
  elements.diagnosticsCount.textContent = count
    ? `${count} 项需关注`
    : "0 项需关注";
  elements.diagnosticsCount.className = count
    ? "quiet-badge quiet-badge-warning"
    : "quiet-badge quiet-badge-success";

  if (count === 0) {
    elements.diagnosticsEmpty.textContent = isRunActive()
      ? "当前没有问题结果；运行结束前状态仍可能变化。"
      : "本次运行没有 Known Gap 或新增失败。";
    elements.diagnosticsEmpty.className =
      "diagnostics-empty diagnostics-empty-success";
    elements.diagnosticsEmpty.hidden = false;
    elements.diagnosticsList.hidden = true;
    elements.diagnosticsList.replaceChildren();
    return;
  }

  const fragment = document.createDocumentFragment();
  if (runnerError) {
    fragment.appendChild(
      createDiagnosticItem(
        "failed",
        "RUNNER",
        "验收运行器错误",
        runnerError,
        null,
      ),
    );
  }
  problems.forEach(({ invariant, result }) => {
    const category = statusCategory(statusOf(result));
    const failingCases = casesForInvariantResult(result).filter((item) =>
      ["gap", "failed"].includes(statusCategory(statusOf(item))),
    );
    const summary =
      category === "gap"
        ? descriptionOf(result.known_gap || invariant.knownGap) ||
          "测试确认存在已知语义缺口。"
        : resultMessage(failingCases[0]) || "测试返回了意外失败。";
    fragment.appendChild(
      createDiagnosticItem(
        category,
        invariant.id,
        invariant.title,
        summary,
        invariant,
      ),
    );
  });
  elements.diagnosticsList.replaceChildren(fragment);
  elements.diagnosticsList.hidden = false;
  elements.diagnosticsEmpty.hidden = true;
}

function createDiagnosticItem(category, id, title, summary, invariant) {
  const item = createElement("article", `diagnostic-item diagnostic-${category}`);
  const copy = createElement("div", "diagnostic-copy");
  const heading = createElement("div", "diagnostic-heading");
  heading.append(
    createStatusChip(category === "gap" ? "known_gap" : "failed"),
    createElement("code", "", id),
    createElement("strong", "", title),
  );
  copy.append(heading, createElement("p", "", summary));
  item.appendChild(copy);
  if (invariant) {
    const button = createElement("button", "small-button", "查看所属证据");
    button.type = "button";
    button.addEventListener("click", () => revealInvariant(invariant));
    item.appendChild(button);
  }
  return item;
}

function revealInvariant(invariant) {
  clearFilters(false);
  state.expandedGroups.add(invariant.groupId);
  state.openInvariants.add(invariant.id);
  renderDomainTree();
  window.requestAnimationFrame(() => {
    const target = document.getElementById(
      `invariant-${invariant.id.toLowerCase()}`,
    );
    if (target) target.scrollIntoView({ behavior: "smooth", block: "center" });
  });
}

function clearFilters(render = true) {
  state.filters = { problemsOnly: false, status: "all", group: "all" };
  elements.problemFilter.checked = false;
  elements.statusFilter.value = "all";
  elements.groupFilter.value = "all";
  if (render) renderDomainTree();
}

function invariantMatchesFilters(invariant) {
  const result = getInvariantResultMap().get(invariant.id);
  const category = statusCategory(statusOf(result));
  if (
    state.filters.problemsOnly &&
    !["gap", "failed"].includes(category)
  ) {
    return false;
  }
  if (
    state.filters.status !== "all" &&
    state.filters.status !== category
  ) {
    return false;
  }
  return true;
}

function getCases(run = state.run) {
  if (!isObject(run)) return [];
  const result = isObject(run.result) ? run.result : {};
  return normalizeResultCollection(result.cases || run.cases, "case_id");
}

function getInvariantResults(run = state.run) {
  if (!isObject(run)) return [];
  const result = isObject(run.result) ? run.result : {};
  return normalizeResultCollection(
    result.invariants || run.invariants,
    "invariant_id",
  );
}

function normalizeResultCollection(value, preferredKey) {
  if (Array.isArray(value)) return value.filter(isObject);
  if (!isObject(value)) return [];
  return Object.entries(value).map(([key, item]) => {
    if (isObject(item)) {
      return {
        [preferredKey]: item[preferredKey] || item.id || key,
        ...item,
      };
    }
    return { [preferredKey]: key, status: item };
  });
}

function runResultPayload() {
  return isObject(state.run && state.run.result) ? state.run.result : {};
}

function getInvariantResultMap() {
  const map = new Map();
  getInvariantResults().forEach((item) => {
    const id = cleanText(item.invariant_id || item.id);
    if (id) map.set(id, item);
  });
  if (map.size === 0) {
    const groupedCases = new Map();
    getCases().forEach((item) => {
      invariantIdsOf(item).forEach((id) => {
        if (!groupedCases.has(id)) groupedCases.set(id, []);
        groupedCases.get(id).push(item);
      });
    });
    groupedCases.forEach((cases, id) => {
      map.set(id, {
        invariant_id: id,
        status: aggregateStatus(cases),
        cases,
      });
    });
  }
  return map;
}

function getCaseResultMap(invariantId) {
  const map = new Map();
  const invariantResult = getInvariantResultMap().get(invariantId);
  casesForInvariantResult(invariantResult).forEach((item) => {
    const nodeid = cleanText(item.nodeid || item.case_id || item.id);
    if (nodeid) map.set(nodeid, item);
  });
  getCases().forEach((item) => {
    const nodeid = cleanText(item.nodeid || item.case_id || item.id);
    if (!nodeid || map.has(nodeid)) return;
    const ids = invariantIdsOf(item);
    if (ids.includes(invariantId)) map.set(nodeid, item);
  });
  return map;
}

function casesForInvariantResult(result) {
  return isObject(result)
    ? normalizeResultCollection(result.cases, "case_id")
    : [];
}

function invariantIdsOf(item) {
  if (!isObject(item)) return [];
  const ids = [];
  [
    item.invariant_id,
    item.invariant,
    item.group_id && /^INV-\d+$/i.test(String(item.group_id))
      ? item.group_id
      : "",
  ].forEach((value) => {
    const normalized = cleanText(value);
    if (normalized && !ids.includes(normalized)) ids.push(normalized);
  });
  asArray(item.invariant_ids).forEach((value) => {
    const normalized = cleanText(value);
    if (normalized && !ids.includes(normalized)) ids.push(normalized);
  });
  asArray(item.evidence_refs).forEach((ref) => {
    const normalized = isObject(ref)
      ? cleanText(ref.invariant_id || ref.id)
      : "";
    if (normalized && !ids.includes(normalized)) ids.push(normalized);
  });
  return ids;
}

function aggregateStatus(items) {
  const categories = items.map((item) => statusCategory(statusOf(item)));
  if (categories.includes("failed")) return "failed";
  if (categories.includes("gap")) return "known_gap";
  if (categories.includes("active")) return "running";
  if (categories.length && categories.every((item) => item === "passed")) {
    return "passed";
  }
  if (categories.length && categories.every((item) => item === "skipped")) {
    return "skipped";
  }
  return "not_run";
}

function categoryCounts(items) {
  const counts = {
    total: items.length,
    passed: 0,
    gap: 0,
    failed: 0,
    active: 0,
    skipped: 0,
    not_run: 0,
  };
  items.forEach((item) => {
    const category = statusCategory(statusOf(item));
    if (Object.hasOwn(counts, category)) counts[category] += 1;
    else counts.not_run += 1;
  });
  return counts;
}

function statusCategory(status) {
  const normalized = cleanText(status).toLowerCase();
  if (GAP_STATUSES.has(normalized)) return "gap";
  if (FAILURE_STATUSES.has(normalized)) return "failed";
  if (ACTIVE_STATUSES.has(normalized)) return "active";
  if (isPassedStatus(normalized)) return "passed";
  if (["skipped", "cancelled", "canceled", "interrupted"].includes(normalized)) {
    return "skipped";
  }
  return "not_run";
}

function statusOf(item) {
  if (!isObject(item)) return cleanText(item).toLowerCase();
  return cleanText(item.status || item.outcome || item.state).toLowerCase();
}

function isTerminalStatus(status) {
  return TERMINAL_STATUSES.has(cleanText(status).toLowerCase());
}

function isPassedStatus(status) {
  return ["passed", "success", "succeeded", "completed", "complete"].includes(
    cleanText(status).toLowerCase(),
  );
}

function isRunActive() {
  return Boolean(state.run && ACTIVE_STATUSES.has(statusOf(state.run)));
}

function setStatusChip(target, status) {
  const normalized = cleanText(status).toLowerCase() || "idle";
  const category = statusCategory(normalized);
  target.textContent = STATUS_LABELS[normalized] || normalized;
  target.className = `status-chip status-${category}`;
}

function createStatusChip(status) {
  const chip = createElement("span", "status-chip");
  setStatusChip(chip, status);
  return chip;
}

function createRiskBadge(risk) {
  const normalized = cleanText(risk).toLowerCase() || "unknown";
  return createElement(
    "span",
    `risk-badge risk-${riskClass(normalized)}`,
    `风险 ${RISK_LABELS[normalized] || RISK_LABELS[risk] || risk}`,
  );
}

function riskClass(risk) {
  if (["critical", "极高"].includes(risk)) return "critical";
  if (["high", "高"].includes(risk)) return "high";
  if (["medium", "中"].includes(risk)) return "medium";
  if (["low", "低"].includes(risk)) return "low";
  return "unknown";
}

function invariantsInGroup(groupId) {
  return state.invariants.filter((item) => item.groupId === groupId);
}

function testsForProfile(invariant, profile) {
  if (profile === "gate") return invariant.gateTests;
  return uniqueEvidence([...invariant.gateTests, ...invariant.supportingTests]);
}

function invariantProfileCaseCount(invariant, profile) {
  const tests = testsForProfile(invariant, profile);
  if (tests.length) return tests.length;
  const raw = isObject(invariant.counts)
    ? invariant.counts[profile]
    : null;
  return readCount(raw, ["cases", "case_count", "tests", "test_count"]) ??
    (Number.isFinite(Number(raw)) ? Number(raw) : 0);
}

function uniqueProfileNodeids(invariants, profile) {
  const nodeids = new Set();
  invariants.forEach((invariant) => {
    testsForProfile(invariant, profile).forEach((test) => {
      if (test.nodeid) nodeids.add(test.nodeid);
    });
  });
  return nodeids;
}

function selectedProfileCaseCount(invariants, profile) {
  const nodeids = uniqueProfileNodeids(invariants, profile);
  if (nodeids.size) return nodeids.size;
  return invariants.reduce(
    (total, invariant) =>
      total + invariantProfileCaseCount(invariant, profile),
    0,
  );
}

function readCount(value, keys) {
  if (Number.isFinite(Number(value)) && value !== null && value !== "") {
    return Number(value);
  }
  if (!isObject(value)) return null;
  for (const key of keys) {
    if (Number.isFinite(Number(value[key]))) return Number(value[key]);
  }
  return null;
}

function updateControls() {
  const active = isRunActive();
  const unavailable = state.loadingCatalog || state.startingRun;
  elements.runButton.disabled =
    unavailable || active || state.selected.size === 0 || !state.catalog;
  elements.cancelButton.hidden = !active;
  elements.cancelButton.disabled = state.cancelRequested;
  elements.selectAllButton.disabled = active || unavailable || !state.catalog;
  elements.clearSelectionButton.disabled = active || unavailable || !state.catalog;
  elements.profileGate.disabled = active || unavailable;
  elements.profileFull.disabled = active || unavailable;
}

function traceOf(result) {
  if (!isObject(result)) return [];
  if (Array.isArray(result.trace)) return result.trace.filter(isObject);
  if (isObject(result.trace) && Array.isArray(result.trace.events)) {
    return result.trace.events.filter(isObject);
  }
  return [];
}

function resultMessage(result) {
  if (!isObject(result)) return "";
  const value =
    result.message ||
    result.failure ||
    result.error ||
    result.longrepr ||
    result.traceback;
  if (!value) return "";
  if (typeof value === "string") return value;
  return safeJson(value);
}

function durationText(item) {
  if (!isObject(item)) return "";
  const raw = item.duration_seconds ?? item.duration ?? item.elapsed_seconds;
  const seconds = Number(raw);
  if (!Number.isFinite(seconds) || seconds < 0) return "";
  if (seconds < 1) return `${Math.round(seconds * 1000)} ms`;
  return `${seconds.toFixed(seconds >= 10 ? 1 : 2)} s`;
}

function humanizeNodeid(nodeid) {
  const raw = cleanText(nodeid);
  const testName = raw.split("::").pop() || raw;
  return testName
    .replace(/^test_/, "")
    .replaceAll("_", " ")
    .replace(/\s+/g, " ")
    .trim() || "语义验收证据";
}

function descriptionOf(value) {
  if (value === null || value === undefined) return "";
  if (typeof value === "string" || typeof value === "number") {
    return cleanText(value);
  }
  if (Array.isArray(value)) {
    return value.map(descriptionOf).filter(Boolean).join("；");
  }
  if (isObject(value)) {
    return cleanText(
      value.description || value.message || value.summary || value.title,
    );
  }
  return "";
}

async function copyRunId() {
  const runId = cleanText(state.run && state.run.run_id);
  if (!runId) return;
  try {
    await navigator.clipboard.writeText(runId);
    showToast("Run ID 已复制。", "success");
  } catch {
    showToast("浏览器未允许访问剪贴板。", "warning");
  }
}

function showToast(message, tone = "info") {
  if (state.toastTimer) window.clearTimeout(state.toastTimer);
  elements.toast.textContent = cleanText(message);
  elements.toast.className = `toast toast-${tone}`;
  elements.toast.hidden = false;
  state.toastTimer = window.setTimeout(() => {
    elements.toast.hidden = true;
  }, 4_200);
}

function formatTimestamp(value) {
  const raw = cleanText(value);
  if (!raw) return "刚刚";
  const date = new Date(raw);
  if (Number.isNaN(date.getTime())) return raw;
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(date);
}

function safeJson(value) {
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function createElement(tagName, className = "", text = "") {
  const element = document.createElement(tagName);
  if (className) element.className = className;
  if (text !== "") element.textContent = String(text);
  return element;
}

function cleanText(value) {
  if (value === null || value === undefined) return "";
  return String(value).trim();
}

function finiteNumber(value, fallback) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function messageOf(error) {
  return cleanText(error && error.message) || "未知错误";
}

function asArray(value) {
  return Array.isArray(value) ? value : [];
}

function isObject(value) {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}
