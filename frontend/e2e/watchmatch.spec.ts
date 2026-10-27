import {expect, test} from "@playwright/test";

test("home page is responsive at phone and desktop sizes", async ({page}) => {
  for (const viewport of [{width: 320, height: 568}, {width: 390, height: 844}, {width: 1440, height: 900}]) {
    await page.setViewportSize(viewport);
    await page.goto("/");
    await expect(page.getByRole("heading", {name: "What sounds good tonight?"})).toBeVisible();
    await expect(page.getByRole("button", {name: "Create room"})).toBeVisible();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
    expect(overflow).toBe(false);
  }
});

test("discovery filters expose collections and selectable recency", async ({page}) => {
  await page.goto("/");
  await page.locator(".mode-card", {hasText: "Choose filters"}).click();
  const filterType = page.locator(".filter-panel > select").first();
  await expect(filterType.locator('option[value="collection"]')).toHaveCount(1);
  await filterType.selectOption("recency");
  const recency = page.getByRole("combobox", {name: "Recency type"});
  await expect(recency).toBeVisible();
  await expect(recency.locator('option[value="released"]')).toContainText("Released");
  await expect(recency.locator('option[value="added"]')).toContainText("Added");
  await expect(recency.locator('option[value="both"]')).toContainText("released or added");
  await expect(page.getByRole("checkbox", {name: "Unwatched only"})).toBeVisible();
});

test("a completed room renders its match", async ({page, request}) => {
  const roomResponse = await request.post("/api/rooms", {data: {kind: "movie", mode: "random"}});
  expect(roomResponse.ok()).toBeTruthy();
  const room = await roomResponse.json();
  const one = await (await request.post(`/api/rooms/${room.code}/join`, {data: {name: "Alex"}})).json();
  const two = await (await request.post(`/api/rooms/${room.code}/join`, {data: {name: "Sam"}})).json();
  const cards = await (await request.get(`/api/rooms/${room.code}/cards?participant_id=${one.participant_id}`)).json();
  expect(cards.length).toBeGreaterThan(0);
  const mediaId = cards[0].id;
  await request.post(`/api/rooms/${room.code}/swipe`, {data: {participant_id: one.participant_id, media_id: mediaId, direction: "right"}});
  await request.post(`/api/rooms/${room.code}/swipe`, {data: {participant_id: two.participant_id, media_id: mediaId, direction: "right"}});
  await page.goto(`/room/${room.code}`);
  await expect(page.getByText("IT'S A MATCH")).toBeVisible();
  const escapedTitle = cards[0].title.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  await expect(page.getByRole("heading", {name: new RegExp(escapedTitle)})).toBeVisible();
});

test("admin preview is responsive and its sections are navigable", async ({page}) => {
  for (const viewport of [{width: 390, height: 844}, {width: 1440, height: 900}]) {
    await page.setViewportSize(viewport);
    await page.goto("/admin");
    await expect(page.getByRole("heading", {name: "Rooms"})).toBeVisible();
    await expect(page.getByRole("button", {name: "Save changes"})).toBeEnabled();
    await expect(page.getByRole("button", {name: /Catalog & Plex/})).toHaveCount(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
    await page.getByRole("button", {name: /Access/}).click();
    await expect(page.getByRole("heading", {name: "Your household"})).toBeVisible();

    await page.route("**/api/auth/me", route => route.fulfill({status: 200, contentType: "application/json", body: JSON.stringify({authenticated: true, required: true, username: "Owner"})}));
    await page.route("**/api/system/access", route => route.fulfill({status: 200, contentType: "application/json", body: JSON.stringify({authorized: true, username: "Owner"})}));
    await page.route("**/api/sync/status", route => route.fulfill({status: 200, contentType: "application/json", body: JSON.stringify({running: false, interval_minutes: 1440, latest: null, history: []})}));
    await page.route("**/api/system/accounts", route => route.fulfill({status: 200, contentType: "application/json", body: JSON.stringify({users: [{id: 1, username: "Owner", plex_id: "owner"}], households: [{id: 1, name: "Home", slug: "home", user_id: 1, room_count: 2, match_count: 1}]})}));
    await page.goto("/system");
    await expect(page.getByRole("heading", {name: "Catalog & Plex"})).toBeVisible();
    await expect(page.getByText("Owner-only system console")).toBeVisible();
    const syncControls = page.locator(".setting-card", {hasText: "Control the shared Plex catalog update schedule"});
    await expect(syncControls.getByText(/Sync now|Sync in progress/)).toBeVisible();
    await expect(syncControls.getByText("Save schedule")).toBeVisible();
    await expect(page.getByRole("heading", {name: "Recent synchronization runs"})).toBeVisible();
    await page.getByRole("button", {name: /Account recovery/}).click();
    await expect(page.getByRole("heading", {name: "Household account recovery"})).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
    await page.unroute("**/api/auth/me");
    await page.unroute("**/api/system/access");
    await page.unroute("**/api/sync/status");
    await page.unroute("**/api/system/accounts");
  }
});

test("configuration is gated when Plex authentication is required", async ({page}) => {
  await page.route("**/api/auth/me", route => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({authenticated: false, required: true}),
  }));
  await page.goto("/admin");
  await expect(page.getByRole("heading", {name: "Sign in to manage your household."})).toBeVisible();
  await expect(page.getByRole("button", {name: "Sign in with Plex to configure WatchMatch"})).toBeVisible();
  await expect(page.getByRole("button", {name: "Save changes"})).toHaveCount(0);
});

