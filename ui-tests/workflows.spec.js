const { test, expect } = require("@playwright/test");
const AxeBuilder = require("@axe-core/playwright").default;
const headers = process.env.FLOW_API_TOKEN
  ? { Authorization: `Bearer ${process.env.FLOW_API_TOKEN}` }
  : {};

test.beforeEach(async ({ page }) => {
  if (process.env.FLOW_API_TOKEN)
    await page.addInitScript(
      (token) => sessionStorage.setItem("flowledger-token", token),
      process.env.FLOW_API_TOKEN,
    );
});

async function openWorkspace(page, view = "queue") {
  await page.goto(`/#${view}`);
  await expect(page.getByText(/Connected · (local|protected)/)).toBeVisible();
  await expect(page.locator("#overview-caption")).not.toContainText("Reading");
  await expect(page.locator("#global-error")).toBeHidden();
}

async function createEvent(page, options = {}) {
  const event = {
    subject: `Browser verification ${Date.now()}-${Math.random().toString(16).slice(2, 6)}`,
    description:
      "Synthetic browser verification event. No real customer or external action.",
    category: "delivery",
    priority: "normal",
    failure: "none",
    key: `browser-${crypto.randomUUID()}`,
    ...options,
  };
  await page.getByRole("button", { name: "New event", exact: true }).click();
  await page.getByLabel("Subject", { exact: true }).fill(event.subject);
  await page.getByLabel("Description", { exact: true }).fill(event.description);
  await page
    .getByLabel("Category", { exact: true })
    .selectOption(event.category);
  await page
    .getByLabel("Priority", { exact: true })
    .selectOption(event.priority);
  await page.getByLabel("Idempotency key").fill(event.key);
  if (event.failure !== "none")
    await page
      .getByLabel("Synthetic fault injection")
      .selectOption(event.failure);
  await page.getByRole("button", { name: "Create event", exact: true }).click();
  await expect(
    page.getByRole("dialog", { name: "Create an event" }),
  ).toBeHidden();
  await expect(page.locator("#job-inspector h2")).toHaveText(event.subject);
  return event;
}

async function lookup(request, key) {
  const response = await request.get("/api/jobs", { headers });
  expect(response.ok()).toBeTruthy();
  return (await response.json()).jobs.find((job) => job.event_key === key);
}

async function jobDetails(request, id) {
  const response = await request.get(`/api/jobs/${id}`, { headers });
  expect(response.ok()).toBeTruthy();
  return response.json();
}

async function runUntil(page, request, id, status) {
  for (let count = 0; count < 30; count++) {
    const current = await jobDetails(request, id);
    if (current.job.status === status) return current;
    if (current.job.status === "retry_wait") {
      const due = new Date(current.job.next_attempt_at).valueOf();
      await expect
        .poll(() => Date.now() >= due, {
          timeout: 12000,
          intervals: [100, 250, 500],
        })
        .toBeTruthy();
    }
    await page.getByRole("button", { name: "Run one due job" }).click();
    await expect(
      page.getByRole("button", { name: "Run one due job" }),
    ).toBeEnabled();
  }
  throw new Error(
    `Target job did not reach ${status} after 30 bounded worker ticks.`,
  );
}

async function decide(page, action, reason) {
  await page
    .locator("#job-inspector")
    .getByRole("button", { name: `${action} job`, exact: true })
    .click();
  await page.getByLabel("Actor label").fill("Browser verification operator");
  await page.getByLabel("Reason", { exact: true }).fill(reason);
  await page
    .getByRole("dialog")
    .getByRole("button", { name: `${action} job`, exact: true })
    .click();
  await expect(page.locator("#action-dialog")).toBeHidden();
}

test("records intake, executes a real local case and renders source markup as inert text", async ({
  page,
  request,
}) => {
  await openWorkspace(page);
  const markup = '<img src=x onerror="window.eventExecuted=true">';
  const event = await createEvent(page, {
    description: `Synthetic delivery. ${markup}`,
  });
  const job = await lookup(request, event.key);
  expect(job).toBeTruthy();
  await expect(page.locator(".job-description")).toContainText(markup);
  await expect(page.locator("#job-inspector img")).toHaveCount(0);
  expect(await page.evaluate(() => window.eventExecuted)).toBeUndefined();
  const result = await runUntil(page, request, job.id, "succeeded");
  expect(result.case.subject).toBe(event.subject);
  await expect(page.locator(".job-case")).toContainText("Local case created");
  await expect(page.locator(".audit-entry")).not.toHaveCount(0);
  await page.getByRole("link", { name: /^Local cases/ }).click();
  await expect(
    page.locator(".case-card").filter({ hasText: event.subject }),
  ).toBeVisible();
  const accessibility = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
    .analyze();
  expect(accessibility.violations).toEqual([]);
});

