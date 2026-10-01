import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const source = await readFile(new URL("../src/modules/tweets/tweets.js", import.meta.url), "utf8");
const storageKey = "ai-workbench:tweets:v1";
const oldMarkerKeys = [
  "ai-workbench:tweets:seeded:v1",
  "ai-workbench:tweets:twitter-seeded:v3",
  "ai-workbench:tweets:media-migrated:v1",
  "ai-workbench:tweets:original-copy-migrated:v1",
];

// Keep every test inside a VM and an in-memory store. Access to the private
// loader is appended only to this test's source, without changing product APIs.
async function createHarness(entries = [], { storageThrows = false } = {}) {
  const store = new Map(entries);
  const writes = [];
  const context = vm.createContext({
    URL,
    window: {},
    localStorage: {
      getItem(key) {
        if (storageThrows) throw new Error("Storage unavailable");
        return store.has(key) ? store.get(key) : null;
      },
      setItem(key, value) {
        writes.push({ key, value });
        store.set(key, String(value));
      },
    },
  });
  const module = new vm.SourceTextModule(
    source + "\nexport { loadTweets as testLoadTweets, saveTweets as testSaveTweets };\n",
    { context, identifier: "tweets-storage-test" },
  );
  await module.link(() => { throw new Error("Unexpected dependency"); });
  await module.evaluate();
  return {
    store,
    writes,
    load: () => JSON.parse(JSON.stringify(module.namespace.testLoadTweets())),
    save: (items) => module.namespace.testSaveTweets(items),
  };
}

function sampleTweet(overrides = {}) {
  return {
    id: "tweet-test-custom",
    url: "https://x.com/example/status/123456789",
    authorName: "Test author",
    handle: "@example",
    body: "My own saved text\nWith a second line.",
    mediaType: "video",
    mediaUrl: "https://example.invalid/custom-cover.png",
    mediaUrls: ["https://example.invalid/custom-cover.png", "https://example.invalid/second.png"],
    videoUrl: "https://example.invalid/custom-video.mp4",
    avatarUrl: "https://example.invalid/custom-avatar.png",
    tags: ["mine"],
    note: "My own note",
    addedAt: "2026-01-01T00:00:00.000Z",
    accent: "#123456",
    metadataSource: "custom",
    metadataFetchedAt: "2026-01-02T00:00:00.000Z",
    metadataUrl: "https://example.invalid/custom-metadata",
    customProperty: { retained: true },
    ...overrides,
  };
}

test("a new store remains empty without any startup write", async () => {
  const harness = await createHarness();
  assert.deepEqual(harness.load(), []);
  assert.deepEqual(harness.load(), []);
  assert.deepEqual(harness.writes, []);
  assert.equal(harness.store.size, 0);
});

test("old seed and migration flags cannot populate an empty store", async () => {
  for (const entries of [[], oldMarkerKeys.map((key) => [key, "1"])]) {
    const harness = await createHarness(entries);
    assert.deepEqual(harness.load(), []);
    assert.deepEqual(harness.writes, []);
    assert.deepEqual([...harness.store], entries);
  }
});

test("saved custom content, links, media and metadata survive repeated reads", async () => {
  const tweet = sampleTweet();
  const raw = JSON.stringify([tweet]);
  const markers = oldMarkerKeys.map((key) => [key, "unchanged"]);
  const harness = await createHarness([[storageKey, raw], ...markers]);
  assert.deepEqual(harness.load(), [tweet]);
  assert.deepEqual(harness.load(), [tweet]);
  assert.equal(harness.store.get(storageKey), raw);
  assert.deepEqual(harness.writes, []);
  for (const [key, value] of markers) assert.equal(harness.store.get(key), value);
});

test("old seed ids keep their saved content and do not restore other seed ids", async () => {
  for (const id of ["tweet-seed-product-note", "tweet-seed-robot-paper", "tweet-seed-video-thread"]) {
    const tweet = sampleTweet({ id });
    const raw = JSON.stringify([tweet]);
    const harness = await createHarness([[storageKey, raw]]);
    assert.deepEqual(harness.load(), [tweet]);
    assert.equal(harness.store.get(storageKey), raw);
    assert.deepEqual(harness.writes, []);
  }
});

test("unsupported old records remain on disk and survive a later explicit save", async () => {
  const visible = sampleTweet();
  const legacy = sampleTweet({ id: "tweet-legacy", url: "https://example.invalid/old-post" });
  const raw = JSON.stringify([visible, legacy]);
  const harness = await createHarness([[storageKey, raw]]);
  assert.deepEqual(harness.load(), [visible]);
  assert.equal(harness.store.get(storageKey), raw);
  assert.deepEqual(harness.writes, []);
  const edited = { ...visible, note: "Intentionally edited" };
  harness.save([edited]);
  assert.deepEqual(JSON.parse(harness.store.get(storageKey)), [edited, legacy]);
  assert.equal(harness.writes.length, 1);
  assert.equal(harness.writes[0].key, storageKey);
});

test("invalid JSON or non-array storage is never overwritten on load", async () => {
  for (const raw of ["{bad json", '{"oldShape":true}', "null", "[]"]) {
    const harness = await createHarness([[storageKey, raw]]);
    assert.deepEqual(harness.load(), []);
    assert.equal(harness.store.get(storageKey), raw);
    assert.deepEqual(harness.writes, []);
  }
});

test("unavailable storage does not trigger a fallback write", async () => {
  const harness = await createHarness([], { storageThrows: true });
  assert.deepEqual(harness.load(), []);
  assert.deepEqual(harness.writes, []);
});
