import { expect, test } from "@playwright/test";

const SYNTHETIC = "Synthetic: Red Line suspended JFK/UMass to Park Street";
const IDENTITY = "I am an independent prototype, not a City of Boston or MBTA service.";

test("three-minute demo: plan, suspend the Red Line, ask the agent", async ({ page }) => {
  const consoleErrors: string[] = [];
  const tileRequests: string[] = [];
  page.on("console", (m) => m.type() === "error" && consoleErrors.push(m.text()));
  page.on("pageerror", (e) => consoleErrors.push(String(e)));
  page.on("request", (r) => r.url().includes("tile.openstreetmap") && tileRequests.push(r.url()));

  await page.goto("/");
  await expect(page.getByRole("note").first()).toHaveText(
    "Independent prototype. Not a City of Boston or MBTA service.",
  );

  await page.getByRole("button", { name: /Plan Fields Corner/ }).click();
  const routes = page.getByRole("list", { name: "Pareto routes" }).locator("> li");
  await expect(routes.first()).toBeVisible({ timeout: 30_000 });
  await expect(page.locator(".plan-meta")).toContainText("departs Tuesday 8:15 AM");
  await expect(page.locator(".plan-meta")).toContainText("alerts: none");
  await expect(page.getByRole("button", { name: "Route: Red Line" })).toBeVisible();
  const before = await routes.count();
  await expect(page.locator(".chart circle")).toHaveCount(before);
  expect(await page.locator(".leaflet-overlay-pane path").count()).toBeGreaterThan(0);

  await page.getByRole("button", { name: /Suspend the Red Line/ }).click();
  const banner = page.getByRole("status");
  await expect(banner).toContainText("Entered: 19, then Orange Line, 15, then SL4", {
    timeout: 30_000,
  });
  await expect(page.getByLabel("Alert source")).toHaveValue(SYNTHETIC);
  await expect(page.locator(".plan-meta")).toContainText(`alerts: ${SYNTHETIC}`);
  const red = routes.filter({ has: page.getByRole("button", { name: "Route: Red Line" }) });
  await expect(red).toContainText("1.00 (0 to 1)");
  await expect(red.getByRole("note")).toContainText("Synthetic demo alert: Red Line suspended");

  // One highlight drives the list and the chart together.
  await page.getByRole("button", { name: "Route: 19, then Orange Line" }).focus();
  await expect(page.locator(".route.active")).toHaveCount(1);
  await expect(page.locator(".chart circle[r='9']")).toHaveCount(1);

  await page.getByRole("button", { name: /Try: / }).click();
  const reply = page.locator(".msg.agent").first();
  await expect(reply).toBeVisible({ timeout: 90_000 });
  await expect(reply.locator("p").first()).toHaveText(IDENTITY);
  await expect(reply).toContainText(/Tue(sday)?\b.*\b0?8:15/);
  await expect(reply.locator(".meta")).toContainText("plan_trip");

  await page.screenshot({ path: "test-results/demo-final.png" });
  expect(consoleErrors).toEqual([]);
  expect(tileRequests).toEqual([]);
});
