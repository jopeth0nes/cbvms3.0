import assert from "node:assert/strict";
import { build } from "esbuild";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname } from "node:path";

const routePlugin = {
  name: "mock-portal-api-route",
  setup(builder) {
    builder.onResolve({ filter: /\.\.\/app\/api\/.*route$/ }, () => ({ path: "api-route", namespace: "fixture" }));
    builder.onResolve({ filter: /\.\.\/lib\/password-crypto$/ }, () => ({ path: "crypto-class", namespace: "fixture" }));
    builder.onLoad({ filter: /.*/, namespace: "fixture" }, () => ({
      contents: `
        export async function GET(request) { return Response.json({ method: request.method }); }
        export async function POST(request) { return Response.json({ method: request.method }); }
        export class PasswordCrypto {}
      `,
      loader: "js",
    }));
  },
};

const buildResult = await build({
  stdin: {
    contents: await readFile(new URL("./worker.ts", import.meta.url), "utf8"),
    resolveDir: dirname(fileURLToPath(import.meta.url)),
    sourcefile: "worker.ts",
    loader: "ts",
  },
  bundle: true,
  format: "esm",
  platform: "browser",
  write: false,
  plugins: [routePlugin],
});
const moduleUrl = `data:text/javascript;base64,${Buffer.from(buildResult.outputFiles[0].text).toString("base64")}`;
const { default: worker } = await import(moduleUrl);
const assetCalls = [];
const env = {
  ASSETS: {
    async fetch(request) {
      assetCalls.push(new URL(request.url).pathname);
      return new Response(`asset:${new URL(request.url).pathname}`, {
        headers: { "Content-Type": "text/plain" },
      });
    },
  },
};

const home = await worker.fetch(new Request("https://portal.example/"), env);
assert.equal(home.status, 200);
assert.equal(await home.text(), "asset:/portal.html");
assert.equal(home.headers.get("X-Frame-Options"), "DENY");
assert.equal(home.headers.get("Content-Security-Policy").includes("object-src 'none'"), true);

const script = await worker.fetch(new Request("https://portal.example/app.js"), env);
assert.equal(script.status, 200);
assert.equal(await script.text(), "asset:/app.js");

const get = await worker.fetch(new Request("https://portal.example/api/me"), env);
assert.deepEqual(await get.json(), { method: "GET" });
const post = await worker.fetch(new Request("https://portal.example/api/login", { method: "POST" }), env);
assert.deepEqual(await post.json(), { method: "POST" });

const denied = await worker.fetch(new Request("https://portal.example/.openai/hosting.json"), env);
assert.equal(denied.status, 404);
assert.equal(assetCalls.includes("/.openai/hosting.json"), false);
const badMethod = await worker.fetch(new Request("https://portal.example/api/login", { method: "DELETE" }), env);
assert.equal(badMethod.status, 405);
assert.equal(badMethod.headers.get("Allow"), "GET, POST");

console.log("Standalone Cloudflare Worker routing checks passed.");
