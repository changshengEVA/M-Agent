"use strict";

const API_ROOT = "/api";
const POLL_INTERVAL_MS = 900;
const REQUEST_TIMEOUT_MS = 15_000;
const CONTRACT_RUN_STORAGE_KEY = "m-agent-p1-contract-runs";

const ACTIVE_STATUSES = new Set([
  "queued",
  "pending",
  "starting",
  "running",
  "cancelling",
  "canceling",
]);

const TERMINAL_STATUSES = new Set([
  "passed",
  "known_gaps",
  "failed",
  "error",
  "incomplete",
  "cancelled",
  "canceled",
  "timeout",
  "timed_out",
]);

const STATUS_LABELS = {
  queued: "已排队",
  pending: "等待中",
  starting: "启动中",
  running: "运行中",
  cancelling: "取消中",
  canceling: "取消中",
  passed: "Passed",
  known_gap: "Known Gap",
  known_gaps: "Known Gaps",
  failed: "Unexpected",
  error: "Error",
  incomplete: "Incomplete",
  cancelled: "Cancelled",
  canceled: "Cancelled",
  timeout: "Timeout",
  timed_out: "Timeout",
  not_run: "Not Run",
  not_implemented: "Not Implemented",
  not_covered: "Not Covered",
  future: "Future",
  executable: "Executable",
};

const STATUS_PRIORITY = {
  failed: 0,
  error: 0,
  timeout: 0,
  timed_out: 0,
  known_gap: 1,
  running: 2,
  queued: 2,
  cancelling: 2,
  not_run: 3,
  not_implemented: 4,
  not_covered: 4,
  future: 4,
  passed: 5,
};

const DOMAIN_ORDER = { TX: 0, SP: 1, AT: 2 };
const LAYER_LABELS = {
  core: "Core",
  robustness: "Robustness",
  matcher_evaluation: "Matcher",
};

const ARTIFACT_LABELS = {
  summary: "Summary JSON",
  pytest: "pytest JSON",
  junit: "JUnit XML",
  stdout: "stdout",
  stderr: "stderr",
};

const state = {
  catalog: null,
  runtimeId: "think_life_v1",
  runsByRuntime: new Map(),
  selectedVariantId: "",
  starting: false,
  cancelRequested: false,
  pollGeneration: 0,
  pollFailures: 0,
  toastTimer: null,
  filters: {
    search: "",
    domain: "all",
    layer: "all",
    status: "all",
    unmetOnly: false,
  },
};

const elements = {};

document.addEventListener("DOMContentLoaded", () => {
  captureElements();
  bindEvents();
  loadContractCatalog();
});

function captureElements() {
  [
    "catalogState",
    "runtimeSelect",
    "runButton",
    "cancelButton",
    "runStatus",
    "runConclusion",
    "platformCoverageBadge",
    "runMeta",
    "metricExecuted",
    "metricExecutedMeta",
    "metricPassed",
    "metricGaps",
    "metricUnexpected",
    "metricChecks",
    "metricChecksMeta",
    "runIdValue",
    "runDuration",
    "runArtifacts",
    "domainOverview",
    "filterSearch",
    "filterDomain",
    "filterLayer",
    "filterStatus",
    "filterUnmetOnly",
    "clearFilters",
    "visibleCount",
    "variantList",
    "variantEmpty",
    "evidencePanel",
    "toast",
  ].forEach((id) => {
    const element = document.getElementById(id);
    if (!element) throw new Error(`Missing required UI element: #${id}`);
    elements[id] = element;
  });
}

function bindEvents() {
  document
    .getElementById("contractFilters")
    .addEventListener("submit", (event) => event.preventDefault());
  elements.runtimeSelect.addEventListener("change", () => {
    state.pollGeneration += 1;
    state.runtimeId = elements.runtimeSelect.value;
    state.selectedVariantId = "";
    state.pollFailures = 0;
    renderAll();
  });
  elements.runButton.addEventListener("click", startContractRun);
  elements.cancelButton.addEventListener("click", cancelContractRun);
  elements.filterSearch.addEventListener("input", () => {
    state.filters.search = cleanText(elements.filterSearch.value).toLowerCase();
    renderResults();
  });
  elements.filterDomain.addEventListener("change", () => {
    state.filters.domain = elements.filterDomain.value;
    renderResults();
    renderDomainOverview();
  });
  elements.filterLayer.addEventListener("change", () => {
    state.filters.layer = elements.filterLayer.value;
    renderResults();
  });
  elements.filterStatus.addEventListener("change", () => {
    state.filters.status = elements.filterStatus.value;
    renderResults();
  });
  elements.filterUnmetOnly.addEventListener("change", () => {
    state.filters.unmetOnly = elements.filterUnmetOnly.checked;
    renderResults();
  });
  elements.clearFilters.addEventListener("click", () => {
    state.filters = {
      search: "",
      domain: "all",
      layer: "all",
      status: "all",
      unmetOnly: false,
    };
    elements.filterSearch.value = "";
    elements.filterDomain.value = "all";
    elements.filterLayer.value = "all";
    elements.filterStatus.value = "all";
    elements.filterUnmetOnly.checked = false;
    renderAll();
  });
}

