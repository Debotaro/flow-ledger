const { test, expect } = require("@playwright/test");
const AxeBuilder = require("@axe-core/playwright").default;

// These tests simulate API responses at the browser boundary. They validate UI
// state handling; workflows.spec.js separately executes genuine API transitions.
const labels = {
  queued: "Queued",
  running: "Running",
  retry_wait: "Retry scheduled",
  pending_approval: "Needs approval",
  succeeded: "Succeeded",
  dead_letter: "Dead letter",
  rejected: "Rejected",
};
const jobs = Object.keys(labels).map((status, index) => ({
  id: `synthetic-${status}`,
  event_key: `synthetic-key-${index}`,
  subject: `Fictional ${labels[status]} event`,
  description: "Synthetic UI boundary fixture, not a real worker execution.",
  category: "general",
  priority: "normal",
  action: "support_case",
  status,
  attempts: status === "queued" ? 0 : 1,
  max_attempts: 3,
  next_attempt_at: status === "retry_wait" ? "2026-10-16T06:00:02Z" : null,
  lease_until: status === "running" ? "2026-10-16T06:00:30Z" : null,
  last_error: null,
  replay_count: 0,
  created_at: "2026-10-16T06:00:00Z",
  updated_at: "2026-10-16T06:00:00Z",
  failure_mode: "none",
}));

async function simulatedWorkspace(page, options = {}) {
  const fixture = {
    authRequired: false,
    token: "synthetic-browser-boundary-token",
    jobs,
    ...options,
  };
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const json = (value, status = 200) =>
      route.fulfill({ status, json: value });
    if (url.pathname === "/api/health")
      return json({
        status: "ok",
        version: "1.0.0",
        auth_required: fixture.authRequired,
        demo_mode: true,
        lease_seconds: 30,
        max_attempts: 3,
      });
    if (
      fixture.authRequired &&
      request.headers().authorization !== `Bearer ${fixture.token}`
    )
      return json({ detail: "Authentication required." }, 401);
    if (url.pathname === "/api/overview")
      return json({
        counts: Object.fromEntries(
          Object.keys(labels).map((status) => [
            status,
            fixture.jobs.filter((job) => job.status === status).length,
          ]),
        ),
        cases: 0,
        audit_events: 0,
      });
    if (url.pathname === "/api/jobs")
      return json({
        jobs: fixture.jobs.filter(
          (job) =>
            !url.searchParams.has("status") ||
            job.status === url.searchParams.get("status"),
        ),
      });
    if (url.pathname.startsWith("/api/jobs/"))
      return json({
        job: fixture.jobs.find((job) => url.pathname.endsWith(job.id)),
        case: null,
        audit: [],
      });
    if (url.pathname === "/api/cases") return json({ cases: [] });
    if (url.pathname === "/api/audit") return json({ events: [] });
    if (url.pathname === "/api/verification")
      return json({ status: "not_run" });
    return json({ detail: "Unexpected simulated endpoint." }, 404);
  });
  return fixture;
}

test("simulated UI boundary: names and filters all seven statuses, including an active lease", async ({
  page,
}) => {
  await simulatedWorkspace(page);
  await page.goto("/");
  await expect(page.locator("#jobs-body tr")).toHaveCount(7);
  for (const [status, label] of Object.entries(labels)) {
    await page.getByLabel("Filter by status").selectOption(status);
    await expect(page.locator("#jobs-body tr")).toHaveCount(1);
    await expect(page.locator("#jobs-body .status-tag")).toHaveText(label);
    await expect(
      page.getByRole("button", {
        name: `Filter ${label}: 1 jobs`,
        exact: true,
      }),
    ).toHaveAttribute("aria-pressed", "true");
  }
  await page
    .getByRole("button", { name: "Filter Running: 1 jobs", exact: true })
    .click();
  await page
    .getByRole("button", {
      name: "Inspect job: Fictional Running event",
      exact: true,
    })
    .click();
  await expect(page.locator("#job-inspector .status-tag")).toHaveText(
    "Running",
  );
  await expect(page.locator(".job-facts")).toContainText(
    "2026-10-16 06:00:30 UTC",
  );
});