test("system console is gated separately for the configured owner", async ({page}) => {
  await page.route("**/api/auth/me", route => route.fulfill({
    status: 200, contentType: "application/json",
    body: JSON.stringify({authenticated: false, required: true}),
  }));
  await page.goto("/system");
  await expect(page.getByRole("heading", {name: "Owner sign-in required."})).toBeVisible();
  await expect(page.getByRole("button", {name: "Sign in with Plex to access the system console"})).toBeVisible();
  await expect(page.getByRole("heading", {name: "Catalog & Plex"})).toHaveCount(0);
});

test("household settings save and change room creation choices", async ({page, request}) => {
  const original = await (await request.get("/api/settings")).json();
  try {
    await page.goto("/admin");
    const showsChoice = page.locator(".admin-choice", {hasText: "TV shows"});
    const genreToggle = page.locator(".setting-row", {hasText: "Match a genre"}).locator("input");
    await expect(showsChoice).toBeVisible({timeout: 3000});
    await expect(genreToggle).toBeAttached({timeout: 3000});
    await showsChoice.click();
    await genreToggle.click({force: true});
    await page.getByRole("button", {name: "Save changes"}).click();
    await expect(page.getByText("Settings saved")).toBeVisible({timeout: 3000});
    await page.goto("/");
    await expect(page.locator(".media-kind button", {hasText: "TV Shows"})).toHaveCount(0);
    await expect(page.locator(".mode-card", {hasText: "Match a genre"})).toHaveCount(0);
  } finally {
    await request.put("/api/settings", {data: original});
  }
});

test("admin controls room-choice memory and matched-title cooldown", async ({page, request}) => {
  const original = await (await request.get("/api/settings")).json();
  try {
    await page.goto("/admin");
    const resetChoices = page.locator(".setting-row", {hasText: "Reset to defaults for new rooms"}).locator("input");
    await expect(resetChoices).toBeAttached();
    if (!(await resetChoices.isChecked())) await resetChoices.click({force: true});
    await page.getByRole("button", {name: /Match history/}).click();
    await expect(page.getByRole("heading", {name: "Previous matches"})).toBeVisible();
    const cooldown = page.locator(".admin-field", {hasText: "Cooldown period"}).locator("input");
    await cooldown.fill("9");
    await page.getByRole("button", {name: "Save changes"}).click();
    await expect(page.getByText("Settings saved")).toBeVisible();
    const saved = await (await request.get("/api/settings")).json();
    expect(saved.reset_room_choices).toBe(true);
    expect(saved.matched_item_cooldown_days).toBe(9);
  } finally {
    await request.put("/api/settings", {data: original});
  }
});

