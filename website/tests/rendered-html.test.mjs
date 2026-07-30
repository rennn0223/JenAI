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
  assert.match(html, /JENAI\s*(?:<!-- -->)?2\.5\.1(?:<!-- -->)? · SIMULATION-FIRST ROBOTICS/i);
  assert.match(html, /<title>JenAI Documentation<\/title>/i);
  assert.match(html, /Robot workflows selected by AI/i);
  assert.match(html, /v(?:<!-- -->)?2\.5\.1/i);
  assert.doesNotMatch(html, /v2\.4\.0/i);
  assert.match(html, /href="#main-content"/i);
  assert.match(html, /role="combobox"/i);
  assert.match(html, /aria-controls="documentation-search-results"/i);
  assert.match(html, /aria-controls="documentation-navigation"/i);
  assert.match(html, /aria-expanded="false"/i);
  assert.match(html, /id="main-content"/i);
  assert.match(html, /Semantic area patrol/i);
  assert.doesNotMatch(html, /codex-preview/i);
});

test("renders the semantic area patrol guide", async () => {
  const response = await render("/docs/area-patrol");
  assert.equal(response.status, 200);

  const html = await response.text();
  assert.match(html, /Semantic area patrol/i);
  assert.match(html, /JenAI v(?:<!-- -->)?2\.5\.1/i);
  assert.match(html, /does not choose every waypoint in a token-by-token loop/i);
  assert.match(html, /partial_success/i);
});

test("keeps the conventional QuickStart URL available", async () => {
  const response = await render("/docs/quickstart");
  assert.equal(response.status, 200);

  const html = await response.text();
  assert.match(html, /Isaac Sim QuickStart/i);
  assert.match(html, /JenAI v(?:<!-- -->)?2\.5\.1/i);
  assert.match(html, /isaac_nav2\.sh restart/i);
  assert.doesNotMatch(html, /ros2 launch carter_navigation/i);
});

test("describes NXDog support without overclaiming physical motion", async () => {
  const response = await render("/docs/future-work");
  assert.equal(response.status, 200);

  const html = await response.text();
  assert.match(html, /Quadruped motion integration/i);
  assert.match(html, /Read-only NXDog observation exists/i);
  assert.match(html, /physical validation/i);
  assert.doesNotMatch(html, /<strong>Quadruped integration<\/strong>/i);
});