test("reuses an identical idempotency key and preserves conflicting intake for correction", async ({
  page,
  request,
}) => {
  await openWorkspace(page);
  const event = await createEvent(page);
  const original = await lookup(request, event.key);
  await createEvent(page, event);
  await expect(page.locator("#global-notice")).toContainText(
    "Existing event reused",
  );
  expect((await lookup(request, event.key)).id).toBe(original.id);
  await page.getByRole("button", { name: "New event", exact: true }).click();
  await page
    .getByLabel("Subject", { exact: true })
    .fill("Conflicting browser verification");
  await page.getByLabel("Description", { exact: true }).fill(event.description);
  await page.getByLabel("Idempotency key").fill(event.key);
  await page.getByRole("button", { name: "Create event", exact: true }).click();
  await expect(page.locator("#event-error")).toBeVisible();
  await expect(page.getByLabel("Idempotency key")).toHaveValue(event.key);
});

test("keeps a refund gated until a recorded human approval permits execution", async ({
  page,
  request,
}) => {
  await openWorkspace(page);
  const event = await createEvent(page, { category: "refund" });
  const job = await lookup(request, event.key);
  await expect(page.locator("#job-inspector .status-tag").first()).toHaveText(
    "Needs approval",
  );
  await page.getByRole("button", { name: "Run one due job" }).click();
  await expect(
    page.getByRole("button", { name: "Run one due job" }),
  ).toBeEnabled();
  const gated = await jobDetails(request, job.id);
  expect(gated.job.status).toBe("pending_approval");
  expect(gated.job.attempts).toBe(0);
  expect(gated.case).toBeNull();
  await page
    .getByRole("button", { name: `Inspect job: ${event.subject}`, exact: true })
    .click();
  await expect(page.locator("#job-inspector h2")).toHaveText(event.subject);
  const reason = "Fictional refund reviewed for a local case only.";
  await page
    .locator("#job-inspector")
    .getByRole("button", { name: "Approve job", exact: true })
    .click();
  const dialogAccessibility = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
    .analyze();
  expect(dialogAccessibility.violations).toEqual([]);
  await page.getByLabel("Actor label").fill("Browser verification operator");
  await page.getByLabel("Reason", { exact: true }).fill(reason);
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Approve job", exact: true })
    .click();
  await expect(page.locator("#action-dialog")).toBeHidden();
  const approved = await jobDetails(request, job.id);
  expect(approved.job.status).toBe("queued");
  expect(JSON.stringify(approved.audit)).toContain(reason);
  const result = await runUntil(page, request, job.id, "succeeded");
  expect(result.case).toBeTruthy();
});

test("records a rejection without creating a case", async ({
  page,
  request,
}) => {
  await openWorkspace(page);
  const event = await createEvent(page, { category: "security" });
  const job = await lookup(request, event.key);
  await decide(
    page,
    "Reject",
    "Synthetic security escalation rejected after review.",
  );
  const result = await jobDetails(request, job.id);
  expect(result.job.status).toBe("rejected");
  expect(result.case).toBeNull();
  expect(result.job.attempts).toBe(0);
  await expect(page.locator("#job-inspector .status-tag").first()).toHaveText(
    "Rejected",
  );
});

test("recovers from a clearly labeled first-attempt synthetic failure at the recorded retry time", async ({
  page,
  request,
}) => {
  await openWorkspace(page);
  const event = await createEvent(page, {
    category: "general",
    failure: "once",
  });
  const job = await lookup(request, event.key);
  await expect(page.locator(".fault-tag")).toContainText(
    "SYNTHETIC FAULT INJECTION",
  );
  const waiting = await runUntil(page, request, job.id, "retry_wait");
  expect(waiting.job.attempts).toBe(1);
  expect(waiting.job.next_attempt_at).toBeTruthy();
  expect(waiting.case).toBeNull();
  const succeeded = await runUntil(page, request, job.id, "succeeded");
  expect(succeeded.job.attempts).toBe(2);
  expect(succeeded.case).toBeTruthy();
  const allCases = await request.get("/api/cases", { headers });
  expect(
    (await allCases.json()).cases.filter((item) => item.job_id === job.id),
  ).toHaveLength(1);
});

