const ORIGIN_DOWN_STATUSES = new Set([502, 503, 504, 520, 521, 522, 523, 524, 525, 526, 527, 530]);
const PAGE_PATHS = new Set(["/", "/index.html"]);
const PAGE_METHODS = new Set(["GET", "HEAD"]);

export const FALLBACK_STYLE = [
  "body{margin:0;background:#f2ead9;color:#171713;font:17px/1.55 Georgia,'Times New Roman',serif}",
  "main{max-width:40rem;margin:0 auto;padding:12vh 1.5rem 3rem}",
  ".brand{font:700 1.1rem/1 Georgia,serif;letter-spacing:.08em;color:#171713;text-decoration:none}",
  ".brand span{color:#c93424;font-style:italic}",
  ".label{margin:2.5rem 0 .5rem;font:600 .78rem/1.2 'Arial Narrow',Arial,sans-serif;letter-spacing:.14em;text-transform:uppercase;color:#c93424}",
  "h1{margin:0 0 1rem;font-size:clamp(1.9rem,6vw,2.8rem);line-height:1.12;font-weight:400}",
  "p{margin:0 0 1rem}",
  "footer{margin-top:2.5rem;border-top:1px solid #171713;padding-top:1rem;font:.85rem/1.4 'Arial Narrow',Arial,sans-serif}",
  "a{color:#171713}",
].join("");

export const FALLBACK_HTML = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>Walshit / Status unreachable</title>
<style>${FALLBACK_STYLE}</style>
</head>
<body>
<main>
<a class="brand" href="https://walshit.com/">WAL<span>/</span>SHIT</a>
<p class="label">Service status &middot; Unreachable</p>
<h1>We can&rsquo;t reach the house right now.</h1>
<p>The status page runs on the same home connection as Walshit services. When this page can&rsquo;t be reached, the home internet connection or server is most likely down, and Watch, Home base, Requests, Audiobooks and Walshit.com are probably unavailable too.</p>
<p>There&rsquo;s nothing you need to do. Please check back in a few minutes.</p>
<footer>This notice is served from the edge while the house is offline.</footer>
</main>
</body>
</html>
`;

async function sha256Base64(text) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return btoa(String.fromCharCode(...new Uint8Array(digest)));
}

async function fallbackResponse(method) {
  const styleHash = await sha256Base64(FALLBACK_STYLE);
  return new Response(method === "HEAD" ? null : FALLBACK_HTML, {
    status: 503,
    headers: {
      "content-type": "text/html; charset=utf-8",
      "cache-control": "no-store, max-age=0",
      "retry-after": "60",
      "x-content-type-options": "nosniff",
      "referrer-policy": "no-referrer",
      "content-security-policy": `default-src 'none'; style-src 'sha256-${styleHash}'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'`,
    },
  });
}

function isPageRequest(request) {
  return PAGE_METHODS.has(request.method) && PAGE_PATHS.has(new URL(request.url).pathname);
}

export default {
  async fetch(request) {
    const page = isPageRequest(request);
    let upstream;
    try {
      upstream = await fetch(request);
    } catch {
      if (page) {
        return fallbackResponse(request.method);
      }
      return new Response("origin unreachable", {
        status: 502,
        headers: { "content-type": "text/plain; charset=utf-8", "cache-control": "no-store, max-age=0" },
      });
    }
    if (page && ORIGIN_DOWN_STATUSES.has(upstream.status)) {
      return fallbackResponse(request.method);
    }
    return upstream;
  },
};