test("a failed first round enters the privacy-safe Second Chance UI", async ({page, request}) => {
  const original = await (await request.get("/api/settings")).json();
  try {
    await request.put("/api/settings", {data: {
      ...original, finite_sessions_enabled: true, finite_card_limit: 3,
    }});
    const room = await (await request.post("/api/rooms", {data: {kind: "movie", mode: "random"}})).json();
    const one = await (await request.post(`/api/rooms/${room.code}/join`, {data: {name: "Alex"}})).json();
    const two = await (await request.post(`/api/rooms/${room.code}/join`, {data: {name: "Sam"}})).json();
    const cards = await (await request.get(`/api/rooms/${room.code}/cards?participant_id=${one.participant_id}`)).json();
    expect(cards.length).toBeGreaterThanOrEqual(2);
    const onePick = cards[0].id;
    const twoPick = cards[1].id;
    for (const participant of [one.participant_id, two.participant_id]) {
      for (const card of cards) {
        const direction = participant === one.participant_id
          ? (card.id === onePick ? "right" : "left")
          : (card.id === twoPick ? "right" : "left");
        await request.post(`/api/rooms/${room.code}/swipe`, {data: {
          participant_id: participant, media_id: card.id, direction,
        }});
      }
    }
    const state = await (await request.get(`/api/rooms/${room.code}`)).json();
    expect(state.phase).toBe("second_chance");

    await page.goto(`/join/${room.code}`);
    await page.evaluate(({key, value}) => localStorage.setItem(key, value), {
      key: `wm-${room.code}`, value: one.participant_id,
    });
    await page.reload();
    await expect(page.getByText("SECOND CHANCE")).toBeVisible();
    await expect(page.getByText("The other person chose this")).toBeVisible();
    await expect(page.getByRole("button", {name: /Top Choice/})).toHaveCount(0);
    await page.getByRole("button", {name: "Like"}).click();
    await expect(page.getByText("IT'S A MATCH")).toBeVisible();
  } finally {
    await request.put("/api/settings", {data: original});
  }
});

test("undo and another-match controls work in a finite room", async ({page, request}) => {
  const original = await (await request.get("/api/settings")).json();
  try {
    await request.put("/api/settings", {data: {
      ...original, undo_enabled: true, another_match_enabled: true,
      finite_sessions_enabled: true, finite_card_limit: 3,
    }});
    const room = await (await request.post("/api/rooms", {data: {kind: "movie", mode: "random"}})).json();
    const one = await (await request.post(`/api/rooms/${room.code}/join`, {data: {name: "Alex"}})).json();
    const two = await (await request.post(`/api/rooms/${room.code}/join`, {data: {name: "Sam"}})).json();
    await page.goto(`/join/${room.code}`);
    await page.evaluate(({key, value}) => localStorage.setItem(key, value), {
      key: `wm-${room.code}`, value: one.participant_id,
    });
    await page.reload();
    const topChoice = page.getByRole("button", {name: "Top Choice, 1 remaining"});
    await expect(topChoice).toBeVisible();
    await topChoice.click();
    await expect(page.getByRole("heading", {name: "You\'re all caught up"})).toBeVisible();
    const undo = page.getByRole("button", {name: /Undo last swipe/});
    await expect(undo).toBeEnabled();
    await undo.click();
    await expect(page.getByRole("button", {name: "Top Choice, 1 remaining"})).toBeEnabled();

    await page.getByRole("button", {name: "Pass"}).click();
    await expect(undo).toBeEnabled();
    await undo.click();
    await expect(undo).toBeDisabled();

    const cardsOne = await (await request.get(`/api/rooms/${room.code}/cards?participant_id=${one.participant_id}`)).json();
    const cardsTwo = await (await request.get(`/api/rooms/${room.code}/cards?participant_id=${two.participant_id}`)).json();
    const twoIds = new Set(cardsTwo.map((card: {id: string}) => card.id));
    const mediaId = cardsOne.find((card: {id: string}) => twoIds.has(card.id)).id;
    await request.post(`/api/rooms/${room.code}/swipe`, {data: {participant_id: one.participant_id, media_id: mediaId, direction: "right"}});
    await request.post(`/api/rooms/${room.code}/swipe`, {data: {participant_id: two.participant_id, media_id: mediaId, direction: "right"}});
    const another = page.getByRole("button", {name: "Find another match"});
    await expect(another).toBeVisible();
    await another.click();
    await expect(page.getByRole("heading", {name: "No match this time."})).toBeVisible();
  } finally {
    await request.put("/api/settings", {data: original});
  }
});
