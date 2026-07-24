import assert from "node:assert/strict";
import test from "node:test";

async function render(path = "/") {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);

  return worker.fetch(
    new Request(new URL(path, "http://localhost"), {
      headers: { accept: "text/html" },
    }),
    {
      ASSETS: {
        fetch: async () => new Response("Not found", { status: 404 }),
      },
    },
    {
      waitUntil() {},
      passThroughOnException() {},
    },
  );
}

test("renders the JenAI documentation home page", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);

  const html = await response.text();
  assert.match(html, /JENAI 2\.4 · SIMULATION-FIRST ROBOTICS/i);
  assert.match(html, /<title>JenAI Documentation<\/title>/i);
  assert.match(html, /Robot workflows selected by AI/i);
  assert.match(html, /v2\.4\.0/i);
  assert.doesNotMatch(html, /v2\.2\.0/i);
  assert.match(html, /Semantic area patrol/i);
  assert.doesNotMatch(html, /codex-preview/i);
});

test("renders the semantic area patrol guide", async () => {
  const response = await render("/docs/area-patrol");
  assert.equal(response.status, 200);

  const html = await response.text();
  assert.match(html, /Semantic area patrol/i);
  assert.match(html, /JenAI v2\.4\.0/i);
  assert.match(html, /does not choose every waypoint in a token-by-token loop/i);
  assert.match(html, /partial_success/i);
});

test("keeps the conventional QuickStart URL available", async () => {
  const response = await render("/docs/quickstart");
  assert.equal(response.status, 200);

  const html = await response.text();
  assert.match(html, /Isaac Sim QuickStart/i);
  assert.match(html, /JenAI v2\.4\.0/i);
});
