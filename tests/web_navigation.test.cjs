/* Optional browser regression: uses an existing novel without generating or editing content. */
const assert = require("node:assert/strict");
const { test } = require("node:test");

test("bookshelf opens a novel, scene navigation and refresh preserve the writing page", {
  skip: !process.env.AINOVEL_LEGACY_BROWSER_TEST,
}, async () => {
  const { chromium } = require(process.env.AINOVEL_PLAYWRIGHT_PATH || "playwright");
  const browser = await chromium.launch({
    headless: true, executablePath: process.env.AINOVEL_BROWSER_EXECUTABLE || undefined,
  });
  try {
    const page = await browser.newPage({baseURL:process.env.AINOVEL_TEST_URL || "http://127.0.0.1:8000"});
    // The MVP does not ship a favicon; exclude that browser-generated request.
    await page.route("**/favicon.ico", route => route.fulfill({status:204}));
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    page.on("console", message => { if (message.type() === "error") errors.push(message.text()); });
    await page.goto(process.env.AINOVEL_TEST_URL || "http://127.0.0.1:8000");
    const stories = await (await page.request.get("/api/stories")).json();
    const story = stories.find(item => item.scene_count > 0);
    assert.ok(story,"Browser navigation test needs a novel containing a scene");
    const book = page.locator("#stories .book-card").filter({hasText:story.title}).first();
    await book.waitFor();
    await book.click();
    await page.locator("#workspace:not([hidden])").waitFor();
    await page.locator("#story-contents .contents-scene").first().waitFor();
    assert.equal(await page.locator("#bookshelf").isVisible(), false);
    const storyUrl = page.url();
    assert.match(storyUrl, /#story\/[0-9a-f-]{36}$/);
    await page.locator("#story-contents .contents-scene").first().click();
    await page.reload();
    await page.locator("#workspace:not([hidden])").waitFor();
    await page.locator("#story-contents .contents-scene").first().waitFor();
    assert.equal(page.url(), storyUrl);
    await page.locator("#tab-records").click();
    await page.locator("#record-category").selectOption("world");
    await page.locator("#story-records").getByText("世界观条目", {exact:true}).waitFor();
    await page.locator("#story-records .record-row").first().getByRole("button", {name:"编辑",exact:true}).click();
    assert.equal(await page.locator("#modal-body textarea").inputValue(), story.title);
    await page.locator("#modal-close").click();
    const worldEntries = page.locator("#story-records .record-group").filter({hasText:"世界观条目"});
    await worldEntries.getByRole("button", {name:"＋",exact:true}).click();
    await page.getByLabel("名称", {exact:true}).fill("雨为什么往上落");
    await page.getByLabel("值", {exact:true}).fill("钟楼附近的异常");
    await page.getByRole("button", {name:"＋ 子字段",exact:true}).click();
    assert.equal(await page.locator("#modal-body .custom-child").count(), 1);
    await page.locator("#modal-close").click();
    await page.locator("#record-category").selectOption("writer");
    await page.locator("#story-records").getByText("主线", {exact:true}).waitFor();
    await page.locator("#story-records").getByText("伏笔", {exact:true}).waitFor();
    await page.locator("#record-category").selectOption("characters");
    await page.locator("#story-records").getByText("档案条目", {exact:true}).first().waitFor();
    await page.locator("#story-records").getByText("绰号与来源", {exact:true}).first().waitFor();
    await page.locator("#back-shelf").click();
    await page.locator("#bookshelf:not([hidden])").waitFor();
    assert.equal(await page.locator("#workspace").isVisible(), false);
    await page.goBack();
    await page.locator("#workspace:not([hidden])").waitFor();
    assert.deepEqual(errors, []);
    await page.setViewportSize({width:390,height:844});
    await page.locator("#toggle-contents").click();
    assert.equal(await page.locator("#contents").isVisible(), true);
    await page.locator("#story-contents .contents-scene").first().click();
    assert.equal(await page.locator("#contents").isVisible(), false);
    assert.equal(await page.locator("#chat-input").isVisible(), true);
  } finally { await browser.close(); }
});
