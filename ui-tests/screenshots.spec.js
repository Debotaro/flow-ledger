const { test, expect } = require("@playwright/test");
const path = require("node:path");
const fs = require("node:fs");

test("captures real fictional demo states on desktop and mobile", async ({
  page,
  request,
}) => {
  test.skip(
    process.env.FLOW_SCREENSHOTS !== "1",
    "Opt in against a dedicated fictional preview workspace.",
  );
  const headers = process.env.FLOW_API_TOKEN
    ? { Authorization: `Bearer ${process.env.FLOW_API_TOKEN}` }
    : {};
  if (process.env.FLOW_API_TOKEN)
    await page.addInitScript(
      (token) => sessionStorage.setItem("flowledger-token", token),
      process.env.FLOW_API_TOKEN,
    );
  const directory = path.join(__dirname, "..", "docs", "screenshots");
  fs.mkdirSync(directory, { recursive: true });
  const seeded = await request.post("/api/seed", { headers });
  expect(seeded.ok()).toBeTruthy();
  const response = await request.get("/api/jobs", { headers });
  expect(response.ok()).toBeTruthy();
  const jobs = (await response.json()).jobs;
  expect(
    jobs.every((job) => job.event_key.startsWith("fictional-seed-v1:")),
  ).toBeTruthy();
  // An initially fresh showcase gains a real success and scheduled retry. No
  // API responses or displayed counts are substituted for these screenshots.
  if ((await seeded.json()).added > 0) {
    for (let index = 0; index < 2; index++) {
      const tick = await request.post("/api/worker/tick", { headers });
      expect(tick.ok()).toBeTruthy();
      expect((await tick.json()).processed).toBe(true);
    }
  }
  await page.goto("/");
  await expect(page.getByText(/Connected · (local|protected)/)).toBeVisible();
  await expect(page.locator("#overview-caption")).not.toContainText("Reading");
  await page.mouse.move(1200, 30);
  await page.screenshot({
    path: path.join(directory, "queue-desktop.png"),
    fullPage: true,
  });
  await page.getByLabel("Filter by status").selectOption("pending_approval");
  await expect(page.locator("#jobs-body .status-tag").first()).toHaveText(
    "Needs approval",
  );
  await page.locator(".job-subject").first().click();
  await expect(page.locator("#job-inspector h2")).toBeVisible();
  await page.mouse.move(1200, 30);
  await page.screenshot({
    path: path.join(directory, "job-desktop.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "Close job details" }).click();
  await page.getByLabel("Filter by status").selectOption("");
  await expect(page.locator(".status-card.selected")).toHaveCount(0);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.mouse.move(380, 15);
  await page.screenshot({
    path: path.join(directory, "queue-mobile.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 1440, height: 1050 });
  await page.getByRole("link", { name: /^Verification/ }).click();
  await expect(page.locator("#view-verification")).toBeVisible();
  await expect(page.locator(".scenario-card")).not.toHaveCount(0);
  await page.mouse.move(1200, 30);
  await page.screenshot({
    path: path.join(directory, "verification-desktop.png"),
    fullPage: true,
  });
});
