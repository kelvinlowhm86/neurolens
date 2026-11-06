// POST JSON with its SHA-256 in x-amz-content-sha256: behind CloudFront's origin access control
// a POST without it is refused before it reaches the app (M3b). A classic script, so both
// pages' scripts can call it. `options` adds fetch options (keepalive for a POST that must
// survive the page navigating away).
async function postJSON(url, body, options = {}) {
  const text = JSON.stringify(body);
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
  const hash = Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2, '0')).join('');
  return fetch(url, {
    ...options,
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'x-amz-content-sha256': hash },
    body: text,
  });
}
