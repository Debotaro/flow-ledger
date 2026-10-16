"use strict";
(() => {
  const $ = (id) => document.getElementById(id);
  const statuses = {
    queued: "Queued",
    running: "Running",
    retry_wait: "Retry scheduled",
    pending_approval: "Needs approval",
    succeeded: "Succeeded",
    dead_letter: "Dead letter",
    rejected: "Rejected",
  };
  const state = {
    health: null,
    overview: null,
    jobs: [],
    cases: [],
    audit: [],
    report: null,
    selectedId: null,
    selected: null,
    action: null,
    version: 0,
    loading: false,
  };
  let token = "";
  try {
    token = sessionStorage.getItem("flowledger-token") || "";
  } catch (_) {
    /* Storage policy may restrict persistence; in-memory access still works. */
  }
  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }
  function humanize(value) {
    return String(value || "Unavailable")
      .replace(/_/g, " ")
      .replace(/^./, (letter) => letter.toUpperCase());
  }
  function utc(value) {
    if (!value) return "—";
    const date = new Date(value);
    return Number.isNaN(date.valueOf())
      ? "Unavailable"
      : date
          .toISOString()
          .replace("T", " ")
          .replace(/\.\d{3}Z$/, " UTC");
  }
  function shortId(value) {
    return String(value || "").slice(0, 12);
  }
  function announce(text) {
    $("live-status").textContent = text;
  }
  function message(text, kind = "error") {
    const target = $(kind === "error" ? "global-error" : "global-notice");
    target.textContent = text;
    target.hidden = !text;
    if (text)
      $(kind === "error" ? "global-notice" : "global-error").hidden = true;
  }
  const idleContent = new WeakMap();
  function busy(button, active, text) {
    if (active) {
      if (!idleContent.has(button))
        idleContent.set(button, Array.from(button.childNodes));
      button.textContent = text;
    } else if (idleContent.has(button)) {
      button.replaceChildren(...idleContent.get(button));
      idleContent.delete(button);
    }
    button.disabled = active;
    button.setAttribute("aria-busy", String(active));
  }
  async function api(path, options = {}) {
    const headers = { Accept: "application/json", ...options.headers };
    if (options.body) headers["Content-Type"] = "application/json";
    if (token) headers.Authorization = `Bearer ${token}`;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 30000);
    let response;
    try {
      response = await fetch(path, {
        ...options,
        headers,
        signal: controller.signal,
        credentials: "same-origin",
      });
    } catch (error) {
      throw new Error(
        error.name === "AbortError"
          ? "This request timed out. Refresh the queue to check its recorded state before retrying an action."
          : "Cannot reach the workspace. Check the local server, then use Connection to reconnect. Intake retries must keep the same idempotency key.",
      );
    } finally {
      clearTimeout(timer);
    }
    let data;
    try {
      data = await response.json();
    } catch (_) {
      throw new Error(
        "The server returned an unreadable response. Refresh the queue to check the recorded state.",
      );
    }
    if (!response.ok) {
      const detail = Array.isArray(data.detail)
        ? data.detail.map((item) => item.msg || "Invalid input").join("; ")
        : data.detail;
      const error = new Error(
        response.status === 401
          ? "A valid API token is required. Open Connection to unlock this workspace."
          : String(detail || "The request could not be completed."),
      );
      error.status = response.status;
      if (response.status === 401) {
        $("connection-state").className = "connection-state failed";
        $("connection-label").textContent = "Access required";
      }
      throw error;
    }
    return data;
  }
  function tag(status) {
    return el(
      "span",
      `status-tag status-${Object.hasOwn(statuses, status) ? status : "queued"}`,
      statuses[status] || humanize(status),
    );
  }
  function showView(name, focus = false) {
    const names = {
      queue: "Work queue",
      cases: "Local cases",
      audit: "Audit ledger",
      verification: "Verification",
    };
    if (!Object.hasOwn(names, name)) name = "queue";
    for (const key of Object.keys(names))
      $("view-" + key).hidden = key !== name;
    document.querySelectorAll("[data-view]").forEach((link) => {
      const active = link.dataset.view === name;
      link.classList.toggle("active", active);
      if (active) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    });
    $("view-name").textContent = names[name];
    document.title = `${names[name]} — FlowLedger`;
    if (focus) {
      const heading = $(name + "-title");
      heading.tabIndex = -1;
      heading.focus({ preventScroll: true });
    }
  }
  function renderHealth() {
    $("connection-state").className = "connection-state connected";
    $("connection-label").textContent = state.health.auth_required
      ? "Connected · protected"
      : "Connected · local";
    $("demo-label").hidden = !state.health.demo_mode;
    $("failure-controls").hidden = !state.health.demo_mode;
    if (!state.health.demo_mode) $("event-failure").value = "none";
    $("footer-policy").textContent =
      `${state.health.max_attempts} attempts per run · ${state.health.lease_seconds}s lease · durable outbox`;
    $("connection-description").textContent = state.health.auth_required
      ? "This workspace requires its configured API token. It stays in this browser tab's session storage and is sent only to this application's same-origin API."
      : "This workspace is running in local mode. An API token is optional unless your server is configured to require one. The token stays in this browser tab's session storage.";
  }
  function renderOverview() {
    const overview = state.overview;
    if (!overview) return;
    $("status-summary").replaceChildren();
    for (const [status, label] of Object.entries(statuses)) {
      const button = el(
        "button",
        `status-card ${$("status-filter").value === status ? "selected" : ""}`,
      );
      button.type = "button";
      button.dataset.status = status;
      button.setAttribute(
        "aria-label",
        `Filter ${label}: ${overview.counts[status] || 0} jobs`,
      );
      button.setAttribute(
        "aria-pressed",
        String($("status-filter").value === status),
      );
      button.append(
        el("strong", "", overview.counts[status] || 0),
        el("span", "status-card-label", label),
      );
      button.addEventListener("click", () => {
        $("status-filter").value =
          $("status-filter").value === status ? "" : status;
        refresh().catch((error) => message(error.message));
      });
      $("status-summary").append(button);
    }
    $("nav-case-count").textContent = String(overview.cases);
    $("overview-caption").textContent =
      `${overview.cases} local cases · ${overview.audit_events} audit events · refreshed ${utc(new Date())}`;
  }
  function renderJobs() {
    $("jobs-body").replaceChildren();
    $("queue-count").textContent = String(state.jobs.length);
    $("queue-empty").hidden = state.jobs.length > 0;
    const filtered = Boolean($("status-filter").value);
    $("queue-empty-title").textContent = filtered
      ? "No jobs in this state."
      : "An orderly start.";
    $("queue-empty-description").textContent = filtered
      ? "Choose another status or return to all statuses to see the rest of the queue."
      : "Create an event to follow its journey, or load the fictional demonstration queue.";
    $("seed-button").hidden = filtered;
    document.querySelector(".empty-footnote").hidden = filtered;
    for (const job of state.jobs) {
      const row = el("tr", state.selectedId === job.id ? "selected" : "");
      const subjectCell = el("td");
      const button = el("button", "job-subject", job.subject);
      button.type = "button";
      button.setAttribute("aria-label", `Inspect job: ${job.subject}`);
      button.addEventListener("click", () => selectJob(job.id));
      const subtitle = el("span", "job-subtitle");
      subtitle.append(
        el("span", "", humanize(job.category)),
        el(
          "span",
          `priority-${job.priority}`,
          `${humanize(job.priority)} priority`,
        ),
      );
      subjectCell.append(
        button,
        subtitle,
        el("span", "job-identity", shortId(job.id)),
      );
      const statusCell = el("td");
      statusCell.append(tag(job.status));
      row.append(
        subjectCell,
        statusCell,
        el("td", "attempt-number", `${job.attempts} / ${job.max_attempts}`),
        el("td", "date-column table-date", utc(job.updated_at)),
      );
      $("jobs-body").append(row);
    }
  }
  function renderAuditEntry(item) {
    const entry = el("div", "audit-entry");
    entry.append(
      el("p", "audit-event-title", humanize(item.event)),
      el(
        "div",
        "audit-event-meta",
        `${utc(item.created_at)} · ${item.actor || "System"}`,
      ),
    );
    const detail =
      typeof item.detail === "string"
        ? item.detail
        : JSON.stringify(item.detail || {}, null, 2);
    if (detail && detail !== "{}")
      entry.append(el("p", "audit-event-detail", detail));
    return entry;
  }
  function renderInspector() {
    const panel = $("job-inspector");
    panel.replaceChildren();
    panel.hidden = !state.selectedId;
    $("queue-layout").classList.toggle("split", Boolean(state.selectedId));
    if (!state.selectedId) return;
    if (!state.selected) {
      panel.append(el("p", "loading-copy", "Reading job and its audit trail…"));
      return;
    }
    const { job, audit, case: localCase } = state.selected;
    const header = el("div", "inspector-heading");
    const title = el("div");
    title.append(
      el("p", "micro-label", "JOB DETAIL / " + shortId(job.id)),
      el("h2", "", job.subject),
      tag(job.status),
    );
    const close = el("button", "icon-button", "×");
    close.type = "button";
    close.setAttribute("aria-label", "Close job details");
    close.addEventListener("click", () => {
      state.selectedId = null;
      state.selected = null;
      renderInspector();
      renderJobs();
    });
    header.append(title, close);
    panel.append(header);
    const body = el("div", "inspector-body");
    body.append(el("p", "job-description", job.description));
    const facts = el("dl", "job-facts");
    [
      ["Category", humanize(job.category)],
      ["Priority", humanize(job.priority)],
      ["Local action", humanize(job.action)],
      ["Attempts", `${job.attempts} / ${job.max_attempts}`],
      ["Next attempt", utc(job.next_attempt_at)],
      ["Lease expiry", utc(job.lease_until)],
      ["Replays", job.replay_count],
      ["Event key", job.event_key],
      ["Job ID", job.id],
      ["Created", utc(job.created_at)],
      ["Updated", utc(job.updated_at)],
    ].forEach(([name, value]) =>
      facts.append(el("dt", "", name), el("dd", "", value)),
    );
    body.append(facts);
    if (job.failure_mode && job.failure_mode !== "none")
      body.append(
        el(
          "span",
          "fault-tag",
          `SYNTHETIC FAULT INJECTION · ${job.failure_mode === "once" ? "first attempt fails" : "every attempt fails"}. Local demonstration only.`,
        ),
      );
    if (job.last_error)
      body.append(
        el("p", "job-error", `Last recorded error: ${job.last_error}`),
      );
    if (job.status === "pending_approval") {
      body.append(
        el(
          "p",
          "job-policy",
          "Policy requires a human decision before this job can execute. Approval permits only the local case action shown above.",
        ),
      );
      const actions = el("div", "job-actions");
      const approve = el("button", "button button-primary", "Approve job");
      approve.type = "button";
      approve.addEventListener("click", () => openAction("approve", job));
      const reject = el("button", "button button-secondary", "Reject job");
      reject.type = "button";
      reject.addEventListener("click", () => openAction("reject", job));
      actions.append(approve, reject);
      body.append(actions);
    } else if (job.status === "dead_letter") {
      body.append(
        el(
          "p",
          "job-policy",
          "The attempt budget is exhausted. A recorded replay opens a new bounded attempt budget and preserves the event identity and audit trail. The configured synthetic fault still applies.",
        ),
      );
      const actions = el("div", "job-actions");
      const replay = el("button", "button button-primary", "Replay job");
      replay.type = "button";
      replay.addEventListener("click", () => openAction("replay", job));
      actions.append(replay);
      body.append(actions);
    }
    if (localCase) {
      const effect = el("div", "job-case");
      effect.append(
        el("strong", "", "Local case created"),
        el("p", "", `Case ${localCase.id} · ${humanize(localCase.action)}`),
        el(
          "p",
          "",
          `Recorded ${utc(localCase.created_at)}. This is a database effect, not an external action.`,
        ),
      );
      body.append(effect);
    }
    panel.append(body);
    const auditPanel = el("div", "inspector-audit");
    const auditHeading = el("h3", "", "Complete audit trail");
    auditHeading.append(el("span", "count-pill", (audit || []).length));
    auditPanel.append(auditHeading);
    const timeline = el("div", "audit-timeline");
    for (const item of audit || []) timeline.append(renderAuditEntry(item));
    if (!(audit || []).length)
      timeline.append(el("p", "inline-note", "No audit entries returned."));
    auditPanel.append(timeline);
    panel.append(auditPanel);
  }
  async function selectJob(id, moveFocus = false) {
    state.selectedId = id;
    state.selected = null;
    renderJobs();
    renderInspector();
    location.hash = "queue";
    try {
      const result = await api(`/api/jobs/${encodeURIComponent(id)}`);
      if (state.selectedId !== id) return;
      state.selected = result;
      renderInspector();
      if (moveFocus || window.innerWidth < 1200) {
        $("job-inspector").scrollIntoView({ block: "start" });
      }
      announce(
        `Job loaded: ${result.job.subject}. ${statuses[result.job.status]}.`,
      );
    } catch (error) {
      message(error.message);
      $("job-inspector").replaceChildren(
        el(
          "p",
          "loading-copy text-error",
          "Job details could not be loaded. Select the job again to retry.",
        ),
      );
    }
  }
  function renderCases() {
    $("cases-list").replaceChildren();
    if (!state.cases.length) {
      const empty = el("div", "report-empty");
      empty.append(
        el("h2", "", "An effect worth tracing."),
        el(
          "p",
          "",
          "No local cases have been created. Run a due job in the work queue, then inspect the resulting case here.",
        ),
      );
      $("cases-list").append(empty);
      return;
    }
    for (const item of state.cases) {
      const card = el("article", "case-card");
      const top = el("div", "case-top");
      top.append(
        el("span", "case-index", "CASE / " + shortId(item.id)),
        tag("succeeded"),
      );
      card.append(
        top,
        el("h2", "", item.subject),
        el("p", "case-action", humanize(item.action)),
        el(
          "p",
          "",
          `${humanize(item.category)} · ${humanize(item.priority)} priority`,
        ),
        el("p", "", `Created ${utc(item.created_at)}`),
        el("p", "", `Event key: ${item.event_key}`),
      );
      const source = el(
        "button",
        "button button-secondary",
        "Inspect originating job ↗",
      );
      source.type = "button";
      source.addEventListener("click", () => selectJob(item.job_id, true));
      card.append(source);
      $("cases-list").append(card);
    }
  }
  function renderLedger() {
    $("audit-list").replaceChildren();
    if (!state.audit.length) {
      $("audit-list").append(
        el(
          "p",
          "loading-copy",
          "No recorded events yet. Create an event to begin the ledger.",
        ),
      );
      return;
    }
    for (const item of state.audit) {
      const row = el("article", "ledger-entry");
      const body = el("div", "ledger-description");
      body.append(
        el("strong", "", "Actor label: " + (item.actor || "System")),
        el(
          "span",
          "",
          typeof item.detail === "string"
            ? item.detail
            : JSON.stringify(item.detail || {}),
        ),
      );
      row.append(
        el("time", "ledger-time", utc(item.created_at)),
        el("div", "ledger-event", humanize(item.event)),
        body,
      );
      const button = el("button", "ledger-button", "Inspect job ↗");
      button.type = "button";
      button.addEventListener("click", () => selectJob(item.job_id, true));
      row.append(button);
      $("audit-list").append(row);
    }
  }
  function renderReport() {
    const panel = $("verification-report");
    panel.replaceChildren();
    const report = state.report;
    if (
      !report ||
      report.status !== "complete" ||
      !Array.isArray(report.scenarios)
    ) {
      const empty = el("div", "report-empty");
      empty.append(
        el("h2", "", "Recorded evidence belongs here."),
        el(
          "p",
          "",
          "No completed verification report is available. Run the reproducible local experiment, then refresh this page.",
        ),
        el("code", "", "python -m flowledger.verify"),
        el(
          "p",
          "",
          "The experiment uses its own temporary database. This console reads its report and does not run the experiment.",
        ),
      );
      panel.append(empty);
      return;
    }
    const header = el("div", "verification-heading");
    const intro = el("div");
    intro.append(
      el("h2", "", `${report.passed} of ${report.total} scenarios passed.`),
      el(
        "p",
        "",
        `Recorded ${utc(report.generated_at)} · ${Number(report.elapsed_ms).toFixed(1)} ms total experiment time`,
      ),
    );
    if (report.environment)
      intro.append(
        el(
          "p",
          "",
          `Python ${report.environment.python} · SQLite ${report.environment.sqlite} · ${report.environment.platform}`,
        ),
      );
    header.append(
      intro,
      el(
        "span",
        `status-tag ${report.passed === report.total ? "status-succeeded" : "status-dead_letter"}`,
        "Recorded results",
      ),
    );
    panel.append(header);
    const grid = el("div", "scenario-grid");
    for (const scenario of report.scenarios) {
      const card = el("article", "scenario-card");
      const heading = el("div", "scenario-heading");
      heading.append(
        el("h3", "", humanize(scenario.name)),
        el(
          "span",
          `status-tag ${scenario.passed ? "status-succeeded" : "status-dead_letter"}`,
          scenario.passed ? "Passed" : "Failed",
        ),
      );
      card.append(
        heading,
        el(
          "p",
          "",
          `${Number(scenario.elapsed_ms).toFixed(1)} ms · measured local experiment`,
        ),
      );
      const data = el("dl", "scenario-data");
      for (const [key, value] of Object.entries(scenario.details || {}))
        data.append(
          el("dt", "", humanize(key)),
          el(
            "dd",
            "",
            typeof value === "object" ? JSON.stringify(value, null, 2) : value,
          ),
        );
      card.append(data);
      grid.append(card);
    }
    panel.append(grid);
    if (report.scope) panel.append(el("p", "verification-note", report.scope));
    if (report.settings)
      panel.append(
        el(
          "p",
          "verification-note",
          `Experiment settings: ${report.settings.max_attempts} maximum attempts, ${report.settings.retry_base_seconds}s retry base, ${report.settings.lease_seconds}s lease. Injected clocks and synthetic failures are scoped by the recorded scenarios.`,
        ),
      );
    const raw = el("details", "raw-report");
    raw.append(
      el("summary", "", "Inspect the complete report JSON"),
      el("pre", "", JSON.stringify(report, null, 2)),
    );
    panel.append(raw);
  }
  async function refresh() {
    const version = ++state.version;
    const filter = $("status-filter").value;
    const [overview, jobs, cases, audit] = await Promise.all([
      api("/api/overview"),
      api(
        "/api/jobs" + (filter ? `?status=${encodeURIComponent(filter)}` : ""),
      ),
      api("/api/cases"),
      api("/api/audit"),
    ]);
    if (version !== state.version) return;
    state.overview = overview;
    state.jobs = jobs.jobs || [];
    state.cases = cases.cases || [];
    state.audit = audit.events || [];
    renderOverview();
    renderJobs();
    renderCases();
    renderLedger();
    if (state.selectedId) {
      const id = state.selectedId;
      const result = await api(`/api/jobs/${encodeURIComponent(id)}`);
      if (state.selectedId === id) {
        state.selected = result;
        renderInspector();
      }
    }
  }
  async function loadReport() {
    state.report = await api("/api/verification");
    renderReport();
  }
  async function connect() {
    state.health = await api("/api/health");
    renderHealth();
    await refresh();
    try {
      await loadReport();
    } catch (error) {
      state.report = null;
      renderReport();
      message(
        `Queue connected. The verification report could not be read: ${error.message}`,
      );
    }
  }
  function openEvent() {
    $("event-form").reset();
    $("event-key").value = "event-" + crypto.randomUUID();
    $("event-error").hidden = true;
    $("event-dialog").showModal();
    $("event-subject").focus();
  }
  function openAction(action, job) {
    state.action = { action, job };
    $("action-form").reset();
    $("action-error").hidden = true;
    $("action-title").textContent =
      action === "approve"
        ? "Approve this job?"
        : action === "reject"
          ? "Reject this job?"
          : "Replay this job?";
    $("action-description").textContent =
      action === "approve"
        ? `Approve “${job.subject}” for its local case action. It will enter the queue and still requires a worker execution.`
        : action === "reject"
          ? `Reject “${job.subject}”. The job will not execute; this decision and your reason will be recorded.`
          : `Replay “${job.subject}” with a new bounded attempt budget. The event identity, original fault setting and complete audit trail are retained.`;
    $("submit-action").textContent =
      action === "approve"
        ? "Approve job"
        : action === "reject"
          ? "Reject job"
          : "Replay job";
    $("submit-action").className =
      `button ${action === "reject" ? "button-danger" : "button-primary"}`;
    $("action-dialog").showModal();
    $("action-actor").focus();
  }
  $("new-event").addEventListener("click", openEvent);
  $("status-filter").addEventListener("change", () =>
    refresh().catch((error) => message(error.message)),
  );
  $("event-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = {
      subject: $("event-subject").value.trim(),
      description: $("event-description").value.trim(),
      category: $("event-category").value,
      priority: $("event-priority").value,
      failure_mode: state.health?.demo_mode ? $("event-failure").value : "none",
    };
    const key = $("event-key").value.trim();
    if (!payload.subject || !payload.description || !key) {
      $("event-error").textContent =
        "Provide a subject, description and idempotency key.";
      $("event-error").hidden = false;
      return;
    }
    busy($("create-event"), true, "Recording…");
    $("event-error").hidden = true;
    try {
      const result = await api("/api/events", {
        method: "POST",
        headers: { "Idempotency-Key": key },
        body: JSON.stringify(payload),
      });
      state.selectedId = result.job.id;
      $("status-filter").value = "";
      await refresh();
      $("event-dialog").close();
      location.hash = "queue";
      message(
        result.duplicate
          ? "Existing event reused. The idempotency key prevented a duplicate job."
          : "Event recorded with its outbox job and audit entry.",
        "notice",
      );
    } catch (error) {
      $("event-error").textContent = error.message;
      $("event-error").hidden = false;
    } finally {
      busy($("create-event"), false);
    }
  });
  $("action-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!state.action) return;
    const { action, job } = state.action;
    const actor = $("action-actor").value.trim();
    const note = $("action-note").value.trim();
    if (!actor || !note) {
      $("action-error").textContent =
        "Record an actor label and a reason for this action.";
      $("action-error").hidden = false;
      return;
    }
    busy($("submit-action"), true, "Recording decision…");
    $("action-error").hidden = true;
    try {
      await api(
        `/api/jobs/${encodeURIComponent(job.id)}/${action === "replay" ? "replay" : "decision"}`,
        {
          method: "POST",
          body: JSON.stringify({
            actor,
            note,
            ...(action === "replay" ? {} : { decision: action }),
          }),
        },
      );
      await refresh();
      $("action-dialog").close();
      message(
        `${action === "approve" ? "Approval" : action === "reject" ? "Rejection" : "Replay"} recorded with the supplied actor label and reason.`,
        "notice",
      );
    } catch (error) {
      $("action-error").textContent =
        error.status === 409
          ? `${error.message} Refresh the queue to see the current state.`
          : error.message;
      $("action-error").hidden = false;
    } finally {
      busy($("submit-action"), false);
    }
  });
  $("run-next").addEventListener("click", async () => {
    busy($("run-next"), true, "Running one…");
    try {
      const result = await api("/api/worker/tick", { method: "POST" });
      if (result.job) state.selectedId = result.job.id;
      await refresh();
      message(
        result.processed && result.job
          ? `Processed “${result.job.subject}”: ${statuses[result.job.status] || result.job.status}.`
          : "No due jobs were available. Approvals and scheduled retry times still apply.",
        "notice",
      );
    } catch (error) {
      message(error.message);
    } finally {
      busy($("run-next"), false);
    }
  });
  $("seed-button").addEventListener("click", async () => {
    busy($("seed-button"), true, "Loading fictional events…");
    try {
      const result = await api("/api/seed", { method: "POST" });
      await refresh();
      message(
        `Loaded ${result.added} fictional ${result.added === 1 ? "event" : "events"}. All effects stay in this local workspace.`,
        "notice",
      );
    } catch (error) {
      message(error.message);
    } finally {
      busy($("seed-button"), false);
    }
  });
  for (const id of ["refresh-queue", "refresh-cases", "refresh-audit"])
    $(id).addEventListener("click", async () => {
      busy($(id), true, "Refreshing…");
      try {
        await refresh();
        announce("Workspace state refreshed.");
      } catch (error) {
        message(error.message);
      } finally {
        busy($(id), false);
      }
    });
  $("refresh-verification").addEventListener("click", async () => {
    busy($("refresh-verification"), true, "Refreshing…");
    try {
      await loadReport();
      announce("Verification report refreshed.");
    } catch (error) {
      message(error.message);
    } finally {
      busy($("refresh-verification"), false);
    }
  });
  $("connection-button").addEventListener("click", openConnection);
  function openConnection() {
    $("api-token").value = token;
    $("connection-error").hidden = true;
    $("connection-dialog").showModal();
    $("api-token").focus();
  }
  $("connection-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const old = token;
    token = $("api-token").value.trim();
    busy($("connect-submit"), true, "Connecting…");
    $("connection-error").hidden = true;
    try {
      await connect();
      try {
        if (token) sessionStorage.setItem("flowledger-token", token);
        else sessionStorage.removeItem("flowledger-token");
      } catch (_) {
        /* Continue in memory if session storage is restricted. */
      }
      $("connection-dialog").close();
      message("Workspace connected.", "notice");
    } catch (error) {
      token = old;
      $("connection-error").textContent = error.message;
      $("connection-error").hidden = false;
    } finally {
      busy($("connect-submit"), false);
    }
  });
  document
    .querySelectorAll(".close-dialog")
    .forEach((button) =>
      button.addEventListener("click", () => button.closest("dialog").close()),
    );
  document.querySelector(".skip-link").addEventListener("click", (event) => {
    event.preventDefault();
    $("main-content").focus();
    $("main-content").scrollIntoView({ block: "start" });
  });
  window.addEventListener("hashchange", () =>
    showView(location.hash.slice(1), true),
  );
  showView(location.hash.slice(1));
  renderReport();
  connect().catch((error) => {
    message(error.message);
    $("connection-state").className = "connection-state failed";
    $("connection-label").textContent =
      error.status === 401 ? "Access required" : "Disconnected";
    if (error.status === 401) openConnection();
  });
})();
