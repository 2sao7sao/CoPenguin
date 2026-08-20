const state = {
  overview: null,
  detail: null,
  selectedThreadId: localStorage.getItem("copenguin:selected-thread") || null,
  view: "overview",
  detailRequest: 0,
  decisionMode: new Map(),
  presentingDeliveries: new Set(),
};

const elements = {
  shell: document.querySelector("#app-shell"),
  taskRail: document.querySelector("#task-rail"),
  threadGroups: document.querySelector("#thread-groups"),
  detailPane: document.querySelector("#detail-pane"),
  attentionRail: document.querySelector("#attention-rail"),
  attentionList: document.querySelector("#attention-list"),
  navAttentionCount: document.querySelector("#nav-attention-count"),
  bottomAttentionCount: document.querySelector("#bottom-attention-count"),
  refreshButton: document.querySelector("#refresh-button"),
  composer: document.querySelector("#task-composer"),
  composerText: document.querySelector("#composer-text"),
  composerMode: document.querySelector("#composer-mode"),
  composerProject: document.querySelector("#composer-project"),
  railFilter: document.querySelector("#rail-filter"),
  railFilterLabel: document.querySelector("#rail-filter-label"),
  clearFilter: document.querySelector("#clear-filter"),
  mobileTaskBack: document.querySelector("#mobile-task-back"),
  artifactDialog: document.querySelector("#artifact-dialog"),
  artifactDialogTitle: document.querySelector("#artifact-dialog-title"),
  artifactDialogBody: document.querySelector("#artifact-dialog-body"),
  artifactDialogMeta: document.querySelector("#artifact-dialog-meta"),
  artifactDialogClose: document.querySelector("#artifact-dialog-close"),
  artifactDialogDone: document.querySelector("#artifact-dialog-done"),
  toastRegion: document.querySelector("#toast-region"),
};

const THREAD_STATES = {
  created: "正在准备",
  dormant: "尚未开始",
  queued: "已排队",
  running: "运行中",
  waiting_user: "等待你补充信息",
  waiting_approval: "等待你批准",
  waiting_receipt: "等待外部结果",
  waiting_dependency: "等待依赖",
  waiting_resource: "等待可用资源",
  verifying: "正在验证",
  delivered: "等待你验收",
  failed: "需要处理失败",
  paused: "已暂停",
  cancelled: "已取消",
  archived: "已归档",
};

const ATTENTION_STATES = {
  needs_input: "需要你补充信息",
  needs_approval: "需要你批准",
  has_conflict: "需要解决冲突",
  delivery_ready: "等待你验收",
  failed: "需要处理失败",
  none: "",
};

const RUN_STATES = {
  created: "正在准备",
  queued: "已排队",
  running: "运行中",
  waiting_user: "等待你的信息",
  waiting_approval: "等待批准",
  waiting_receipt: "等待外部结果",
  waiting_dependency: "等待依赖",
  waiting_resource: "等待资源",
  verifying: "正在验证",
  completed: "已完成",
  partial: "部分完成",
  failed: "失败",
  quarantined: "已隔离",
  cancelled: "已取消",
};

const STEP_LABELS = {
  model: "模型处理",
  tool_read: "读取资料",
  tool_write: "写入动作",
  transform: "整理来源",
  verifier: "验证证据",
  delivery_prepare: "准备交付",
};

const STEP_STATES = {
  created: "正在准备",
  running: "运行中",
  succeeded: "已完成",
  failed: "失败",
  waiting_approval: "等待批准",
  waiting_input: "等待输入",
  waiting_resource: "等待资源",
  quarantined: "已隔离",
  cancelled: "已取消",
};

const DELIVERY_STATES = {
  prepared: "正在展示交付",
  presented: "等待你决定",
  accepted: "已接受",
  revision_requested: "已提交修改",
  rejected: "已拒绝",
  deferred: "已稍后处理",
  taken_over: "已由你接管",
};

const DECISION_COPY = {
  accept: "接受",
  revise: "修改",
  defer: "稍后",
  take_over: "接管",
  reject: "拒绝",
};

