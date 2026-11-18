import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { test } from "node:test";

import worker, { FALLBACK_HTML, FALLBACK_STYLE } from "./fallback-worker.mjs";

const ORIGIN_DOWN_STATUSES = [502, 503, 504, 520, 521, 522, 523, 524, 525, 526, 527, 530];

function withFetch(impl, run) {
  const original = globalThis.fetch;
  globalThis.fetch = impl;
  return run().finally(() => {
    globalThis.fetch = original;
  });
}

function request(path, init = {}) {
  return new Request(`https://status.walshit.com${path}`, init);
}

test("healthy origin responses pass through untouched", async () => {
  const upstream = new Response("ok", { status: 200, headers: { "x-origin": "1" } });
  await withFetch(async () => upstream, async () => {
    const response = await worker.fetch(request("/"));
    assert.equal(response, upstream);
  });
});

test("ordinary client errors from the origin pass through", async () => {
  const upstream = new Response("missing", { status: 404 });
  await withFetch(async () => upstream, async () => {
    assert.equal(await worker.fetch(request("/nope")), upstream);
  });
});

for (const status of ORIGIN_DOWN_STATUSES) {
  test(`origin status ${status} on the page serves the fallback`, async () => {
    await withFetch(async () => new Response("down", { status }), async () => {
      const response = await worker.fetch(request("/"));
      assert.equal(response.status, 503);
      assert.equal(await response.text(), FALLBACK_HTML);
    });
  });
}

test("a thrown origin fetch serves the fallback for page requests", async () => {
  await withFetch(async () => {
    throw new Error("connect failed");
  }, async () => {
    const response = await worker.fetch(request("/index.html"));
    assert.equal(response.status, 503);
    assert.equal(await response.text(), FALLBACK_HTML);
  });
});

test("fallback response is uncached, typed, and locked down", async () => {
  await withFetch(async () => new Response("down", { status: 530 }), async () => {
    const response = await worker.fetch(request("/"));
    const headers = response.headers;
    assert.equal(headers.get("content-type"), "text/html; charset=utf-8");
    assert.equal(headers.get("cache-control"), "no-store, max-age=0");
    assert.equal(headers.get("retry-after"), "60");
    assert.equal(headers.get("x-content-type-options"), "nosniff");
    assert.equal(headers.get("referrer-policy"), "no-referrer");
    const styleHash = createHash("sha256").update(FALLBACK_STYLE).digest("base64");
    assert.equal(
      headers.get("content-security-policy"),
      `default-src 'none'; style-src 'sha256-${styleHash}'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'`,
    );
  });
});

test("HEAD page requests get fallback headers without a body", async () => {
  await withFetch(async () => new Response(null, { status: 530 }), async () => {
    const response = await worker.fetch(request("/", { method: "HEAD" }));
    assert.equal(response.status, 503);
    assert.equal(await response.text(), "");
  });
});

test("non-page paths keep the origin failure so monitors still see it", async () => {
  for (const path of ["/healthz.txt", "/status/v2/summary.json", "/status.css", "/favicon.ico"]) {
    const upstream = new Response("down", { status: 530 });
    await withFetch(async () => upstream, async () => {
      assert.equal(await worker.fetch(request(path)), upstream, path);
    });
  }
});

test("thrown fetch on non-page paths becomes an uncached 502", async () => {
  await withFetch(async () => {
    throw new Error("connect failed");
  }, async () => {
    const response = await worker.fetch(request("/status/v2/summary.json"));
    assert.equal(response.status, 502);
    assert.equal(response.headers.get("cache-control"), "no-store, max-age=0");
  });
});

test("non-GET page requests are never answered with the fallback", async () => {
  const upstream = new Response("down", { status: 530 });
  await withFetch(async () => upstream, async () => {
    assert.equal(await worker.fetch(request("/", { method: "POST" })), upstream);
  });
});

test("fallback copy is public-safe and self-contained", () => {
  assert.match(FALLBACK_HTML, /^<!doctype html>/i);
  assert.ok(FALLBACK_HTML.includes(`<style>${FALLBACK_STYLE}</style>`));
  assert.doesNotMatch(FALLBACK_HTML, /<script|<img|<link|src=|url\(/i);
  assert.doesNotMatch(FALLBACK_HTML, /\b(?:10|127|172|192)\.\d+\.\d+\.\d+\b/);
  assert.doesNotMatch(FALLBACK_HTML, /proxmox|tunnel|cloudflared|docker|nas|truenas|plex\.direct/i);
  assert.ok(FALLBACK_HTML.includes("https://walshit.com/"));
  assert.ok(FALLBACK_HTML.length < 8192);
});