test("exhausts bounded attempts and records a dead-letter replay without changing event identity", async ({
  page,
  request,
}) => {
  await openWorkspace(page);
  const event = await createEvent(page, {
    category: "general",
    failure: "always",
  });
  const job = await lookup(request, event.key);
  const exhausted = await runUntil(page, request, job.id, "dead_letter");
  expect(exhausted.job.attempts).toBe(exhausted.job.max_attempts);
  expect(exhausted.case).toBeNull();
  await page
    .getByRole("button", { name: `Inspect job: ${event.subject}`, exact: true })
    .click();
  await expect(page.locator("#job-inspector .status-tag").first()).toHaveText(
    "Dead letter",
  );
  await decide(
    page,
    "Replay",
    "Synthetic replay to demonstrate a new bounded attempt budget.",
  );
  const replayed = await jobDetails(request, job.id);
  expect(replayed.job.event_key).toBe(event.key);
  expect(replayed.job.status).toBe("queued");
  expect(replayed.job.attempts).toBe(0);
  expect(replayed.job.replay_count).toBe(1);
  expect(replayed.audit.length).toBeGreaterThan(exhausted.audit.length);
  await expect(page.locator(".fault-tag")).toContainText("every attempt fails");
});

test("filters actual statuses and renders recorded verification results", async ({
  page,
  request,
}) => {
  await openWorkspace(page);
  await page.getByLabel("Filter by status").selectOption("pending_approval");
  await expect
    .poll(async () => page.locator("#jobs-body .status-tag").allTextContents())
    .toEqual(
      (
        await (
          await request.get("/api/jobs?status=pending_approval", { headers })
        ).json()
      ).jobs.map(() => "Needs approval"),
    );
  const response = await request.get("/api/verification", { headers });
  const report = await response.json();
  await page.getByRole("link", { name: /^Verification/ }).click();
  if (report.status === "complete") {
    await expect(
      page.getByRole("heading", {
        name: `${report.passed} of ${report.total} scenarios passed.`,
      }),
    ).toBeVisible();
    await expect(page.locator(".scenario-card")).toHaveCount(
      report.scenarios.length,
    );
  } else
    await expect(
      page.getByText("No completed verification report is available.", {
        exact: false,
      }),
    ).toBeVisible();
});

test("keeps the intake idempotency key after a simulated network failure", async ({
  page,
}) => {
  await openWorkspace(page);
  await page.route("**/api/events", (route) =>
    route.abort("connectionrefused"),
  );
  await page.getByRole("button", { name: "New event", exact: true }).click();
  await page
    .getByLabel("Subject", { exact: true })
    .fill("Synthetic interrupted intake");
  await page
    .getByLabel("Description", { exact: true })
    .fill("Fictional retry verification.");
  const key = await page.getByLabel("Idempotency key").inputValue();
  await page.getByRole("button", { name: "Create event", exact: true }).click();
  await expect(page.locator("#event-error")).toContainText(
    "same idempotency key",
  );
  await expect(page.getByLabel("Idempotency key")).toHaveValue(key);
  await expect(
    page.getByRole("button", { name: "Create event", exact: true }),
  ).toBeEnabled();
});

test("supports keyboard skip, named dialog focus and Escape dismissal", async ({
  page,
}) => {
  await openWorkspace(page);
  await page.keyboard.press("Tab");
  await expect(
    page.getByRole("link", { name: "Skip to content" }),
  ).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.locator("#main-content")).toBeFocused();
  await page.getByRole("button", { name: "New event", exact: true }).click();
  await expect(page.getByLabel("Subject", { exact: true })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(page.locator("#event-dialog")).toBeHidden();
  await expect(
    page.getByRole("button", { name: "New event", exact: true }),
  ).toBeFocused();
});

for (const viewport of [
  { name: "desktop", width: 1440, height: 1050 },
  { name: "mobile", width: 390, height: 844 },
  { name: "narrow mobile", width: 320, height: 740 },
]) {
  test(`${viewport.name} views and dialogs have no automated WCAG A/AA violations or page overflow`, async ({
    page,
  }) => {
    await page.setViewportSize({
      width: viewport.width,
      height: viewport.height,
    });
    await openWorkspace(page);
    for (const [view, label] of [
      ["queue", "Work queue"],
      ["cases", "Local cases"],
      ["audit", "Audit ledger"],
      ["verification", "Verification"],
    ]) {
      await page.getByRole("link", { name: new RegExp(`^${label}`) }).click();
      await expect(page.locator(`#view-${view}`)).toBeVisible();
      const result = await new AxeBuilder({ page })
        .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
        .analyze();
      expect(result.violations).toEqual([]);
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= window.innerWidth,
        ),
      ).toBeTruthy();
    }
    await page.getByRole("link", { name: /^Work queue/ }).click();
    await page.getByRole("button", { name: "New event", exact: true }).click();
    const eventAxe = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
      .analyze();
    expect(eventAxe.violations).toEqual([]);
    await page.keyboard.press("Escape");
    await page.getByRole("button", { name: "Connection", exact: true }).click();
    const connectionAxe = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
      .analyze();
    expect(connectionAxe.violations).toEqual([]);
  });
}