async function requestJson(path, options = {}) {
  const controller = new AbortController();
  const timeout = window.setTimeout(
    () => controller.abort(),
    REQUEST_TIMEOUT_MS,
  );
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
      const error = new Error(
        String(
          payload.detail ||
            payload.error ||
            payload.message ||
            `${response.status} ${response.statusText}`,
        ),
      );
      error.status = response.status;
      throw error;
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

async function loadContractCatalog() {
  setCatalogState("读取目录", "neutral");
  try {
    state.catalog = await requestJson("/contract/catalog");
    const coverage = isObject(state.catalog.coverage)
      ? state.catalog.coverage
      : {};
    const complete = Boolean(coverage.catalog_complete);
    setCatalogState(complete ? "目录完整" : "目录异常", complete ? "success" : "danger");
    renderAll();
  } catch (error) {
    state.catalog = null;
    setCatalogState("目录不可用", "danger");
    elements.runConclusion.textContent = `无法读取 P1 目录：${messageOf(error)}`;
    showToast(messageOf(error), "danger");
    renderAll();
  }
}

function setCatalogState(label, tone) {
  elements.catalogState.textContent = label;
  elements.catalogState.className = `badge badge-${tone}`;
}

function activeRun() {
  return state.runsByRuntime.get(state.runtimeId) || null;
}

function runResult(run = activeRun()) {
  return isObject(run && run.result) ? run.result : null;
}

function resultMatchesRuntime(run, runtimeId) {
  if (!isObject(run)) return false;
  const result = runResult(run);
  const actualRuntime = cleanText(
    (result && result.runtime_id) || run.runtime_id,
  );
  return actualRuntime === runtimeId;
}

function buildContractRows() {
  if (!isObject(state.catalog)) return [];
  const run = activeRun();
  const result = resultMatchesRuntime(run, state.runtimeId)
    ? runResult(run)
    : null;
  const resultByVariant = new Map();
  asArray(result && result.scenarios).forEach((scenario) => {
    asArray(scenario.variants).forEach((variant) => {
      resultByVariant.set(
        cleanText(variant.variant_id || variant.id),
        variant,
      );
    });
  });

  return asArray(state.catalog.scenarios).flatMap((scenario) =>
    asArray(scenario.variants).map((variant) => {
      const binding =
        asArray(variant.bindings).find(
          (item) => cleanText(item.runtime_id) === state.runtimeId,
        ) || {};
      const variantId = cleanText(variant.variant_id || variant.id);
      const executed = resultByVariant.get(variantId) || null;
      const availability =
        cleanText(binding.availability) || "not_covered";
      const status = executed
        ? cleanText(executed.status) || "failed"
        : availability === "executable"
          ? "not_run"
          : availability;
      const cases = asArray(executed && executed.cases);
      const checks = cases.flatMap((caseItem) => {
        const observation = isObject(caseItem.observation)
          ? caseItem.observation
          : {};
        const data = isObject(observation.data) ? observation.data : {};
        return asArray(data.checks).filter(isObject);
      });
      const failedChecks = checks.filter((check) => check.passed !== true);
      const passedChecks = checks.filter((check) => check.passed === true);
      const registeredGapChecks = failedChecks.filter((check) =>
        Boolean(cleanText(check.known_gap_key)),
      );
      const unexpectedChecks = failedChecks.filter(
        (check) => !cleanText(check.known_gap_key),
      );
      const firstCase = cases[0] || {};
      const observation = isObject(firstCase.observation)
        ? firstCase.observation
        : {};
      const data = isObject(observation.data) ? observation.data : {};

      return {
        scenarioId: cleanText(scenario.scenario_id || scenario.id),
        scenarioTitle: cleanText(scenario.title),
        scenarioOrder: finiteNumber(scenario.order, 0),
        domain: cleanText(scenario.domain),
        variantId,
        variantTitle: cleanText(variant.title),
        layer: cleanText(variant.layer),
        runtimeId: state.runtimeId,
        availability,
        binding,
        executed,
        status,
        cases,
        checks,
        failedChecks,
        passedChecks,
        registeredGapChecks,
        unexpectedChecks,
        facts: isObject(data.facts) ? data.facts : {},
        trace: asArray(firstCase.trace).filter(isObject),
      };
    }),
  );
}

function filteredRows(rows) {
  const query = state.filters.search;
  return rows
    .filter((row) => {
      if (state.filters.domain !== "all" && row.domain !== state.filters.domain) {
        return false;
      }
      if (state.filters.layer !== "all" && row.layer !== state.filters.layer) {
        return false;
      }
      if (!statusMatchesFilter(row, state.filters.status)) return false;
      if (state.filters.unmetOnly && row.failedChecks.length === 0) return false;
      if (
        query &&
        ![
          row.variantId,
          row.scenarioId,
          row.scenarioTitle,
          row.variantTitle,
        ]
          .join(" ")
          .toLowerCase()
          .includes(query)
      ) {
        return false;
      }
      return true;
    })
    .sort(compareRows);
}

function statusMatchesFilter(row, filter) {
  if (filter === "all") return true;
  if (filter === "problems") {
    return row.status === "known_gap" || isUnexpectedStatus(row.status);
  }
  if (filter === "failed") return isUnexpectedStatus(row.status);
  if (filter === "unavailable") {
    return ["not_implemented", "not_covered", "future"].includes(row.status);
  }
  return row.status === filter;
}

function compareRows(left, right) {
  const priority =
    finiteNumber(STATUS_PRIORITY[left.status], 9) -
    finiteNumber(STATUS_PRIORITY[right.status], 9);
  if (priority !== 0) return priority;
  const domain =
    finiteNumber(DOMAIN_ORDER[left.domain], 9) -
    finiteNumber(DOMAIN_ORDER[right.domain], 9);
  if (domain !== 0) return domain;
  if (left.scenarioOrder !== right.scenarioOrder) {
    return left.scenarioOrder - right.scenarioOrder;
  }
  return left.variantId.localeCompare(right.variantId);
}

function renderAll() {
  const rows = buildContractRows();
  renderRunOverview(rows);
  renderDomainOverview(rows);
  renderResults(rows);
  updateControls(rows);
}

function renderRunOverview(rows) {
  const catalogVariantCount = rows.length;
  const executableCount = rows.filter(
    (row) => row.availability === "executable",
  ).length;
  elements.platformCoverageBadge.textContent =
    `平台覆盖 ${executableCount}/${catalogVariantCount}`;

  const run = activeRun();
  const runStatus = cleanText(run && run.status);
  const isActive = ACTIVE_STATUSES.has(runStatus);
  const executedRows = rows.filter((row) => row.executed);
  const passed = rows.filter((row) => row.status === "passed").length;
  const gaps = rows.filter((row) => row.status === "known_gap").length;
  const unexpectedRows = rows.filter(
    (row) => isUnexpectedStatus(row.status) || row.unexpectedChecks.length,
  ).length;
  const checks = rows.flatMap((row) => row.checks);
  const passedChecks = checks.filter((check) => check.passed === true).length;
  const failedChecks = checks.length - passedChecks;

  elements.metricExecuted.textContent = run
    ? `${executedRows.length}/${catalogVariantCount}`
    : "—";
  elements.metricExecutedMeta.textContent =
    `${executableCount} 项可执行`;
  elements.metricPassed.textContent = run ? String(passed) : "—";
  elements.metricGaps.textContent = run ? String(gaps) : "—";
  elements.metricUnexpected.textContent = run ? String(unexpectedRows) : "—";
  elements.metricChecks.textContent = checks.length
    ? String(checks.length)
    : "—";
  elements.metricChecksMeta.textContent = checks.length
    ? `${passedChecks} passed · ${failedChecks} unmet`
    : "等待证据";

  elements.runIdValue.textContent = cleanText(run && run.run_id) || "—";
  const result = runResult(run);
  const duration = finiteNumber(result && result.duration_seconds, -1);
  elements.runDuration.textContent =
    duration >= 0 ? `${duration.toFixed(2)}s` : "—";

  if (!run) {
    elements.runStatus.textContent = "未运行";
    elements.runStatus.className = "status status-not-run";
    elements.runMeta.textContent = `${state.runtimeId} · 等待运行`;
    if (executableCount === 0) {
      elements.runConclusion.textContent =
        "该 Runtime 尚未实现 P1 adapter；目录中的场景均显示 Not Implemented。";
      document.getElementById("run-overview-title").textContent =
        "Runtime 尚不可执行";
    } else {
      elements.runConclusion.textContent =
        "目录已就绪。运行后将分别显示目标契约通过、已登记差距和未登记失败。";
      document.getElementById("run-overview-title").textContent =
        "尚未运行";
    }
    renderArtifacts(null);
    return;
  }

  setStatusElement(elements.runStatus, runStatus);
  elements.runMeta.textContent = isActive
    ? `${state.runtimeId} · 子进程执行中，结果将在完成后生成`
    : `${state.runtimeId} · ${STATUS_LABELS[runStatus] || runStatus}`;

  if (isActive) {
    document.getElementById("run-overview-title").textContent =
      "正在执行 P1 矩阵";
    elements.runConclusion.textContent =
      "测试正在隔离子进程中运行。运行完成前不会用目录状态伪造检查结果。";
  } else if (unexpectedRows > 0 || ["failed", "error"].includes(runStatus)) {
    document.getElementById("run-overview-title").textContent =
      "发现未登记失败";
    elements.runConclusion.textContent =
      `共有 ${unexpectedRows} 个 variant 出现 Unexpected；` +
      "这些失败没有被 Known Gap 掩盖，需要优先处理。";
  } else if (runStatus === "known_gaps" || gaps > 0) {
    document.getElementById("run-overview-title").textContent =
      "平台运行正常，Runtime 尚未满足目标契约";
    elements.runConclusion.textContent =
      `${gaps} 个 variant 为 Known Gap，0 个未登记失败。` +
      `检查级证据：${passedChecks} 通过，${failedChecks} 项差距。`;
  } else if (runStatus === "passed") {
    document.getElementById("run-overview-title").textContent =
      "目标契约全部通过";
    elements.runConclusion.textContent =
      `${passed} 个 variant 已满足当前 P1 目标语义。`;
  } else {
    document.getElementById("run-overview-title").textContent =
      STATUS_LABELS[runStatus] || "运行已结束";
    elements.runConclusion.textContent =
      cleanText(run.error) || "运行未产生完整可执行结果。";
  }
  renderArtifacts(run);
}

function renderDomainOverview(rows = buildContractRows()) {
  const fragment = document.createDocumentFragment();
  ["TX", "SP", "AT"].forEach((domain) => {
    const domainRows = rows.filter((row) => row.domain === domain);
    const gaps = domainRows.filter((row) => row.status === "known_gap").length;
    const unexpected = domainRows.filter(
      (row) => isUnexpectedStatus(row.status),
    ).length;
    const passed = domainRows.filter((row) => row.status === "passed").length;
    const button = createElement(
      "button",
      `domain-card ${
        state.filters.domain === domain ? "domain-card-active" : ""
      }`,
    );
    button.type = "button";
    button.dataset.domain = domain;
    button.setAttribute(
      "aria-pressed",
      String(state.filters.domain === domain),
    );
    const top = createElement("span", "domain-card-top");
    top.append(
      createElement("strong", "", domain),
      createElement("b", "", `${domainRows.length} variants`),
    );
    const counts = createElement("span", "domain-card-counts");
    counts.append(
      metricDot("passed", passed),
      metricDot("gap", gaps),
      metricDot("failed", unexpected),
    );
    button.append(top, counts);
    button.addEventListener("click", () => {
      state.filters.domain =
        state.filters.domain === domain ? "all" : domain;
      elements.filterDomain.value = state.filters.domain;
      renderDomainOverview(rows);
      renderResults(rows);
    });
    fragment.append(button);
  });
  elements.domainOverview.replaceChildren(fragment);
}

function metricDot(tone, count) {
  const wrapper = createElement("span", `domain-metric domain-metric-${tone}`);
  wrapper.append(
    createElement("i", "", ""),
    document.createTextNode(
      `${count} ${
        tone === "passed" ? "pass" : tone === "gap" ? "gap" : "unexpected"
      }`,
    ),
  );
  return wrapper;
}

function renderResults(rows = buildContractRows()) {
  const visible = filteredRows(rows);
  elements.visibleCount.textContent = `${visible.length} / ${rows.length} 项`;
  elements.variantEmpty.hidden = visible.length > 0;

  if (
    !visible.some((row) => row.variantId === state.selectedVariantId)
  ) {
    state.selectedVariantId = visible[0] ? visible[0].variantId : "";
  }

  const fragment = document.createDocumentFragment();
  visible.forEach((row) => {
    const button = createElement(
      "button",
      `variant-row ${
        row.variantId === state.selectedVariantId
          ? "variant-row-selected"
          : ""
      }`,
    );
    button.type = "button";
    button.dataset.variantId = row.variantId;
    button.setAttribute(
      "aria-current",
      row.variantId === state.selectedVariantId ? "true" : "false",
    );

    const identity = createElement("span", "variant-identity");
    const meta = createElement("span", "variant-meta");
    meta.append(
      createElement("code", "", row.variantId),
      createElement(
        "span",
        "layer-chip",
        LAYER_LABELS[row.layer] || row.layer,
      ),
    );
    const title = createElement(
      "strong",
      "",
      row.layer === "robustness"
        ? row.variantTitle
        : row.scenarioTitle,
    );
    identity.append(meta, title);

    const outcome = createElement("span", "variant-outcome");
    outcome.append(createStatusChip(row.status));
    if (row.checks.length) {
      outcome.append(
        createElement(
          "small",
          "",
          `${row.passedChecks.length}/${row.checks.length} checks · ` +
            `${row.failedChecks.length} unmet`,
        ),
      );
    } else if (row.availability === "executable") {
      outcome.append(createElement("small", "", "尚无执行证据"));
    } else {
      outcome.append(
        createElement(
          "small",
          "",
          STATUS_LABELS[row.availability] || row.availability,
        ),
      );
    }

    const chevron = createElement("span", "variant-chevron", "›");
    button.append(identity, outcome, chevron);
    button.addEventListener("click", () => {
      state.selectedVariantId = row.variantId;
      renderResults(rows);
    });
    fragment.append(button);
  });
  elements.variantList.replaceChildren(fragment);

  const selected = rows.find(
    (row) => row.variantId === state.selectedVariantId,
  );
  renderEvidenceDetail(selected || null);
}

function renderEvidenceDetail(row) {
  if (!row) {
    const placeholder = createElement("div", "detail-placeholder");
    placeholder.append(
      createElement("span", "", "↖"),
      createElement("strong", "", "选择一个场景查看证据"),
      createElement(
        "p",
        "",
        "失败检查、Expected / Actual、证据来源、facts 和 trace 会显示在这里。",
      ),
    );
    elements.evidencePanel.replaceChildren(placeholder);
    return;
  }

  const header = createElement("header", "detail-header");
  const titleWrap = createElement("div", "");
  const eyebrow = createElement("div", "detail-eyebrow");
  eyebrow.append(
    createElement("code", "", row.variantId),
    createElement(
      "span",
      "layer-chip",
      LAYER_LABELS[row.layer] || row.layer,
    ),
  );
  titleWrap.append(
    eyebrow,
    createElement(
      "h2",
      "",
      row.layer === "robustness"
        ? row.variantTitle
        : row.scenarioTitle,
    ),
    createElement(
      "p",
      "",
      `${row.runtimeId} · ${STATUS_LABELS[row.availability] || row.availability}`,
    ),
  );
  header.append(titleWrap, createStatusChip(row.status));

  const content = document.createDocumentFragment();
  content.append(header);

  if (row.binding.known_gap) {
    const gap = createElement("section", "gap-reason");
    gap.append(
      createElement("span", "", "Registered Known Gap"),
      createElement("p", "", cleanText(row.binding.known_gap)),
    );
    content.append(gap);
  }

  const summary = createElement("div", "check-summary");
  [
    ["checks", row.checks.length],
    ["passed", row.passedChecks.length],
    ["registered gaps", row.registeredGapChecks.length],
    ["unexpected", row.unexpectedChecks.length],
  ].forEach(([label, value]) => {
    const item = createElement("div", "");
    item.append(
      createElement("strong", "", String(value)),
      createElement("span", "", String(label)),
    );
    summary.append(item);
  });
  content.append(summary);

  if (!row.executed) {
    const unavailable = createElement("div", "detail-empty");
    unavailable.append(
      createElement(
        "strong",
        "",
        row.availability === "executable"
          ? "尚未生成执行证据"
          : "该 Runtime 当前不可执行此场景",
      ),
      createElement(
        "p",
        "",
        row.availability === "executable"
          ? "运行完整矩阵后，这里会显示逐项检查和 Runtime observation。"
          : `${STATUS_LABELS[row.availability] || row.availability} 是能力状态，不是测试通过。`,
      ),
    );
    content.append(unavailable);
    appendRegisteredTests(content, row);
    elements.evidencePanel.replaceChildren(content);
    return;
  }

  const failedSection = createElement("section", "detail-section");
  failedSection.append(
    sectionHeading(
      "未满足检查",
      row.failedChecks.length,
      row.unexpectedChecks.length ? "danger" : "gap",
    ),
  );
  if (row.failedChecks.length) {
    const list = createElement("div", "check-list");
    row.failedChecks.forEach((check) => {
      list.append(renderCheckCard(check));
    });
    failedSection.append(list);
  } else {
    failedSection.append(
      createElement("p", "detail-success", "所有目标检查均已满足。"),
    );
  }
  content.append(failedSection);

  if (row.passedChecks.length) {
    const passedDetail = createElement("details", "detail-disclosure");
    passedDetail.append(
      createElement(
        "summary",
        "",
        `已通过检查（${row.passedChecks.length}）`,
      ),
    );
    const list = createElement("div", "check-list check-list-passed");
    row.passedChecks.forEach((check) => {
      list.append(renderCheckCard(check));
    });
    passedDetail.append(list);
    content.append(passedDetail);
  }

  appendFacts(content, row.facts);
  appendTrace(content, row.trace);
  appendTechnicalCases(content, row.cases);
  elements.evidencePanel.replaceChildren(content);
}

function sectionHeading(label, count, tone) {
  const heading = createElement("div", "detail-section-heading");
  heading.append(
    createElement("h3", "", label),
    createElement("span", `count-badge count-badge-${tone}`, String(count)),
  );
  return heading;
}

function renderCheckCard(check) {
  const passed = check.passed === true;
  const gapKey = cleanText(check.known_gap_key);
  const tone = passed ? "passed" : gapKey ? "gap" : "failed";
  const card = createElement("article", `check-card check-card-${tone}`);
  const top = createElement("div", "check-card-top");
  const identity = createElement("div", "");
  identity.append(
    createElement(
      "span",
      "check-result",
      passed ? "✓ Passed" : gapKey ? "! Registered gap" : "× Unexpected",
    ),
    createElement("code", "", cleanText(check.id) || "check"),
  );
  top.append(identity);
  card.append(
    top,
    createElement("p", "check-description", cleanText(check.description)),
  );

  const comparison = createElement("dl", "check-comparison");
  comparison.append(
    createElement("dt", "", "Expected"),
    valueElement("dd", check.expected),
    createElement("dt", "", "Actual"),
    valueElement("dd", check.actual),
  );
  card.append(comparison);

  const metadata = createElement("div", "check-metadata");
  metadata.append(
    metadataItem("Evidence", cleanText(check.evidence) || "—"),
    metadataItem("Gap key", gapKey || "none"),
  );
  card.append(metadata);
  return card;
}

function metadataItem(label, value) {
  const item = createElement("span", "");
  item.append(
    createElement("b", "", label),
    createElement("code", "", value),
  );
  return item;
}

function valueElement(tagName, value) {
  const element = document.createElement(tagName);
  if (isObject(value) || Array.isArray(value)) {
    element.append(createElement("pre", "", safeJson(value)));
  } else {
    element.textContent =
      value === null
        ? "null"
        : value === undefined
          ? "—"
          : String(value);
  }
  return element;
}

function appendFacts(fragment, facts) {
  if (!isObject(facts) || Object.keys(facts).length === 0) return;
  const detail = createElement("details", "detail-disclosure");
  detail.append(
    createElement(
      "summary",
      "",
      `Normalized facts（${Object.keys(facts).length}）`,
    ),
  );
  const grid = createElement("div", "facts-grid");
  Object.entries(facts).forEach(([key, value]) => {
    const card = createElement("article", "fact-card");
    card.append(
      createElement("code", "", key),
      valueElement("div", value),
    );
    grid.append(card);
  });
  detail.append(grid);
  fragment.append(detail);
}

function appendTrace(fragment, trace) {
  if (!trace.length) return;
  const detail = createElement("details", "detail-disclosure");
  detail.append(
    createElement(
      "summary",
      "",
      `Semantic trace（${trace.length} events）`,
    ),
  );
  const timeline = createElement("ol", "trace-timeline");
  trace.forEach((event, index) => {
    const item = createElement("li", "trace-event");
    item.append(
      createElement(
        "span",
        "trace-seq",
        String(event.seq ?? index + 1).padStart(2, "0"),
      ),
    );
    const body = createElement("div", "trace-body");
    body.append(
      createElement(
        "strong",
        "",
        cleanText(event.type || event.event_type) || "event",
      ),
    );
    const meta = [cleanText(event.phase), cleanText(event.source)]
      .filter(Boolean)
      .join(" · ");
    if (meta) body.append(createElement("span", "", meta));
    if (isObject(event.data) && Object.keys(event.data).length) {
      body.append(createElement("pre", "", safeJson(event.data)));
    }
    item.append(body);
    timeline.append(item);
  });
  detail.append(timeline);
  fragment.append(detail);
}

function appendTechnicalCases(fragment, cases) {
  if (!cases.length) return;
  const detail = createElement("details", "detail-disclosure");
  detail.append(
    createElement("summary", "", `技术详情（${cases.length} case）`),
  );
  const list = createElement("div", "technical-list");
  cases.forEach((caseItem) => {
    const card = createElement("article", "technical-card");
    card.append(
      createStatusChip(cleanText(caseItem.outcome) || "not_run"),
      createElement("code", "", cleanText(caseItem.nodeid)),
    );
    if (cleanText(caseItem.message)) {
      card.append(
        createElement("pre", "", cleanText(caseItem.message)),
      );
    }
    list.append(card);
  });
  detail.append(list);
  fragment.append(detail);
}

function appendRegisteredTests(fragment, row) {
  const tests = asArray(row.binding.tests);
  if (!tests.length) return;
  const detail = createElement("details", "detail-disclosure");
  detail.append(
    createElement("summary", "", `Allowlisted tests（${tests.length}）`),
  );
  const list = createElement("div", "technical-list");
  tests.forEach((test) => {
    const card = createElement("article", "technical-card");
    card.append(
      createElement("code", "", cleanText(test.nodeid)),
      createElement("p", "", cleanText(test.proves)),
    );
    list.append(card);
  });
  detail.append(list);
  fragment.append(detail);
}

async function startContractRun() {
  if (state.starting || ACTIVE_STATUSES.has(cleanText(activeRun()?.status))) {
    return;
  }
  state.starting = true;
  state.cancelRequested = false;
  renderAll();
  try {
    const payload = await requestJson("/contract/runs", {
      method: "POST",
      body: JSON.stringify({
        runtime_id: state.runtimeId,
        layers: ["core", "robustness", "matcher_evaluation"],
        timeout_seconds: 300,
      }),
    });
    if (!cleanText(payload.run_id)) {
      throw new Error("服务端没有返回有效 run_id");
    }
    if (!resultMatchesRuntime(payload, state.runtimeId)) {
      throw new Error("服务端返回的 Runtime 与当前选择不一致");
    }
    state.selectedVariantId = "";
    state.runsByRuntime.set(state.runtimeId, payload);
    rememberRun(state.runtimeId, payload.run_id);
    state.pollFailures = 0;
    renderAll();
    const generation = ++state.pollGeneration;
    pollContractRun(payload.run_id, state.runtimeId, generation);
  } catch (error) {
    showToast(`启动失败：${messageOf(error)}`, "danger");
  } finally {
    state.starting = false;
    renderAll();
  }
}

async function pollContractRun(runId, runtimeId, generation) {
  if (generation !== state.pollGeneration) return;
  try {
    const payload = await requestJson(
      `/contract/runs/${encodeURIComponent(runId)}`,
    );
    if (generation !== state.pollGeneration) return;
    state.pollFailures = 0;
    if (!resultMatchesRuntime(payload, runtimeId)) {
      throw new Error("运行结果属于另一个 Runtime，已拒绝展示");
    }
    state.runsByRuntime.set(runtimeId, payload);
    renderAll();
    if (TERMINAL_STATUSES.has(cleanText(payload.status))) {
      state.cancelRequested = false;
      renderAll();
      return;
    }
    window.setTimeout(
      () => pollContractRun(runId, runtimeId, generation),
      POLL_INTERVAL_MS,
    );
  } catch (error) {
    if (generation !== state.pollGeneration) return;
    state.pollFailures += 1;
    if (state.pollFailures <= 3) {
      const delay = POLL_INTERVAL_MS * state.pollFailures;
      window.setTimeout(
        () => pollContractRun(runId, runtimeId, generation),
        delay,
      );
      return;
    }
    showToast(`状态同步失败：${messageOf(error)}`, "danger");
  }
}

async function cancelContractRun() {
  const run = activeRun();
  const runId = cleanText(run && run.run_id);
  if (!runId || state.cancelRequested) return;
  state.cancelRequested = true;
  state.runsByRuntime.set(state.runtimeId, {
    ...run,
    status: "cancelling",
  });
  renderAll();
  try {
    await requestJson(
      `/contract/runs/${encodeURIComponent(runId)}/cancel`,
      { method: "POST" },
    );
  } catch (error) {
    state.cancelRequested = false;
    showToast(`取消失败：${messageOf(error)}`, "danger");
  }
  renderAll();
}

async function restoreLastContractRun(runtimeId) {
  const remembered = rememberedRun(runtimeId);
  if (remembered) {
    try {
      const payload = await requestJson(
        `/contract/runs/${encodeURIComponent(remembered)}`,
      );
      if (runtimeId !== state.runtimeId) return;
      if (!resultMatchesRuntime(payload, runtimeId)) {
        throw new Error("已保存的运行不属于当前 Runtime");
      }
      state.runsByRuntime.set(runtimeId, payload);
      state.selectedVariantId = "";
      renderAll();
      if (ACTIVE_STATUSES.has(cleanText(payload.status))) {
        const generation = ++state.pollGeneration;
        pollContractRun(remembered, runtimeId, generation);
      }
      return;
    } catch (error) {
      if (finiteNumber(error && error.status, 0) !== 404) {
        showToast(`恢复上次运行失败：${messageOf(error)}`, "danger");
      }
    }
  }

  try {
    const latest = await requestJson(
      `/contract/runs/latest?runtime_id=${encodeURIComponent(runtimeId)}`,
    );
    if (runtimeId !== state.runtimeId) return;
    if (!resultMatchesRuntime(latest, runtimeId)) {
      throw new Error("最近运行不属于当前 Runtime");
    }
    state.runsByRuntime.set(runtimeId, latest);
    rememberRun(runtimeId, latest.run_id);
    state.selectedVariantId = "";
    renderAll();
    if (ACTIVE_STATUSES.has(cleanText(latest.status))) {
      const generation = ++state.pollGeneration;
      pollContractRun(latest.run_id, runtimeId, generation);
    }
  } catch (error) {
    if (finiteNumber(error && error.status, 0) !== 404) {
      showToast(`读取最近运行失败：${messageOf(error)}`, "danger");
    }
  }
}

function rememberRun(runtimeId, runId) {
  try {
    const raw = window.localStorage.getItem(CONTRACT_RUN_STORAGE_KEY);
    const values = raw ? JSON.parse(raw) : {};
    values[runtimeId] = runId;
    window.localStorage.setItem(
      CONTRACT_RUN_STORAGE_KEY,
      JSON.stringify(values),
    );
  } catch {
    // Storage is an enhancement; the latest API remains the fallback.
  }
}

function rememberedRun(runtimeId) {
  try {
    const raw = window.localStorage.getItem(CONTRACT_RUN_STORAGE_KEY);
    const values = raw ? JSON.parse(raw) : {};
    return cleanText(values[runtimeId]);
  } catch {
    return "";
  }
}

function updateControls(rows = buildContractRows()) {
  const run = activeRun();
  const active = ACTIVE_STATUSES.has(cleanText(run && run.status));
  const executable = rows.filter(
    (row) => row.availability === "executable",
  ).length;
  elements.runtimeSelect.disabled = active || state.starting;
  elements.runButton.disabled =
    !state.catalog || active || state.starting || executable === 0;
  elements.runButton.textContent = state.starting
    ? "正在创建…"
    : active
      ? "矩阵运行中"
      : executable === 0
        ? "Runtime 尚未实现"
        : "运行完整矩阵";
  elements.cancelButton.hidden = !active || !cleanText(run && run.run_id);
  elements.cancelButton.disabled = state.cancelRequested;
}

function renderArtifacts(run) {
  const result = runResult(run);
  const artifacts = isObject(result && result.artifacts)
    ? result.artifacts
    : {};
  const runId = cleanText(run && run.run_id);
  if (!runId || Object.keys(artifacts).length === 0) {
    elements.runArtifacts.hidden = true;
    elements.runArtifacts.replaceChildren();
    return;
  }
  const links = Object.entries(artifacts).map(([key, value]) => {
    const artifact = cleanText(value || key);
    const link = createElement(
      "a",
      "artifact-link",
      ARTIFACT_LABELS[key] || key,
    );
    link.href =
      `${API_ROOT}/contract/runs/${encodeURIComponent(runId)}/artifacts/` +
      encodeURIComponent(artifact);
    link.setAttribute("download", "");
    return link;
  });
  elements.runArtifacts.replaceChildren(...links);
  elements.runArtifacts.hidden = links.length === 0;
}

function createStatusChip(status) {
  const normalized = cleanText(status) || "not_run";
  return createElement(
    "span",
    `status status-${statusTone(normalized)}`,
    STATUS_LABELS[normalized] || normalized,
  );
}

function setStatusElement(element, status) {
  const normalized = cleanText(status) || "not_run";
  element.textContent = STATUS_LABELS[normalized] || normalized;
  element.className = `status status-${statusTone(normalized)}`;
}

function statusTone(status) {
  if (status === "passed") return "passed";
  if (status === "known_gap" || status === "known_gaps") return "gap";
  if (ACTIVE_STATUSES.has(status)) return "active";
  if (isUnexpectedStatus(status)) return "failed";
  if (["not_implemented", "not_covered", "future"].includes(status)) {
    return "unavailable";
  }
  return "not-run";
}

function isUnexpectedStatus(status) {
  return ["failed", "error", "timeout", "timed_out"].includes(status);
}

function createElement(tagName, className = "", text = "") {
  const element = document.createElement(tagName);
  if (className) element.className = className;
  if (text !== "") element.textContent = String(text);
  return element;
}

function isObject(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function asArray(value) {
  return Array.isArray(value) ? value : [];
}

function cleanText(value) {
  return value === null || value === undefined ? "" : String(value).trim();
}

function finiteNumber(value, fallback = 0) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function safeJson(value) {
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function messageOf(error) {
  return cleanText(error && error.message) || "未知错误";
}

function showToast(message, tone = "neutral") {
  if (state.toastTimer) window.clearTimeout(state.toastTimer);
  elements.toast.textContent = message;
  elements.toast.className = `toast toast-${tone}`;
  elements.toast.hidden = false;
  state.toastTimer = window.setTimeout(() => {
    elements.toast.hidden = true;
  }, 4200);
}