function icon(name) {
  return `<svg aria-hidden="true"><use href="#icon-${name}"></use></svg>`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function apiPath(path) {
  return `/control-room/api${path}`;
}

async function requestJson(path, options = {}) {
  const response = await fetch(path, {
    cache: "no-store",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }
  if (!response.ok) {
    const detail = payload?.detail || `请求失败（${response.status}）`;
    throw new Error(detail);
  }
  return payload;
}

function stateTone(thread) {
  if (thread.attention_state && thread.attention_state !== "none") {
    return thread.attention_state === "failed" ? "failure" : "attention";
  }
  if (["failed", "cancelled"].includes(thread.actual_state)) return "failure";
  if (["delivered", "dormant", "archived"].includes(thread.actual_state)) return "complete";
  if (["created", "queued", "running", "verifying"].includes(thread.actual_state)) {
    return "active";
  }
  if (thread.actual_state?.startsWith("waiting_")) return "attention";
  return "neutral";
}

function stateLabel(thread) {
  if (thread.attention_state && thread.attention_state !== "none") {
    return ATTENTION_STATES[thread.attention_state] || thread.attention_state;
  }
  if (thread.latest_delivery_state) {
    return DELIVERY_STATES[thread.latest_delivery_state] || thread.latest_delivery_state;
  }
  return THREAD_STATES[thread.actual_state] || thread.actual_state;
}

function stateIcon(tone) {
  if (tone === "complete") return "check";
  if (tone === "attention") return "clock";
  if (tone === "failure") return "alert";
  return "clock";
}

function runTone(runState) {
  if (["completed"].includes(runState)) return "complete";
  if (["failed", "quarantined", "cancelled"].includes(runState)) return "failure";
  if (["waiting_user", "waiting_approval", "waiting_receipt", "waiting_dependency", "waiting_resource", "partial"].includes(runState)) {
    return "attention";
  }
  return "active";
}

function stepTone(stepState) {
  if (stepState === "succeeded") return "complete";
  if (["failed", "quarantined", "cancelled"].includes(stepState)) return "failure";
  return "active";
}

function progressClass(threadState) {
  return threadState?.startsWith("waiting_")
    ? "progress-waiting"
    : `progress-${threadState || "none"}`;
}

function relativeTime(value) {
  if (!value) return "刚刚";
  const time = new Date(value).getTime();
  if (!Number.isFinite(time)) return "刚刚";
  const diffSeconds = Math.round((time - Date.now()) / 1000);
  const formatter = new Intl.RelativeTimeFormat("zh-CN", { numeric: "auto" });
  if (Math.abs(diffSeconds) < 60) return formatter.format(diffSeconds, "second");
  const diffMinutes = Math.round(diffSeconds / 60);
  if (Math.abs(diffMinutes) < 60) return formatter.format(diffMinutes, "minute");
  const diffHours = Math.round(diffMinutes / 60);
  if (Math.abs(diffHours) < 24) return formatter.format(diffHours, "hour");
  const diffDays = Math.round(diffHours / 24);
  return formatter.format(diffDays, "day");
}

function formatAbsoluteTime(value) {
  if (!value) return "尚未记录";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "尚未记录";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function formatDuration(start, end) {
  if (!start || !end) return "";
  const seconds = Math.max(0, Math.round((new Date(end) - new Date(start)) / 1000));
  if (!Number.isFinite(seconds)) return "";
  if (seconds < 60) return `${seconds} 秒`;
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return `${minutes} 分 ${String(rest).padStart(2, "0")} 秒`;
}

function formatBytes(bytes) {
  const size = Number(bytes || 0);
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(size < 10240 ? 1 : 0)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

function showToast(message, { error = false } = {}) {
  const toast = document.createElement("div");
  toast.className = `toast${error ? " is-error" : ""}`;
  toast.innerHTML = `${icon(error ? "alert" : "check")}<span>${escapeHtml(message)}</span>`;
  elements.toastRegion.append(toast);
  window.setTimeout(() => toast.remove(), 4200);
}

function setLoading(loading) {
  elements.refreshButton.classList.toggle("is-spinning", loading);
  elements.refreshButton.disabled = loading;
}

async function loadOverview({ silent = false, refreshDetail = true } = {}) {
  if (!silent) setLoading(true);
  try {
    const overview = await requestJson(apiPath("/overview"));
    state.overview = overview;
    const selectedExists = overview.threads.some(
      (thread) => thread.thread_id === state.selectedThreadId,
    );
    if (!selectedExists) {
      state.selectedThreadId =
        overview.attention.find((item) => item.thread_id)?.thread_id ||
        overview.threads[0]?.thread_id ||
        null;
    }
    if (state.selectedThreadId) {
      localStorage.setItem("copenguin:selected-thread", state.selectedThreadId);
    } else {
      localStorage.removeItem("copenguin:selected-thread");
    }
    renderCounts();
    renderThreadGroups();
    renderAttentionRail();
    renderCurrentView();
    if (refreshDetail && ["overview", "tasks"].includes(state.view) && state.selectedThreadId) {
      await loadThreadDetail(state.selectedThreadId, { silent: true });
    }
  } catch (error) {
    if (!silent) {
      renderFatalError(error.message);
      showToast(error.message, { error: true });
    }
  } finally {
    if (!silent) setLoading(false);
  }
}

function renderCounts() {
  const count = state.overview?.counts?.attention || 0;
  for (const element of [elements.navAttentionCount, elements.bottomAttentionCount]) {
    element.textContent = String(count);
    element.hidden = count === 0;
  }
}

function filteredThreads() {
  const threads = state.overview?.threads || [];
  if (state.view !== "attention") return threads;
  const attentionThreadIds = new Set(
    (state.overview?.attention || []).map((item) => item.thread_id).filter(Boolean),
  );
  return threads.filter((thread) => attentionThreadIds.has(thread.thread_id));
}

function renderThreadGroups() {
  if (!state.overview) return;
  const threads = filteredThreads();
  elements.railFilter.hidden = state.view !== "attention";
  elements.railFilterLabel.textContent = `需要处理的任务 ${threads.length}`;
  if (threads.length === 0) {
    elements.threadGroups.innerHTML = `
      <div class="rail-empty">
        ${state.view === "attention" ? "当前没有需要你处理的任务。" : "还没有任务。用上面的输入框描述一个想要的结果。"}
      </div>`;
    return;
  }
  const groups = new Map();
  for (const thread of threads) {
    const project = thread.project_id || "未分类";
    if (!groups.has(project)) groups.set(project, []);
    groups.get(project).push(thread);
  }
  elements.threadGroups.innerHTML = [...groups.entries()]
    .map(
      ([project, projectThreads]) => `
        <section class="thread-group">
          <h2 class="thread-group-heading">
            ${icon("chevron-down")}
            <span>${escapeHtml(project)}</span>
          </h2>
          <div>
            ${projectThreads.map(renderThreadItem).join("")}
          </div>
        </section>`,
    )
    .join("");
}

function renderThreadItem(thread) {
  const tone = stateTone(thread);
  const selected = thread.thread_id === state.selectedThreadId;
  return `
    <button
      class="thread-item${selected ? " is-selected" : ""}"
      type="button"
      data-thread-id="${escapeHtml(thread.thread_id)}"
      aria-pressed="${selected}"
    >
      <span class="thread-state-mark" data-tone="${tone}">${icon(stateIcon(tone))}</span>
      <span class="thread-item-main">
        <span class="thread-title-row">
          <strong class="thread-title">${escapeHtml(thread.title)}</strong>
          <time class="thread-time" datetime="${escapeHtml(thread.updated_at)}">${escapeHtml(relativeTime(thread.updated_at))}</time>
        </span>
        <span class="thread-status" data-tone="${tone}">${escapeHtml(stateLabel(thread))}</span>
        <span class="thread-progress" aria-hidden="true">
          <span class="${escapeHtml(progressClass(thread.actual_state))}"></span>
        </span>
      </span>
      ${icon("chevron-right").replace("<svg", '<svg class="thread-chevron"')}
    </button>`;
}

function renderAttentionRail() {
  const attention = state.overview?.attention || [];
  if (attention.length === 0) {
    elements.attentionList.innerHTML = `
      <div class="attention-empty">
        <img src="/control-room/static/copenguin-logo.svg" alt="" />
        <span>没有等待你的决定。CoPenguin 会把真正需要你的事项放在这里。</span>
      </div>`;
    return;
  }
  elements.attentionList.innerHTML = attention.slice(0, 6).map(renderAttentionItem).join("");
}

function attentionCopy(item) {
  if (item.kind === "delivery") return "打开交付物，检查证据后决定下一步。";
  if (item.kind === "input") return "任务需要一条明确补充，之后才能继续。";
  if (item.kind === "approval") return item.capability
    ? `外部能力 ${item.capability} 等待批准。`
    : "一个受治理动作正在等待批准。";
  if (item.kind === "route") return "消息归属不明确，尚未启动新的工作。";
  if (item.kind === "conflict") return "任务之间存在资源或上下文冲突。";
  if (item.kind === "failure") return "运行失败，尚未自动扩大重试范围。";
  return "需要你的决定。";
}

function attentionLabel(item) {
  return {
    delivery: "等待验收",
    input: "等待补充",
    approval: "等待批准",
    route: "等待确认归属",
    conflict: "发现冲突",
    failure: "运行失败",
  }[item.kind] || "需要处理";
}

function renderAttentionItem(item, index = 0) {
  const target = item.thread_id
    ? `data-thread-id="${escapeHtml(item.thread_id)}"`
    : `data-attention-id="${escapeHtml(item.attention_id)}"`;
  return `
    <button class="attention-item${index === 0 ? " is-primary" : ""}" type="button" ${target}>
      ${icon(item.kind === "failure" ? "alert" : item.kind === "approval" ? "shield" : "clock")}
      <span>
        <strong>${escapeHtml(item.title)}</strong>
        <span class="attention-item-state">
          <span>${escapeHtml(attentionLabel(item))}</span>
          <time datetime="${escapeHtml(item.updated_at)}">${escapeHtml(relativeTime(item.updated_at))}</time>
        </span>
        <p>${escapeHtml(attentionCopy(item))}</p>
      </span>
    </button>`;
}

async function selectThread(threadId) {
  state.selectedThreadId = threadId;
  state.view = "overview";
  localStorage.setItem("copenguin:selected-thread", threadId);
  syncViewControls();
  renderThreadGroups();
  elements.shell.classList.add("mobile-detail-open");
  elements.shell.classList.remove("show-attention");
  await loadThreadDetail(threadId);
  elements.detailPane.focus({ preventScroll: true });
}

async function loadThreadDetail(threadId, { silent = false } = {}) {
  const requestId = ++state.detailRequest;
  if (!silent) {
    elements.detailPane.innerHTML = `<div class="detail-loading"><span class="loading-ring"></span><p>正在整理这条任务的运行记录…</p></div>`;
  }
  try {
    const detail = await requestJson(apiPath(`/threads/${encodeURIComponent(threadId)}`));
    if (requestId !== state.detailRequest || threadId !== state.selectedThreadId) return;
    state.detail = detail;
    renderThreadDetail();
    const latest = detail.deliveries?.[0]?.delivery;
    if (latest?.state === "prepared") {
      presentDelivery(latest.delivery_id);
    }
  } catch (error) {
    if (requestId !== state.detailRequest) return;
    elements.detailPane.innerHTML = `
      <div class="detail-empty">
        ${icon("alert")}
        <h2>无法读取这条任务</h2>
        <p>${escapeHtml(error.message)}</p>
      </div>`;
    showToast(error.message, { error: true });
  }
}

function renderThreadDetail() {
  const detail = state.detail;
  if (!detail || detail.thread.thread_id !== state.selectedThreadId) return;
  const thread = detail.thread;
  const tone = stateTone(thread);
  const latestRun = detail.runs?.[0]?.run;
  const objective = detail.task?.objective || thread.title;
  elements.detailPane.innerHTML = `
    <article class="detail-content">
      <header class="detail-header">
        <div class="detail-header-top">
          <div>
            <div class="detail-project">${icon("branch")}<span>${escapeHtml(thread.project_id)} / ${escapeHtml(thread.current_branch_id)}</span></div>
            <h1 class="detail-title">${escapeHtml(thread.title)}</h1>
          </div>
          <div class="detail-refresh">
            <span>${escapeHtml(relativeTime(thread.updated_at))}刷新</span>
            <button class="icon-button" type="button" data-refresh-detail aria-label="刷新任务">${icon("refresh")}</button>
          </div>
        </div>
        <div class="detail-status-line" data-tone="${tone}">
          ${icon(stateIcon(tone))}
          <span>${escapeHtml(stateLabel(thread))}</span>
        </div>
        <p class="detail-objective"><strong>目标：</strong>${escapeHtml(objective)}</p>
      </header>

      <nav class="detail-tabs" aria-label="任务详情区块">
        <button class="detail-tab is-active" type="button" data-tab-target="runs">运行记录</button>
        <button class="detail-tab" type="button" data-tab-target="artifacts">产出物</button>
        <button class="detail-tab" type="button" data-tab-target="deliveries">交付（验收）</button>
        <button class="detail-tab" type="button" data-tab-target="context">相关信息</button>
      </nav>

      <section class="detail-section" id="detail-runs">
        <div class="section-heading">
          <h2>运行记录</h2>
          <span>${detail.runs.length} 个 Run${latestRun?.supersedes_run_id ? " · 保留旧版本" : ""}</span>
        </div>
        <div class="run-list">${renderRuns(detail.runs)}</div>
      </section>

      <section class="detail-section" id="detail-artifacts">
        <div class="section-heading">
          <h2>产出物</h2>
          <span>${detail.deliveries.length ? "来自已冻结的 Delivery" : "尚无交付"}</span>
        </div>
        <div class="artifact-list">${renderArtifacts(detail.deliveries)}</div>
      </section>

      <section class="detail-section" id="detail-deliveries">
        <div class="section-heading">
          <h2>交付（验收）</h2>
          <span>决定会写入 Runtime</span>
        </div>
        <div class="delivery-stack">${renderDeliveries(detail.deliveries)}</div>
      </section>

      <section class="detail-section" id="detail-context">
        <div class="section-heading"><h2>相关信息</h2><span>只显示可验证状态</span></div>
        ${renderContext(detail)}
      </section>
    </article>`;
}

function renderRuns(runs) {
  if (!runs?.length) {
    return `<div class="run-empty">这条任务还没有 Run。它可能仍在澄清范围，或者只是普通对话。</div>`;
  }
  return runs
    .map((entry, index) => {
      const run = entry.run;
      const tone = runTone(run.state);
      const runNumber = runs.length - index;
      const duration = formatDuration(run.started_at, run.completed_at);
      return `
        <details class="run-panel" ${index === 0 ? "open" : ""}>
          <summary>
            <span class="run-summary-title">
              <strong>Run ${runNumber}</strong>
              <span class="run-state" data-tone="${tone}">${icon(stateIcon(tone))}${escapeHtml(RUN_STATES[run.state] || run.state)}</span>
            </span>
            <span class="run-meta">${duration ? `耗时 ${escapeHtml(duration)}` : escapeHtml(formatAbsoluteTime(run.created_at))}</span>
            ${icon("chevron-down")}
          </summary>
          <div class="step-list">
            ${entry.steps?.length ? entry.steps.map((step) => renderStep(step)).join("") : '<div class="run-empty">这个 Run 尚未记录 Step。</div>'}
          </div>
        </details>`;
    })
    .join("");
}

function renderStep(step) {
  const tone = stepTone(step.state);
  const duration = formatDuration(step.started_at, step.completed_at);
  return `
    <div class="step-row">
      <span class="step-mark" data-tone="${tone}">${icon(tone === "failure" ? "x" : tone === "complete" ? "check" : "clock")}</span>
      <span class="step-kind">Step ${escapeHtml(step.ordinal)}</span>
      <span class="step-name">${escapeHtml(STEP_LABELS[step.kind] || step.kind)}${step.attempt > 1 ? ` · 第 ${step.attempt} 次尝试` : ""}</span>
      <span class="step-time">${escapeHtml(duration || STEP_STATES[step.state] || step.state)}</span>
    </div>`;
}

function renderArtifacts(deliveries) {
  if (!deliveries?.length) {
    return `<div class="section-empty">还没有可检查的 Artifact。运行完成并通过 Verifier 后，它会出现在这里。</div>`;
  }
  const latest = deliveries[0];
  const artifact = latest.primary_artifact;
  const verifier = latest.verifier_artifact;
  const checks = verifier.checks || {};
  const passedChecks = Object.values(checks).filter(Boolean).length;
  const evidence = [
    `引用 ${artifact.citation_count || 0} 个冻结来源`,
    verifier.verdict === "passed" ? `Verifier 已通过 ${passedChecks} 项检查` : "Verifier 证据可打开检查",
    `导出策略：${latest.delivery.export_policy === "local_only" ? "仅限本地" : latest.delivery.export_policy}`,
  ];
  return `
    <div class="artifact-panel">
      <button class="artifact-row" type="button" data-artifact-id="${escapeHtml(artifact.artifact_id)}">
        <span class="artifact-icon">${icon("file")}</span>
        <span class="artifact-copy">
          <strong>${escapeHtml(artifact.title || "Project Decision Record")}</strong>
          <span>${escapeHtml(artifact.artifact_type || artifact.format)} · ${escapeHtml(formatBytes(artifact.size_bytes))} · v${latest.delivery.version}</span>
        </span>
        <span class="artifact-open">打开</span>
      </button>
      <div class="evidence-summary">
        <h3>证据摘要</h3>
        <ul class="evidence-list">${evidence.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>
      </div>
    </div>`;
}

function renderDeliveries(deliveries) {
  if (!deliveries?.length) {
    return `<div class="section-empty">Delivery 尚未准备。完成和验证是不同阶段，只有准备好的交付才会要求你决定。</div>`;
  }
  return deliveries.map((entry, index) => renderDelivery(entry, index === 0)).join("");
}

function deliveryOutcome(entry) {
  return entry.primary_artifact.purpose || `已生成可检查的「${entry.primary_artifact.title || "本地产出物"}」。`;
}

function renderDelivery(entry, isLatest) {
  const delivery = entry.delivery;
  const pending = delivery.state === "presented" && isLatest;
  const mode = state.decisionMode.get(delivery.delivery_id);
  const terminal = !["prepared", "presented"].includes(delivery.state);
  const changes = delivery.previous_delivery_id
    ? "本次修改创建了新的 Run 和 Delivery；上一版产物与证据仍完整保留。"
    : "这是这条任务的首次版本化交付。";
  return `
    <article class="delivery-panel${terminal ? " is-terminal" : ""}">
      <div class="delivery-heading">
        <h3>Delivery v${delivery.version}</h3>
        <span class="delivery-version">${escapeHtml(DELIVERY_STATES[delivery.state] || delivery.state)}</span>
      </div>
      <p class="delivery-outcome">${escapeHtml(deliveryOutcome(entry))}</p>
      <p class="delivery-changes"><strong>主要变化：</strong>${escapeHtml(changes)}</p>
      ${pending ? renderDecisionActions(delivery, mode) : renderDeliveryResult(delivery)}
    </article>`;
}

function renderDecisionActions(delivery, mode) {
  const allowed = new Set(delivery.allowed_decisions || []);
  const button = (decision, iconName, extraClass = "") => `
    <button
      class="${decision === "reject" ? "danger-button" : `decision-button ${extraClass}`}${mode === decision ? " is-selected" : ""}"
      type="button"
      data-decision-action="${decision}"
      data-delivery-id="${escapeHtml(delivery.delivery_id)}"
      ${allowed.has(decision) ? "" : "disabled"}
    >${icon(iconName)}<span>${DECISION_COPY[decision]}</span></button>`;
  let editor = "";
  if (mode === "revise") {
    editor = `
      <form class="decision-editor" data-revision-form data-delivery-id="${escapeHtml(delivery.delivery_id)}">
        <label for="revision-${escapeHtml(delivery.delivery_id)}">修改建议（将用于下一次生成）</label>
        <textarea id="revision-${escapeHtml(delivery.delivery_id)}" maxlength="4000" required placeholder="说明需要调整的内容、方向或约束…"></textarea>
        <div class="decision-editor-footer">
          <span>旧 Delivery 不会被覆盖</span>
          <button class="primary-button" type="submit">提交修改</button>
        </div>
      </form>`;
  } else if (["reject", "take_over"].includes(mode)) {
    const reject = mode === "reject";
    editor = `
      <div class="decision-confirm">
        <p>${reject ? "拒绝会保留这次交付与证据，但不会继续生成新版本。" : "接管会暂停 Thread 的期望状态，后续不会自动开工。"}</p>
        <button class="${reject ? "danger-button" : "secondary-button"}" type="button" data-confirm-decision="${mode}" data-delivery-id="${escapeHtml(delivery.delivery_id)}">
          确认${reject ? "拒绝" : "接管"}
        </button>
      </div>`;
  }
  return `
    <div class="decision-actions">
      ${button("accept", "check", "is-primary")}
      ${button("revise", "file")}
      ${button("defer", "clock")}
      ${button("take_over", "user")}
      ${button("reject", "x")}
    </div>
    ${editor}`;
}

function renderDeliveryResult(delivery) {
  const tone = delivery.state === "accepted"
    ? "complete"
    : delivery.state === "rejected"
      ? "failure"
      : "attention";
  const iconName = tone === "complete" ? "check" : tone === "failure" ? "x" : "clock";
  const suffix = delivery.revision_run_id
    ? `，新的 Run ${delivery.revision_run_id.slice(0, 8)}… 已排队`
    : "";
  return `
    <div class="delivery-result" data-tone="${tone}">
      ${icon(iconName)}
      <span>${escapeHtml(DELIVERY_STATES[delivery.state] || delivery.state)}${escapeHtml(suffix)}</span>
    </div>`;
}

function renderContext(detail) {
  const thread = detail.thread;
  const latestRun = detail.runs?.[0]?.run;
  const rows = [
    ["Project / Branch", `${thread.project_id} / ${thread.current_branch_id}`],
    ["Replay", detail.replay_verified ? "投影与事件重放一致" : "重放检查未通过"],
    ["期望状态", thread.desired_state === "run" ? "允许运行" : THREAD_STATES[thread.desired_state] || thread.desired_state],
    ["执行配方", latestRun?.executor_key || "尚未分配"],
  ];
  return `<div class="view-list">${rows
    .map(
      ([label, value]) => `
        <div class="view-row">
          <span class="view-row-icon" data-tone="${label === "Replay" && detail.replay_verified ? "complete" : ""}">${icon(label === "Replay" ? "shield" : "branch")}</span>
          <span class="view-row-main"><strong>${escapeHtml(label)}</strong><p>${escapeHtml(value)}</p></span>
        </div>`,
    )
    .join("")}</div>`;
}

async function presentDelivery(deliveryId) {
  if (state.presentingDeliveries.has(deliveryId)) return;
  state.presentingDeliveries.add(deliveryId);
  try {
    await requestJson(`/runtime/deliveries/${encodeURIComponent(deliveryId)}/present`, {
      method: "POST",
    });
    await loadOverview({ silent: true, refreshDetail: false });
    await loadThreadDetail(state.selectedThreadId, { silent: true });
  } catch (error) {
    showToast(`无法展示 Delivery：${error.message}`, { error: true });
  } finally {
    state.presentingDeliveries.delete(deliveryId);
  }
}

function setDecisionMode(deliveryId, mode) {
  const current = state.decisionMode.get(deliveryId);
  if (current === mode) state.decisionMode.delete(deliveryId);
  else state.decisionMode.set(deliveryId, mode);
  renderThreadDetail();
  if (mode === "revise") {
    window.requestAnimationFrame(() => {
      document.querySelector(`#revision-${CSS.escape(deliveryId)}`)?.focus();
    });
  }
}

function simpleHash(value) {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(16);
}

function decisionKey(deliveryId, decision, revisionRequest = "") {
  const storageKey = `copenguin:decision-key:${deliveryId}:${decision}:${simpleHash(revisionRequest)}`;
  let value = localStorage.getItem(storageKey);
  if (!value) {
    value = `control-room:${deliveryId}:${decision}:${crypto.randomUUID()}`;
    localStorage.setItem(storageKey, value);
  }
  return value;
}

async function submitDecision(deliveryId, decision, revisionRequest = null) {
  const controls = document.querySelectorAll(`[data-delivery-id="${CSS.escape(deliveryId)}"] button, button[data-delivery-id="${CSS.escape(deliveryId)}"]`);
  controls.forEach((control) => { control.disabled = true; });
  try {
    await requestJson(`/runtime/deliveries/${encodeURIComponent(deliveryId)}/decision`, {
      method: "POST",
      body: JSON.stringify({
        decision,
        actor_id: "owner",
        idempotency_key: decisionKey(deliveryId, decision, revisionRequest || ""),
        reason: "Decided in the local Control Room.",
        revision_request: revisionRequest,
      }),
    });
    state.decisionMode.delete(deliveryId);
    showToast(`${DECISION_COPY[decision]}决定已写入 Runtime。`);
    await loadOverview({ silent: true, refreshDetail: false });
    await loadThreadDetail(state.selectedThreadId, { silent: true });
  } catch (error) {
    showToast(error.message, { error: true });
    controls.forEach((control) => { control.disabled = false; });
  }
}

function syncViewControls() {
  document.querySelectorAll("[data-view]").forEach((button) => {
    const active = button.dataset.view === state.view;
    button.classList.toggle("is-active", Boolean(active));
  });
}

function setView(view) {
  state.view = view;
  syncViewControls();
  elements.shell.classList.remove("show-attention");
  if (["overview", "tasks"].includes(view)) {
    renderThreadGroups();
    if (state.selectedThreadId) {
      elements.shell.classList.add("mobile-detail-open");
      loadThreadDetail(state.selectedThreadId, { silent: Boolean(state.detail) });
    } else {
      renderEmptyDetail();
    }
  } else {
    elements.shell.classList.add("mobile-detail-open");
    renderThreadGroups();
    renderCurrentView();
  }
}

function renderCurrentView() {
  if (!state.overview) return;
  if (state.view === "attention") renderAttentionView();
  else if (state.view === "inbox") renderInboxView();
  else if (state.view === "capabilities") renderCapabilitiesView();
  else if (!state.selectedThreadId) renderEmptyDetail();
}

function renderEmptyDetail() {
  state.detail = null;
  elements.detailPane.innerHTML = `
    <div class="detail-empty">
      <img src="/control-room/static/copenguin-logo.svg" alt="" />
      <h2>把一件事交给 CoPenguin</h2>
      <p>描述你想得到的结果。每件工作都会进入独立 Thread，不会与其他任务混在一起。</p>
    </div>`;
}

function renderAttentionView() {
  const attention = state.overview?.attention || [];
  elements.detailPane.innerHTML = `
    <section class="full-view">
      <header class="full-view-header">
        <div><h1>需要我处理</h1><p>这里只出现会阻塞任务、改变权限或决定交付去向的事项。</p></div>
        <span>${attention.length} 项</span>
      </header>
      <div class="view-list">
        ${attention.length ? attention.map((item) => renderAttentionViewRow(item)).join("") : '<div class="section-empty">现在没有需要你的决定。正在运行的任务会继续保持隔离。</div>'}
      </div>
    </section>`;
}

function renderAttentionViewRow(item) {
  const target = item.thread_id ? `data-thread-id="${escapeHtml(item.thread_id)}"` : "";
  return `
    <button class="view-row" type="button" ${target}>
      <span class="view-row-icon" data-tone="attention">${icon(item.kind === "approval" ? "shield" : "clock")}</span>
      <span class="view-row-main"><strong>${escapeHtml(item.title)}</strong><p>${escapeHtml(attentionCopy(item))}</p></span>
      <time class="view-row-meta">${escapeHtml(relativeTime(item.updated_at))}</time>
    </button>`;
}

function renderInboxView() {
  const inbox = state.overview?.inbox || [];
  elements.detailPane.innerHTML = `
    <section class="full-view">
      <header class="full-view-header">
        <div><h1>收件箱</h1><p>每条消息先在这里留下身份和路由决定，再进入对话或独立任务。</p></div>
        <span>${inbox.length} 条最近记录</span>
      </header>
      <div class="view-list">
        ${inbox.length ? inbox.map(renderInboxRow).join("") : '<div class="section-empty">还没有消息。你可以从任务输入框开始。</div>'}
      </div>
    </section>`;
}

function renderInboxRow(record) {
  const proposed = record.route_state === "proposed";
  return `
    <div class="view-row">
      <span class="view-row-icon" data-tone="${proposed ? "attention" : "complete"}">${icon(proposed ? "clock" : "inbox")}</span>
      <span class="view-row-main">
        <strong>${escapeHtml(routeLabel(record.route_type, record.route_state))}</strong>
        <p>${escapeHtml(record.rationale || "路由决定已持久化。")}</p>
      </span>
      <time class="view-row-meta">${escapeHtml(relativeTime(record.updated_at || record.created_at))}</time>
    </div>`;
}

function routeLabel(routeType, routeState) {
  const route = {
    chat: "普通对话",
    new_task: "新任务",
    thread_update: "任务更新",
    control: "控制命令",
    ambiguous: "待确认归属",
  }[routeType] || routeType;
  return routeState === "proposed" ? `${route} · 尚未开工` : route;
}

function renderCapabilitiesView() {
  const capabilities = state.overview?.capabilities || {};
  const memory = capabilities.memory || {};
  const knowledge = capabilities.knowledge || {};
  const computer = capabilities.computer || {};
  const controlRoom = capabilities.control_room || {};
  elements.detailPane.innerHTML = `
    <section class="full-view">
      <header class="full-view-header">
        <div><h1>记忆与权限</h1><p>先看清当前能力边界，再决定是否在后续版本中扩大授权。</p></div>
      </header>
      <section class="capability-section">
        <h2>上下文能力</h2>
        ${capabilityRow("私人记忆", memory.enabled, memory.enabled ? "已连接受治理 adapter；当前界面不能直接写入或晋升记忆。" : "已关闭，不会读取或写入私人记忆。")}
        ${capabilityRow("知识体系", knowledge.enabled, knowledge.enabled ? "已连接 EvolveKB adapter；检索结果不等于行动权限。" : "已关闭，不会读取或晋升知识。")}
      </section>
      <section class="capability-section">
        <h2>动作与本地边界</h2>
        ${capabilityRow("电脑动作", computer.provider !== "dry-run", `Provider：${computer.provider || "dry-run"}；${computer.approval_required ? "外部动作需要审批" : "当前配置未强制审批"}。`)}
        ${capabilityRow("Control Room", controlRoom.transport === "loopback_only", "数据 API 仅允许 loopback；V2-010 之前没有独立本地会话认证。")}
      </section>
      <div class="capability-notice">此页面只披露能力状态，不会改变权限。Memory、KB、Skill、Hook 和权限仍必须经过独立治理门。</div>
    </section>`;
}

function capabilityRow(label, enabled, detail) {
  return `
    <div class="capability-row">
      <span><strong>${escapeHtml(label)}</strong><p>${escapeHtml(detail)}</p></span>
      <span class="capability-state${enabled ? " is-on" : ""}">${enabled ? "可用" : "关闭 / 受限"}</span>
    </div>`;
}

async function submitComposer(event) {
  event.preventDefault();
  const text = elements.composerText.value.trim();
  if (!text) {
    showToast("请先描述想要的结果。", { error: true });
    elements.composerText.focus();
    return;
  }
  const mode = elements.composerMode.value;
  if (mode === "continue" && !state.selectedThreadId) {
    showToast("请先选择要继续的任务。", { error: true });
    return;
  }
  const submit = elements.composer.querySelector("button[type='submit']");
  submit.disabled = true;
  try {
    const activeThreadIds = (state.overview?.threads || [])
      .filter((thread) => !["archived", "cancelled"].includes(thread.actual_state))
      .map((thread) => thread.thread_id);
    const payload = await requestJson("/runtime/inbox", {
      method: "POST",
      body: JSON.stringify({
        message_id: `control-room-${crypto.randomUUID()}`,
        chat_id: "control-room",
        actor_id: "owner",
        project_id: elements.composerProject.value.trim() || "personal",
        current_thread_id: mode === "continue" ? state.selectedThreadId : null,
        active_thread_ids: activeThreadIds,
        text: mode === "new" ? `/task ${text}` : text,
      }),
    });
    elements.composerText.value = "";
    const record = payload.message;
    if (record.route_state === "proposed") {
      showToast("任务归属还不明确，已放入“需要我处理”，尚未开工。", { error: true });
    } else {
      showToast(mode === "new" ? "新任务已进入独立 Thread。" : "补充信息已写入当前 Thread。");
    }
    if (record.thread_id) state.selectedThreadId = record.thread_id;
    await loadOverview({ refreshDetail: true });
  } catch (error) {
    showToast(error.message, { error: true });
  } finally {
    submit.disabled = false;
  }
}

async function openArtifact(artifactId) {
  elements.artifactDialogTitle.textContent = "正在打开…";
  elements.artifactDialogBody.innerHTML = `<div class="detail-loading"><span class="loading-ring"></span><p>正在验证 Artifact 摘要…</p></div>`;
  elements.artifactDialogMeta.textContent = artifactId;
  elements.artifactDialog.showModal();
  try {
    const artifact = await requestJson(apiPath(`/artifacts/${encodeURIComponent(artifactId)}`));
    elements.artifactDialogTitle.textContent = artifact.title || "本地 Artifact";
    elements.artifactDialogMeta.textContent = `${formatBytes(artifact.size_bytes)} · ${artifact.sha256?.slice(0, 16)}…${artifact.truncated ? " · 预览已截断" : ""}`;
    elements.artifactDialogBody.innerHTML = renderArtifactContent(artifact);
  } catch (error) {
    elements.artifactDialogTitle.textContent = "无法打开 Artifact";
    elements.artifactDialogBody.innerHTML = `<div class="section-empty">${escapeHtml(error.message)}</div>`;
  }
}

function renderArtifactContent(artifact) {
  const content = artifact.content;
  if (content && typeof content === "object" && content.artifact_type === "project_decision_record") {
    const sections = content.sections || {};
    const sectionOrder = [
      ["background_and_problem", "背景与问题"],
      ["confirmed_facts", "已确认事实"],
      ["decisions", "决定"],
      ["action_items", "下一步"],
      ["open_questions", "未决问题"],
      ["risks", "风险"],
    ];
    return `
      <article class="artifact-document">
        <header class="artifact-document-header"><h3>${escapeHtml(content.title || artifact.title)}</h3><p>${escapeHtml(content.purpose || "本地产出的项目决策记录")}</p></header>
        ${sectionOrder
          .filter(([key]) => Array.isArray(sections[key]) && sections[key].length)
          .map(([key, label]) => `<section><h4>${label}</h4><ul>${sections[key].map((item) => `<li>${escapeHtml(typeof item === "string" ? item : JSON.stringify(item))}</li>`).join("")}</ul></section>`)
          .join("")}
      </article>`;
  }
  if (content && typeof content === "object" && content.artifact_type === "verifier_result") {
    const checks = Object.entries(content.checks || {});
    return `
      <article class="artifact-document">
        <header class="artifact-document-header"><h3>验证结果：${escapeHtml(content.verdict || "unknown")}</h3><p>每一项检查都来自版本化 Verifier 结果，而不是界面推断。</p></header>
        <div class="verification-checks">${checks.map(([key, value]) => `<div class="verification-check">${icon(value ? "check" : "x")}<span>${escapeHtml(key)}：${value ? "通过" : "未通过"}</span></div>`).join("")}</div>
      </article>`;
  }
  const raw = typeof content === "string" ? content : JSON.stringify(content, null, 2);
  return `<pre class="artifact-raw">${escapeHtml(raw || "此 Artifact 不是可显示的文本格式。")}</pre>`;
}

function renderFatalError(message) {
  elements.threadGroups.innerHTML = `<div class="rail-empty">无法读取本地 Runtime：${escapeHtml(message)}</div>`;
  elements.detailPane.innerHTML = `
    <div class="detail-empty">
      ${icon("alert")}
      <h2>Control Room 暂时不可用</h2>
      <p>${escapeHtml(message)}</p>
    </div>`;
}

document.addEventListener("click", (event) => {
  const viewButton = event.target.closest("[data-view]");
  if (viewButton) {
    setView(viewButton.dataset.view);
    return;
  }
  const threadButton = event.target.closest("[data-thread-id]");
  if (threadButton?.dataset.threadId) {
    selectThread(threadButton.dataset.threadId);
    return;
  }
  const artifactButton = event.target.closest("[data-artifact-id]");
  if (artifactButton) {
    openArtifact(artifactButton.dataset.artifactId);
    return;
  }
  const decisionButton = event.target.closest("[data-decision-action]");
  if (decisionButton) {
    const decision = decisionButton.dataset.decisionAction;
    const deliveryId = decisionButton.dataset.deliveryId;
    if (["revise", "reject", "take_over"].includes(decision)) {
      setDecisionMode(deliveryId, decision);
    } else {
      submitDecision(deliveryId, decision);
    }
    return;
  }
  const confirmDecision = event.target.closest("[data-confirm-decision]");
  if (confirmDecision) {
    submitDecision(confirmDecision.dataset.deliveryId, confirmDecision.dataset.confirmDecision);
    return;
  }
  const tab = event.target.closest("[data-tab-target]");
  if (tab) {
    document.querySelectorAll(".detail-tab").forEach((item) => item.classList.remove("is-active"));
    tab.classList.add("is-active");
    document.querySelector(`#detail-${CSS.escape(tab.dataset.tabTarget)}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
    return;
  }
  if (event.target.closest("[data-refresh-detail]") && state.selectedThreadId) {
    loadThreadDetail(state.selectedThreadId);
  }
});

document.addEventListener("submit", (event) => {
  const form = event.target.closest("[data-revision-form]");
  if (!form) return;
  event.preventDefault();
  const text = form.querySelector("textarea").value.trim();
  if (!text) {
    showToast("请写明需要修改的内容。", { error: true });
    return;
  }
  submitDecision(form.dataset.deliveryId, "revise", text);
});

elements.composer.addEventListener("submit", submitComposer);
elements.refreshButton.addEventListener("click", () => loadOverview());
elements.clearFilter.addEventListener("click", () => setView("overview"));
elements.mobileTaskBack.addEventListener("click", () => elements.shell.classList.remove("mobile-detail-open"));
elements.attentionRail.querySelector(".attention-close").addEventListener("click", () => elements.shell.classList.remove("show-attention"));
elements.artifactDialogClose.addEventListener("click", () => elements.artifactDialog.close());
elements.artifactDialogDone.addEventListener("click", () => elements.artifactDialog.close());
elements.artifactDialog.addEventListener("click", (event) => {
  if (event.target === elements.artifactDialog) elements.artifactDialog.close();
});
elements.composerMode.addEventListener("change", () => {
  elements.composerProject.disabled = elements.composerMode.value === "continue";
});

window.addEventListener("keydown", (event) => {
  if (event.key === "Escape") elements.shell.classList.remove("show-attention");
  if ((event.metaKey || event.ctrlKey) && event.key === "k") {
    event.preventDefault();
    elements.shell.classList.remove("mobile-detail-open");
    elements.composerText.focus();
  }
});

window.setInterval(() => {
  const active = document.activeElement;
  const editing = active?.matches("textarea, input, select") || elements.artifactDialog.open;
  if (!editing) loadOverview({ silent: true, refreshDetail: true });
}, 30_000);

syncViewControls();
loadOverview();
