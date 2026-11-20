// The samples-only site has no server. This shim, loaded before the product pages' scripts,
// serves the public limits, reports a signed-out visitor, answers every other API call as
// unavailable, and turns sign-in/upload entry points into a disabled "Uploads closed (demo)".
(() => {
  "use strict";
  const CLOSED = "Uploads closed";
  const realFetch = window.fetch.bind(window);
  const RealResponse = window.Response;

  function textOf(input) {
    if (typeof input === "string") return input;
    if (input && typeof input.url === "string") return input.url;
    return String(input);
  }

  function apiPath(input) {
    const path = textOf(input).split(/[?#]/)[0];
    const hit = path.indexOf("/api/");
    if (hit !== -1) return path.slice(hit);
    if (path === "/api" || path === "api") return "/api";
    if (path.startsWith("api/")) return "/" + path;
    return null;
  }

  function dataPath(input) {
    const raw = textOf(input);
    const hit = raw.indexOf("/data/");
    if (hit !== -1) return raw.slice(hit + 1);
    const match = /^\.?\/?(data\/.*)$/.exec(raw);
    return match ? match[1] : null;
  }

  function reply(body, status) {
    return new RealResponse(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    });
  }

  let limits = null;
  function limitsReply() {
    if (!limits) {
      const url = new URL("limits.json", document.baseURI).href;
      limits = realFetch(url, { cache: "no-store" })
        .then(res => {
          if (!res.ok) throw new Error("limits.json " + res.status);
          return res.text();
        })
        .then(text => new RealResponse(text, {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }))
        .catch(() => reply({ error: "unavailable", message: CLOSED }, 503));
    }
    return limits.then(res => res.clone());
  }

  window.fetch = function (input, init) {
    const api = apiPath(input);
    if (api === "/api/limits") return limitsReply();
    if (api === "/api/me") {
      return Promise.resolve(reply({ error: "not_signed_in", message: "Please sign in." }, 401));
    }
    if (api) return Promise.resolve(reply({ error: "unavailable", message: CLOSED }, 503));
    const data = dataPath(input);
    if (data !== null) return realFetch(data, init);
    return realFetch(input, init);
  };

  const style = document.createElement("style");
  style.textContent = ".nl-demo-closed{opacity:.55;cursor:default;pointer-events:none;"
    + "text-decoration:none}"
    // Parts that only make sense with a server: early access, feedback form, the upload call to action.
    // With those gone, "How it works" sits right above the footer, so its footer link is dropped too.
    + "a[href='#access'],#access,.footer-feedback,section.closing,#signInPanel .actions,"
    + ".footer-links a[href='#how'],"
    + "#signedOutNote{display:none!important}"
    // The home hero already says uploads are closed; a second disabled button in the top bar looks broken.
    + "#topSignIn{display:none!important}"
    // On the samples list the side column would hold only the closed notice: give the grid the full
    // width, say it once in the heading, and show the page-end privacy link instead.
    + "#appShell.view-chooser{grid-template-columns:minmax(0,1fr)}"
    + "#appShell.view-chooser .side{display:none}"
    + "body:has(#appShell.view-chooser) .privacy-note.page-end{display:block}";
  (document.head || document.documentElement).append(style);

  function isLoginAnchor(anchor) {
    if (anchor.dataset.nlLogin === "1") return true;
    const href = anchor.getAttribute("href") || "";
    return href === "#login" || href === "/login" || /(^|\/)login(?:[?#]|$)/.test(href);
  }

  function closeAnchor(anchor) {
    if (anchor.dataset.nlClosed === "1") return;
    anchor.dataset.nlClosed = "1";
    anchor.textContent = CLOSED;
    anchor.removeAttribute("href");
    anchor.setAttribute("aria-disabled", "true");
    anchor.classList.add("nl-demo-closed");
  }

  function closeUpload(element) {
    if (!element) return;
    element.classList.add("nl-demo-closed");
    element.setAttribute("aria-disabled", "true");
    if ("disabled" in element) element.disabled = true;
    if (element.id === "batchInput") return;
    const title = element.querySelector("#uploadZoneTitle, #dialogZoneTitle");
    if (title) {
      if (title.textContent !== CLOSED) title.textContent = CLOSED;
    } else if (element.textContent !== CLOSED) {
      element.textContent = CLOSED;
    }
  }

  const UPLOADS = ["#topUpload", "#uploadZone", "#batchUpload", "#dialogZone", "#batchInput"];

  function setText(selector, text) {
    const element = document.querySelector(selector);
    if (element && element.textContent !== text) element.textContent = text;
  }

  function apply() {
    setText("#signInPanel h2", "Uploads are closed");
    setText("#signInPanel p:not([id])", "This is a demo. Explore the samples.");
    setText(".samples-free", "Uploads closed · free to explore");
    document.querySelectorAll("a[href]").forEach(anchor => {
      if (isLoginAnchor(anchor)) closeAnchor(anchor);
    });
    UPLOADS.forEach(selector => closeUpload(document.querySelector(selector)));
  }

  let scheduled = false;
  function schedule() {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(() => {
      scheduled = false;
      apply();
    });
  }

  document.addEventListener("click", event => {
    const target = event.target;
    if (!target || !target.closest) return;
    if (!target.closest("a[data-nl-closed], " + UPLOADS.join(", "))) return;
    event.preventDefault();
    event.stopImmediatePropagation();
  }, true);

  try {
    const url = new URL(window.location.href);
    if (url.searchParams.has("upload")) {
      url.searchParams.delete("upload");
      history.replaceState(null, "", url.pathname + url.search + url.hash);
    }
  } catch (error) {
    // A browser without URL or history still browses the samples.
  }

  function start() {
    apply();
    const root = document.body || document.documentElement;
    new MutationObserver(schedule).observe(root, {
      subtree: true,
      childList: true,
      characterData: true,
      attributes: true,
      attributeFilter: ["href"],
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start, { once: true });
  } else {
    start();
  }
})();
