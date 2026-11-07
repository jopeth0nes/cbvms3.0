import { GET, POST } from "../app/api/[...path]/route";
import { campusRegistration } from '../lib/campus-registration';
import { campusRecords } from '../lib/campus-records';
import { patchPortalScript } from '../lib/portal-decision-display';
export { PasswordCrypto } from '../lib/password-crypto';

interface PortalEnv {
  DB: D1Database;
  BUCKET: R2Bucket;
  ASSETS: Fetcher;
  SYNC_KEY: string;
  CAMPUS_REGISTRATION_KEY?: string;
}

const STATIC_ASSETS = new Map([
  ["/", "/portal.html"],
  ["/portal.html", "/portal.html"],
  ["/style.css", "/style.css"],
  ["/app.js", "/app.js"],
  ["/favicon.svg", "/favicon.svg"],
]);

function secure(response: Response): Response {
  const headers = new Headers(response.headers);
  headers.set("X-Content-Type-Options", "nosniff");
  headers.set("Referrer-Policy", "no-referrer");
  headers.set("X-Frame-Options", "DENY");
  headers.set("Permissions-Policy", "camera=(), microphone=(), geolocation=()");
  headers.set(
    "Content-Security-Policy",
    "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; " +
      "script-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; " +
      "form-action 'self'; frame-ancestors 'none'",
  );
  return new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers,
  });
}

function notFound(): Response {
  return secure(new Response("Not found", { status: 404 }));
}

export default {
  async fetch(request: Request, env: PortalEnv): Promise<Response> {
    const url = new URL(request.url);
    if (url.pathname === '/api/campus/registrations') return secure(await campusRegistration(request, env));
    if (url.pathname === '/api/campus/records') return secure(await campusRecords(request, env));
    if (url.pathname.startsWith("/api/")) {
      if (request.method !== "GET" && request.method !== "POST") {
        return secure(new Response("Method not allowed", {
          status: 405,
          headers: { Allow: "GET, POST" },
        }));
      }
      return secure(await (request.method === "GET" ? GET(request) : POST(request)));
    }

    const assetPath = STATIC_ASSETS.get(url.pathname);
    if (!assetPath || (request.method !== "GET" && request.method !== "HEAD")) {
      return notFound();
    }

    const assetRequest = new Request(new URL(assetPath, url.origin), request);
    if (url.pathname === '/app.js' && request.method === 'GET') {
      assetRequest.headers.delete('If-None-Match'); assetRequest.headers.delete('If-Modified-Since');
    }
    const response = await env.ASSETS.fetch(assetRequest);
    if (response.status === 404) return notFound();
    if (url.pathname === '/app.js' && request.method === 'GET' && response.status === 200) {
      const headers = new Headers(response.headers);
      headers.delete('Content-Length'); headers.delete('ETag'); headers.delete('Content-Encoding');
      headers.set('Cache-Control','no-store');
      return secure(new Response(patchPortalScript(await response.text()), {headers}));
    }
    return secure(response);
  },
};
