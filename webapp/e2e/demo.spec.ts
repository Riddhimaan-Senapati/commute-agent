import { expect, test } from "@playwright/test";

const IDENTITY = "I am an independent prototype, not a City of Boston or MBTA service.";

test("map-first demo: route on the map, pick an objective, suspend the Red Line, ask the agent", async ({
  page,
}) => {
  const consoleErrors: string[] = [];
  page.on("console", (m) => m.type() === "error" && consoleErrors.push(m.text()));
  page.on("pageerror", (e) => consoleErrors.push(String(e)));

  await page.goto("/");
  // The demo trip is planned on load and drawn on a street map with A and B pins.
  const cards = page.getByRole("list", { name: "Routes" }).locator("> li");
  await expect(cards.first()).toBeVisible({ timeout: 30_000 });
  await expect(page.getByLabel("From")).toHaveValue("Fields Corner, Dorchester");
  await expect(page.getByLabel("To")).toHaveValue("Downtown Crossing");
  await expect(page.locator(".pin-start")).toHaveCount(1);
  await expect(page.locator(".pin-end")).toHaveCount(1);
  await expect(page.locator("path.route-selected").first()).toBeVisible();
  await expect(page.locator(".meta").first()).toContainText("Tuesday 8:15 AM");
  await expect(page.locator(".leaflet-tile-loaded").first()).toBeVisible({ timeout: 15_000 });
  const allCount = await cards.count();
  expect(allCount).toBeGreaterThan(3);

  // "Only the fastest" leaves exactly one route, with its directions open.
  await page.getByRole("radio", { name: "Fastest" }).click();
  await expect(cards).toHaveCount(1);
  await expect(cards.first()).toContainText("Fastest");
  await expect(cards.first().getByRole("list", { name: "Directions" })).toBeVisible();
  await page.getByRole("radio", { name: "Lowest CO₂" }).click();
  await expect(cards).toHaveCount(1);
  await expect(cards.first()).toContainText("Lowest CO₂");
  await page.getByRole("radio", { name: "All trade-offs" }).click();
  await expect(cards).toHaveCount(allCount);
  await expect(page.getByRole("figure", { name: "Trade-off chart: time against CO2" })).toBeVisible();

  // Routes outside the Pareto set are listed under it, each naming the route that beats it.
  await expect(page.getByRole("heading", { name: /On the Pareto frontier/ })).toBeVisible();
  const dropped = page.getByRole("list", { name: "Not on the frontier" }).locator("> li");
  await expect(dropped.first()).toContainText(/Beaten by|Duplicate of/);

  // Selecting the Red Line card shows its legs.
  await page.getByRole("button", { name: "Route: Red Line" }).click();
  const red = cards.filter({ has: page.getByRole("button", { name: "Route: Red Line" }) });
  await expect(red.getByRole("list", { name: "Directions" })).toContainText("Red Line");

  // One switch applies the synthetic suspension; alternatives enter and Red Line is flagged.
  await page.getByLabel("Simulate Red Line suspension").check();
  await expect(page.getByRole("status")).toContainText("19, then Orange Line", { timeout: 30_000 });
  await expect(page.getByRole("status")).toContainText("15, then SL4");
  await expect(red.getByRole("note")).toContainText("Synthetic demo alert");

  // A typed place resolves through the gazetteer and re-plans.
  await page.getByLabel("To").fill("Park Street");
  await page.getByLabel("To").press("Enter");
  await expect(page.getByLabel("To")).toHaveValue("Park Street");
  await page.getByLabel("Simulate Red Line suspension").uncheck();
  await expect(page.getByRole("status")).toContainText("restored", { timeout: 30_000 });

  await page.getByRole("button", { name: "Fields Corner → Downtown Crossing" }).click();
  await expect(page.getByLabel("To")).toHaveValue("Downtown Crossing");

  // The agent lives in a drawer.
  await page.getByRole("button", { name: "Ask the agent" }).click();
  await page.getByRole("button", { name: /Try: / }).click();
  const reply = page.locator(".msg.agent").first();
  await expect(reply).toBeVisible({ timeout: 110_000 });
  await expect(reply.locator("p").first()).toHaveText(IDENTITY);


  // New chat clears the conversation, and so does picking another trip.
  await page.getByRole("button", { name: "New chat" }).click();
  await expect(page.locator(".msg")).toHaveCount(0);
  await expect(page.getByRole("button", { name: /Try: / })).toBeVisible();

  await page.screenshot({ path: "test-results/demo-final.png" });
  expect(consoleErrors).toEqual([]);
});

test("offline: with tiles blocked the map falls back to the local basemap", async ({ page }) => {
  await page.route("**/tile.openstreetmap.org/**", (route) => route.abort());
  await page.goto("/");
  await expect(page.getByRole("list", { name: "Routes" }).locator("> li").first()).toBeVisible({
    timeout: 30_000,
  });
  await expect(page.locator(".leaflet-attribution-flag, .leaflet-control-attribution")).toContainText(
    "offline basemap",
    { timeout: 15_000 },
  );
  await page.screenshot({ path: "test-results/offline.png" });
});