test("simulated UI boundary: rejects a wrong token, saves a valid token to this tab and reconnects", async ({
  page,
}) => {
  const fixture = await simulatedWorkspace(page, { authRequired: true });
  await page.goto("/");
  await expect(
    page.getByRole("dialog", { name: "Connection settings" }),
  ).toBeVisible();
  await expect(page.getByLabel("API token", { exact: true })).toBeFocused();
  await page
    .getByLabel("API token", { exact: true })
    .fill("synthetic-wrong-token");
  await page
    .getByRole("button", { name: "Save and connect", exact: true })
    .click();
  await expect(page.locator("#connection-error")).toContainText(
    "valid API token",
  );
  expect(
    await page.evaluate(() => sessionStorage.getItem("flowledger-token")),
  ).toBeNull();
  await page.getByLabel("API token", { exact: true }).fill(fixture.token);
  await page
    .getByRole("button", { name: "Save and connect", exact: true })
    .click();
  await expect(page.locator("#connection-dialog")).toBeHidden();
  await expect(
    page.getByText("Connected · protected", { exact: true }),
  ).toBeVisible();
  expect(
    await page.evaluate(() => localStorage.getItem("flowledger-token")),
  ).toBeNull();
  expect(
    await page.evaluate(() => sessionStorage.getItem("flowledger-token")),
  ).toBe(fixture.token);
  await page.reload();
  await expect(
    page.getByText("Connected · protected", { exact: true }),
  ).toBeVisible();
  await expect(page.locator("body")).not.toContainText(fixture.token);
  expect(page.url()).not.toContain(fixture.token);
});

test("simulated UI boundary: shows empty states and permits recovery after an interrupted connection", async ({
  page,
}) => {
  await simulatedWorkspace(page, { jobs: [] });
  await page.route("**/api/overview", async (route) => {
    await page.unroute("**/api/overview");
    await route.abort("connectionrefused");
  });
  await page.goto("/");
  await expect(page.locator("#global-error")).toContainText(
    "Cannot reach the workspace",
  );
  await expect(page.locator("#connection-label")).toHaveText("Disconnected");
  await page.getByRole("button", { name: "Connection", exact: true }).click();
  await page
    .getByRole("button", { name: "Save and connect", exact: true })
    .click();
  await expect(page.locator("#global-error")).toBeHidden();
  await expect(
    page.getByText("An orderly start.", { exact: true }),
  ).toBeVisible();
  await page.getByRole("link", { name: /^Local cases/ }).click();
  await expect(
    page.getByText("No local cases have been created.", { exact: false }),
  ).toBeVisible();
  await page.getByRole("link", { name: /^Audit ledger/ }).click();
  await expect(
    page.getByText("No recorded events yet.", { exact: false }),
  ).toBeVisible();
  await page.getByRole("link", { name: /^Verification/ }).click();
  await expect(
    page.getByText("No completed verification report is available.", {
      exact: false,
    }),
  ).toBeVisible();
});

test("simulated UI boundary: preserves action input after a concurrent decision conflict", async ({
  page,
}) => {
  await simulatedWorkspace(page);
  await page.route("**/api/jobs/*/decision", (route) =>
    route.fulfill({
      status: 409,
      json: { detail: "The job is no longer pending approval." },
    }),
  );
  await page.goto("/");
  await page
    .getByRole("button", {
      name: "Inspect job: Fictional Needs approval event",
      exact: true,
    })
    .click();
  await page
    .locator("#job-inspector")
    .getByRole("button", { name: "Approve job", exact: true })
    .click();
  await page.getByLabel("Actor label").fill("Fictional operator");
  await page
    .getByLabel("Reason", { exact: true })
    .fill("A recorded synthetic review reason.");
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Approve job", exact: true })
    .click();
  await expect(page.locator("#action-error")).toContainText(
    "Refresh the queue",
  );
  await expect(page.getByLabel("Reason", { exact: true })).toHaveValue(
    "A recorded synthetic review reason.",
  );
  const result = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
    .analyze();
  expect(result.violations).toEqual([]);
});
