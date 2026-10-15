/* Gleaner frontend. Vanilla JS, no build step. All user data lives in localStorage. */
(() => {
  "use strict";

  // ---------- Storage (every call guarded; falls back to memory) ----------
  const memoryStore = {};
  const store = {
    get(key, fallback) {
      let raw = null;
      try {
        raw = window.localStorage.getItem(key);
      } catch (e) {
        raw = key in memoryStore ? memoryStore[key] : null;
      }
      if (raw === null || raw === undefined) return fallback;
      try {
        return JSON.parse(raw);
      } catch (e) {
        return fallback;
      }
    },
    set(key, value) {
      const raw = JSON.stringify(value);
      memoryStore[key] = raw;
      try {
        window.localStorage.setItem(key, raw);
      } catch (e) {
        /* storage full or unavailable: keep the in-memory copy */
      }
    },
  };

  const KEYS = {
    feeds: "gleaner.feeds",
    read: "gleaner.read",
    unreadOnly: "gleaner.unreadOnly",
    tutorialSeen: "gleaner.tutorialSeen",
  };
  const MAX_READ_IDS = 3000;
  const MAX_ITEMS_SHOWN = 400;
  const FETCH_CONCURRENCY = 4;

  // ---------- State ----------
  const state = {
    feeds: sanitizeFeeds(store.get(KEYS.feeds, [])),
    read: new Set(store.get(KEYS.read, [])),
    unreadOnly: !!store.get(KEYS.unreadOnly, false),
    results: new Map(), // feed url -> {title, items, error}
    starter: null,
    loading: false,
    view: "feed",
  };

  function sanitizeFeeds(list) {
    if (!Array.isArray(list)) return [];
    return list
      .filter((f) => f && typeof f.url === "string")
      .map((f) => ({
        url: f.url,
        title: typeof f.title === "string" ? f.title : f.url,
        enabled: f.enabled !== false,
        keywords: Array.isArray(f.keywords) ? f.keywords.filter((k) => typeof k === "string") : [],
      }));
  }

  const saveFeeds = () => store.set(KEYS.feeds, state.feeds);
  const saveRead = () => {
    let ids = Array.from(state.read);
    if (ids.length > MAX_READ_IDS) {
      ids = ids.slice(ids.length - MAX_READ_IDS);
      state.read = new Set(ids);
    }
    store.set(KEYS.read, ids);
  };

  // ---------- Helpers ----------
  const $ = (sel, root = document) => root.querySelector(sel);

  function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    if (attrs) {
      for (const [key, value] of Object.entries(attrs)) {
        if (value === null || value === undefined || value === false) continue;
        if (key === "class") node.className = value;
        else if (key === "text") node.textContent = value;
        else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
        else node.setAttribute(key, value === true ? "" : value);
      }
    }
    for (const child of children.flat()) {
      if (child === null || child === undefined || child === false) continue;
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
  }

  const escapeRegExp = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

  function keywordRegex(keywords) {
    const words = keywords.map((k) => k.trim()).filter(Boolean).sort((a, b) => b.length - a.length);
    if (!words.length) return null;
    return new RegExp(`(${words.map(escapeRegExp).join("|")})`, "gi");
  }

  // Returns a fragment with matches wrapped in <mark>, built without innerHTML.
  function highlight(text, regex) {
    const frag = document.createDocumentFragment();
    if (!regex || !text) {
      frag.append(text || "");
      return frag;
    }
    let last = 0;
    regex.lastIndex = 0;
    let m;
    while ((m = regex.exec(text)) !== null) {
      if (m[0] === "") {
        regex.lastIndex++;
        continue;
      }
      frag.append(text.slice(last, m.index), el("mark", { text: m[0] }));
      last = m.index + m[0].length;
    }
    frag.append(text.slice(last));
    return frag;
  }

  function matchedKeywords(item, keywords) {
    const hay = `${item.title} ${item.summary}`.toLowerCase();
    return keywords.filter((k) => k.trim() && hay.includes(k.trim().toLowerCase()));
  }

  function timeAgo(iso) {
    if (!iso) return "";
    const then = Date.parse(iso);
    if (Number.isNaN(then)) return "";
    const s = Math.round((Date.now() - then) / 1000);
    if (s < 60) return "just now";
    const m = Math.round(s / 60);
    if (m < 60) return `${m}m ago`;
    const h = Math.round(m / 60);
    if (h < 24) return `${h}h ago`;
    const d = Math.round(h / 24);
    if (d < 7) return `${d}d ago`;
    return new Date(then).toLocaleDateString(undefined, { day: "numeric", month: "short" });
  }

  function hostOf(url) {
    try {
      return new URL(url).hostname.replace(/^www\./, "");
    } catch (e) {
      return url;
    }
  }

  function normaliseUrl(input) {
    let value = (input || "").trim();
    if (!value) return "";
    if (!/^[a-z][a-z0-9+.-]*:/i.test(value)) value = `https://${value}`;
    try {
      const url = new URL(value);
      if (url.protocol !== "http:" && url.protocol !== "https:") return "";
      return url.href;
    } catch (e) {
      return "";
    }
  }

  let toastTimer = null;
  function toast(message) {
    const node = $("#toast");
    node.textContent = message;
    node.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (node.hidden = true), 2600);
  }

  // ---------- API ----------
  async function apiFeed(url) {
    const res = await fetch(`/api/feed?url=${encodeURIComponent(url)}`);
    let data = null;
    try {
      data = await res.json();
    } catch (e) {
      /* non-JSON error page */
    }
    if (!res.ok) throw new Error((data && data.error) || `Request failed (${res.status})`);
    return data;
  }

  async function loadStarter() {
    if (state.starter) return state.starter;
    try {
      const res = await fetch("/api/starter-feeds");
      if (!res.ok) throw new Error();
      state.starter = await res.json();
    } catch (e) {
      state.starter = { categories: [], templates: [] };
    }
    return state.starter;
  }

  // ---------- Feed management ----------
  const findFeed = (url) => state.feeds.find((f) => f.url === url);

  function addFeed(url, title) {
    if (findFeed(url)) return false;
    state.feeds.push({ url, title: title || hostOf(url), enabled: true, keywords: [] });
    saveFeeds();
    return true;
  }

  function removeFeed(url) {
    state.feeds = state.feeds.filter((f) => f.url !== url);
    state.results.delete(url);
    saveFeeds();
  }

  async function refreshAll({ quiet = false } = {}) {
    if (state.loading) return;
    const feeds = state.feeds.filter((f) => f.enabled);
    if (!feeds.length) {
      renderItems();
      return;
    }
    state.loading = true;
    $("#refresh-btn").classList.add("spinning");
    if (!quiet) setStatus("Loading…");
    const queue = feeds.slice();
    const worker = async () => {
      while (queue.length) {
        const feed = queue.shift();
        try {
          const data = await apiFeed(feed.url);
          state.results.set(feed.url, { title: data.title, items: data.items || [], error: null });
          if (feed.title === hostOf(feed.url) && data.title) {
            feed.title = data.title;
            saveFeeds();
          }
        } catch (err) {
          const prev = state.results.get(feed.url);
          state.results.set(feed.url, { title: feed.title, items: prev ? prev.items : [], error: err.message });
        }
        renderItems();
      }
    };
    await Promise.all(Array.from({ length: Math.min(FETCH_CONCURRENCY, feeds.length) }, worker));
    state.loading = false;
    $("#refresh-btn").classList.remove("spinning");
    renderItems();
  }

  function setStatus(text) {
    $("#feed-status").textContent = text;
  }

  // ---------- Rendering: feed list ----------
  function collectItems() {
    const all = [];
    for (const feed of state.feeds) {
      if (!feed.enabled) continue;
      const result = state.results.get(feed.url);
      if (!result) continue;
      const regex = keywordRegex(feed.keywords);
      for (const item of result.items) {
        all.push({
          ...item,
          key: `${feed.url}|${item.id}`,
          feed,
          regex,
          matches: matchedKeywords(item, feed.keywords),
          ts: item.published ? Date.parse(item.published) || 0 : 0,
        });
      }
    }
    all.sort((a, b) => b.ts - a.ts);
    return all;
  }

  function renderItems() {
    const list = $("#item-list");
    const enabled = state.feeds.filter((f) => f.enabled);
    $("#feed-empty").hidden = state.feeds.length > 0;

    let items = collectItems();
    const total = items.length;
    if (state.unreadOnly) items = items.filter((i) => !state.read.has(i.key));
    items = items.slice(0, MAX_ITEMS_SHOWN);

    list.replaceChildren(...items.map(renderItem));

    const failed = enabled.filter((f) => state.results.get(f.url)?.error);
    const parts = [];
    if (state.loading) parts.push("Loading…");
    else if (enabled.length && total === 0 && !failed.length) parts.push("No items yet.");
    if (!state.loading && state.unreadOnly && total > 0 && items.length === 0) parts.push("All caught up.");
    if (failed.length) parts.push(`Could not load: ${failed.map((f) => f.title).join(", ")}`);
    if (state.feeds.length && !enabled.length) parts.push("All feeds are switched off in My Feeds.");
    setStatus(parts.join(" "));
  }

  function renderItem(item) {
    const isRead = state.read.has(item.key);
    const classes = ["item"];
    if (isRead) classes.push("read");
    if (item.matches.length) classes.push("match");

    const title = item.link
      ? el("a", { class: "item-title", href: item.link, target: "_blank", rel: "noopener noreferrer" })
      : el("span", { class: "item-title", role: "button", tabindex: "0" });
    title.append(highlight(item.title, item.regex));

    const node = el(
      "li",
      { class: classes.join(" "), "data-key": item.key },
      el(
        "div",
        { class: "item-meta" },
        el("span", { class: "item-source", text: item.feed.title }),
        item.published ? el("time", { datetime: item.published, text: timeAgo(item.published) }) : null,
        isRead ? el("span", { text: "Read" }) : null
      ),
      title,
      item.summary ? el("p", { class: "item-summary" }, highlight(item.summary, item.regex)) : null,
      item.matches.length
        ? el("div", { class: "item-keywords" }, item.matches.map((k) => el("span", { class: "kw-badge", text: k })))
        : null
    );
    const markRead = () => {
      if (state.read.has(item.key)) return;
      state.read.add(item.key);
      saveRead();
      // Re-render after the navigation starts so the tap is not lost.
      setTimeout(renderItems, 300);
    };
    title.addEventListener("click", markRead);
    title.addEventListener("auxclick", markRead);
    title.addEventListener("keydown", (e) => {
      if (e.key === "Enter") markRead();
    });
    return node;
  }

  // ---------- Rendering: My Feeds ----------
  function renderMyFeeds() {
    const list = $("#my-feeds");
    $("#my-feeds-empty").hidden = state.feeds.length > 0;
    list.replaceChildren(...state.feeds.map(renderFeedCard));
  }

  function renderFeedCard(feed) {
    const toggle = el("input", { type: "checkbox", "aria-label": `Show ${feed.title} in Feed` });
    toggle.checked = feed.enabled;
    toggle.addEventListener("change", () => {
      feed.enabled = toggle.checked;
      saveFeeds();
      renderMyFeeds();
      if (feed.enabled && !state.results.has(feed.url)) refreshAll({ quiet: true });
      else renderItems();
    });

    const kwInput = el("input", {
      type: "text",
      placeholder: "Add keyword, e.g. bushfire",
      "aria-label": `Add keyword for ${feed.title}`,
      autocapitalize: "off",
      enterkeyhint: "done",
    });
    const kwForm = el(
      "form",
      {
        class: "kw-form",
        onsubmit: (e) => {
          e.preventDefault();
          const words = kwInput.value.split(",").map((w) => w.trim()).filter(Boolean);
          let added = false;
          for (const w of words) {
            if (!feed.keywords.some((k) => k.toLowerCase() === w.toLowerCase())) {
              feed.keywords.push(w.slice(0, 60));
              added = true;
            }
          }
          if (added) {
            saveFeeds();
            renderMyFeeds();
            renderItems();
            $(`[data-feed-kw="${CSS.escape(feed.url)}"]`)?.focus();
          }
          kwInput.value = "";
        },
      },
      kwInput,
      el("button", { type: "submit", class: "secondary-btn", text: "Add" })
    );
    kwInput.setAttribute("data-feed-kw", feed.url);

    const chips = feed.keywords.map((k) =>
      el(
        "span",
        { class: "kw-chip" },
        k,
        el("button", {
          type: "button",
          "aria-label": `Remove keyword ${k}`,
          text: "×",
          onclick: () => {
            feed.keywords = feed.keywords.filter((x) => x !== k);
            saveFeeds();
            renderMyFeeds();
            renderItems();
          },
        })
      )
    );

    const error = state.results.get(feed.url)?.error;
    return el(
      "li",
      { class: `card feed-card${feed.enabled ? "" : " disabled"}` },
      el(
        "div",
        { class: "feed-head" },
        el("span", { class: "feed-name", text: feed.title }),
        el("label", { class: "switch" }, toggle, el("span", { class: "switch-track", "aria-hidden": "true" }))
      ),
      el("div", { class: "feed-url", text: feed.url }),
      error ? el("p", { class: "form-msg error", text: error }) : null,
      el("div", { class: "kw-editor" }, chips, kwForm),
      el(
        "div",
        { class: "feed-actions" },
        el("button", {
          type: "button",
          class: "text-btn danger",
          text: "Delete feed",
          onclick: () => {
            if (!window.confirm(`Remove "${feed.title}"?`)) return;
            removeFeed(feed.url);
            renderMyFeeds();
            renderItems();
            renderDiscover();
          },
        })
      )
    );
  }

  async function onAddFeed(e) {
    e.preventDefault();
    const input = $("#add-feed-url");
    const msg = $("#add-feed-msg");
    const btn = $("#add-feed-submit");
    const url = normaliseUrl(input.value);
    msg.className = "form-msg";
    if (!url) {
      msg.textContent = "Enter a web address starting with http or https.";
      msg.classList.add("error");
      return;
    }
    if (findFeed(url)) {
      msg.textContent = "That feed is already in your list.";
      return;
    }
    btn.disabled = true;
    msg.textContent = "Checking feed…";
    try {
      const data = await apiFeed(url);
      addFeed(url, data.title);
      state.results.set(url, { title: data.title, items: data.items || [], error: null });
      input.value = "";
      msg.textContent = `Added "${data.title}" with ${data.items.length} items.`;
      renderMyFeeds();
      renderItems();
      renderDiscover();
    } catch (err) {
      msg.textContent = `Could not add feed: ${err.message}`;
      msg.classList.add("error");
    } finally {
      btn.disabled = false;
    }
  }

  // ---------- Rendering: Discover ----------
  async function renderDiscover() {
    const data = await loadStarter();
    const wrap = $("#discover-list");
    wrap.replaceChildren(
      ...data.categories.map((cat) =>
        el(
          "section",
          { class: "category" },
          el("h2", { text: cat.name }),
          el(
            "ul",
            { class: "discover-grid" },
            cat.feeds.map((f) => {
              const added = !!findFeed(f.url);
              return el(
                "li",
                { class: "card discover-item" },
                el(
                  "div",
                  { class: "meta" },
                  el("div", { class: "name", text: f.name }),
                  el("div", { class: "host", text: hostOf(f.url) })
                ),
                el("button", {
                  type: "button",
                  class: added ? "secondary-btn" : "primary-btn",
                  "aria-label": added ? `${f.name} added` : `Add ${f.name}`,
                  disabled: added,
                  text: added ? "Added" : "Add",
                  onclick: () => {
                    addFeed(f.url, f.name);
                    toast(`Added ${f.name}`);
                    renderDiscover();
                    renderMyFeeds();
                    refreshAll({ quiet: true });
                  },
                })
              );
            })
          )
        )
      )
    );

    $("#template-list").replaceChildren(
      ...data.templates.map((t) =>
        el(
          "li",
          { class: "card" },
          el("strong", { class: "name", text: t.name }),
          el("code", { text: t.url }),
          t.help ? el("p", { text: t.help }) : null,
          el("button", {
            type: "button",
            class: "text-btn",
            text: "Use this pattern",
            onclick: () => {
              showView("feeds");
              const input = $("#add-feed-url");
              input.value = t.url;
              input.focus();
            },
          })
        )
      )
    );
  }

  // ---------- Views ----------
  function showView(name) {
    state.view = name;
    for (const section of document.querySelectorAll(".view")) {
      section.hidden = section.dataset.view !== name;
    }
    for (const tab of document.querySelectorAll(".tabbar [data-tab]")) {
      tab.setAttribute("aria-selected", String(tab.dataset.tab === name));
    }
    if (name === "feeds") renderMyFeeds();
    if (name === "discover") renderDiscover();
    if (name === "feed") renderItems();
    window.scrollTo(0, 0);
  }

  // ---------- Pull to refresh ----------
  function setupPullToRefresh() {
    const ptr = $("#ptr");
    const text = $(".ptr-text", ptr);
    const THRESHOLD = 70;
    let startY = null;
    let pull = 0;

    window.addEventListener(
      "touchstart",
      (e) => {
        if (state.view !== "feed" || window.scrollY > 0 || e.touches.length !== 1) return;
        if ($("#tutorial").hidden === false) return;
        startY = e.touches[0].clientY;
        pull = 0;
      },
      { passive: true }
    );
    window.addEventListener(
      "touchmove",
      (e) => {
        if (startY === null) return;
        pull = Math.max(0, e.touches[0].clientY - startY);
        if (pull > 0 && window.scrollY <= 0) {
          ptr.classList.add("dragging");
          ptr.style.height = `${Math.min(pull * 0.5, 64)}px`;
          text.textContent = pull > THRESHOLD ? "Release to refresh" : "Pull to refresh";
        }
      },
      { passive: true }
    );
    window.addEventListener("touchend", async () => {
      if (startY === null) return;
      startY = null;
      ptr.classList.remove("dragging");
      if (pull > THRESHOLD) {
        ptr.classList.add("loading");
        ptr.style.height = "44px";
        text.textContent = "Refreshing…";
        await refreshAll();
        ptr.classList.remove("loading");
      }
      ptr.style.height = "0";
      pull = 0;
    });
  }

  // ---------- Tutorial ----------
  const tutorial = {
    step: 0,
    selected: new Set(),
    preselected: false,
    lastFocus: null,
  };

  function detectPlatform() {
    const ua = navigator.userAgent || "";
    const standalone =
      window.matchMedia?.("(display-mode: standalone)").matches || window.navigator.standalone === true;
    if (standalone) return "installed";
    const isIOS = /iPhone|iPad|iPod/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1);
    if (isIOS) return "ios";
    if (/Android/.test(ua)) return "android";
    return "desktop";
  }

  let deferredInstall = null;
  window.addEventListener("beforeinstallprompt", (e) => {
    e.preventDefault();
    deferredInstall = e;
  });

  const tutorialSteps = [
    {
      title: "Welcome to Gleaner",
      body: () => [
        el("p", { text: "Gleaner collects updates from sites you care about into one list." }),
        el("p", { class: "hint", text: "Everything stays on this device. No account needed." }),
      ],
    },
    {
      title: "Pick a starter pack",
      body: () => {
        const box = el("div", { class: "chips", role: "group", "aria-label": "Starter feeds" });
        const fill = (data) => {
          const feeds = data.categories.flatMap((c) => c.feeds);
          if (!tutorial.preselected) {
            feeds.filter((f) => f.popular).slice(0, 3).forEach((f) => tutorial.selected.add(f.url));
            tutorial.preselected = true;
          }
          box.replaceChildren(
            ...feeds.map((f) => {
              const already = !!findFeed(f.url);
              const chip = el("button", {
                type: "button",
                class: "chip-btn",
                "aria-pressed": String(already || tutorial.selected.has(f.url)),
                disabled: already,
                text: f.name,
                "data-url": f.url,
              });
              chip.addEventListener("click", () => {
                if (tutorial.selected.has(f.url)) tutorial.selected.delete(f.url);
                else tutorial.selected.add(f.url);
                chip.setAttribute("aria-pressed", String(tutorial.selected.has(f.url)));
              });
              return chip;
            })
          );
        };
        if (state.starter) fill(state.starter);
        else {
          box.append(el("span", { class: "hint", text: "Loading…" }));
          loadStarter().then(fill);
        }
        return [el("p", { text: "Tap to choose. You can change these any time in Discover." }), box];
      },
      leave: () => {
        if (!state.starter) return;
        const feeds = state.starter.categories.flatMap((c) => c.feeds);
        let added = 0;
        for (const f of feeds) {
          if (tutorial.selected.has(f.url) && addFeed(f.url, f.name)) added++;
        }
        tutorial.selected.clear();
        if (added) {
          renderItems();
          refreshAll({ quiet: true });
        }
      },
    },
    {
      title: "Keywords",
      body: () => {
        const regex = keywordRegex(["bushfire"]);
        return [
          el("p", { text: "Add words like 'bushfire' or 'RTX' and matching items get highlighted." }),
          el(
            "div",
            { class: "item match example-item", "aria-label": "Example of a highlighted item" },
            el("div", { class: "item-meta" }, el("span", { class: "item-source", text: "ABC News" }), el("span", { text: "5m ago" })),
            el("span", { class: "item-title" }, highlight("Bushfire warning issued for parts of Gippsland", regex)),
            el("div", { class: "item-keywords" }, el("span", { class: "kw-badge", text: "bushfire" }))
          ),
          el("p", { class: "hint", text: "Set keywords for each feed in My Feeds." }),
        ];
      },
    },
    {
      title: "Add to home screen",
      body: () => {
        const platform = detectPlatform();
        if (platform === "installed") {
          return [el("p", { text: "You are already using Gleaner as an app. Enjoy!" })];
        }
        if (platform === "ios") {
          return [
            el("p", { text: "Open Gleaner like an app, straight from your home screen:" }),
            el(
              "ol",
              { class: "steps" },
              el("li", { text: "In Safari, tap the Share button (the square with an arrow)." }),
              el("li", { text: "Scroll down and tap “Add to Home Screen”." }),
              el("li", { text: "Tap “Add”." })
            ),
          ];
        }
        if (platform === "android") {
          const nodes = [
            el("p", { text: "Open Gleaner like an app, straight from your home screen:" }),
            el(
              "ol",
              { class: "steps" },
              el("li", { text: "In Chrome, tap the menu (three dots, top right)." }),
              el("li", { text: "Tap “Install app” or “Add to Home screen”." })
            ),
          ];
          if (deferredInstall) nodes.push(installButton());
          return nodes;
        }
        const mac = /Mac/.test(navigator.platform || navigator.userAgent);
        const nodes = [
          el("p", { text: `On a computer, bookmark this page so it is one click away (press ${mac ? "Cmd" : "Ctrl"}+D).` }),
          el("p", { class: "hint", text: "On your phone, open this page and use “Add to Home Screen”." }),
        ];
        if (deferredInstall) nodes.push(installButton());
        return nodes;
      },
    },
  ];

  function installButton() {
    return el("button", {
      type: "button",
      class: "secondary-btn",
      text: "Install Gleaner",
      onclick: async () => {
        if (!deferredInstall) return;
        deferredInstall.prompt();
        try {
          await deferredInstall.userChoice;
        } catch (e) {
          /* ignore */
        }
        deferredInstall = null;
      },
    });
  }

  function openTutorial() {
    tutorial.step = 0;
    tutorial.preselected = false;
    tutorial.selected.clear();
    tutorial.lastFocus = document.activeElement;
    $("#tutorial").hidden = false;
    document.body.style.overflow = "hidden";
    renderTutorial();
  }

  function closeTutorial() {
    $("#tutorial").hidden = true;
    document.body.style.overflow = "";
    store.set(KEYS.tutorialSeen, true);
    if (tutorial.lastFocus && tutorial.lastFocus.focus) tutorial.lastFocus.focus();
  }

  function renderTutorial() {
    const step = tutorialSteps[tutorial.step];
    const last = tutorial.step === tutorialSteps.length - 1;
    $("#tutorial-progress").textContent = `Step ${tutorial.step + 1} of ${tutorialSteps.length}`;
    $("#tutorial-title").textContent = step.title;
    $("#tutorial-body").replaceChildren(...step.body());
    $("#tutorial-back").style.visibility = tutorial.step === 0 ? "hidden" : "visible";
    $("#tutorial-next").textContent = last ? "Done" : tutorial.step === 1 ? "Add and continue" : "Next";
    $("#tutorial-skip").hidden = last;
    $("#tutorial-title").focus();
  }

  function tutorialNext() {
    const step = tutorialSteps[tutorial.step];
    if (step.leave) step.leave();
    if (tutorial.step >= tutorialSteps.length - 1) {
      closeTutorial();
      showView("feed");
      return;
    }
    tutorial.step++;
    renderTutorial();
  }

  function setupTutorial() {
    $("#tutorial-next").addEventListener("click", tutorialNext);
    $("#tutorial-back").addEventListener("click", () => {
      if (tutorial.step > 0) {
        tutorial.step--;
        renderTutorial();
      }
    });
    $("#tutorial-skip").addEventListener("click", closeTutorial);
    $("#help-btn").addEventListener("click", openTutorial);
    $("#tutorial").addEventListener("keydown", (e) => {
      if (e.key === "Escape") closeTutorial();
      if (e.key === "Tab") {
        // Keep focus inside the dialog.
        const focusables = Array.from(
          $("#tutorial").querySelectorAll("button:not([disabled]), [href], input, [tabindex]:not([tabindex='-1'])")
        ).filter((n) => n.offsetParent !== null);
        if (!focusables.length) return;
        const first = focusables[0];
        const lastEl = focusables[focusables.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          lastEl.focus();
        } else if (!e.shiftKey && document.activeElement === lastEl) {
          e.preventDefault();
          first.focus();
        }
      }
    });
  }

  // ---------- Init ----------
  function init() {
    for (const tab of document.querySelectorAll(".tabbar [data-tab]")) {
      tab.addEventListener("click", () => showView(tab.dataset.tab));
    }
    document.addEventListener("click", (e) => {
      const target = e.target.closest("[data-goto]");
      if (target) showView(target.dataset.goto);
    });

    const unread = $("#unread-only");
    unread.checked = state.unreadOnly;
    unread.addEventListener("change", () => {
      state.unreadOnly = unread.checked;
      store.set(KEYS.unreadOnly, state.unreadOnly);
      renderItems();
    });

    $("#mark-all-read").addEventListener("click", () => {
      for (const item of collectItems()) state.read.add(item.key);
      saveRead();
      renderItems();
      toast("Marked all as read");
    });

    $("#refresh-btn").addEventListener("click", () => refreshAll());
    $("#add-feed-form").addEventListener("submit", onAddFeed);

    setupPullToRefresh();
    setupTutorial();
    showView("feed");
    loadStarter();

    if (!store.get(KEYS.tutorialSeen, false)) openTutorial();
    refreshAll();

    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible" && state.feeds.length) refreshAll({ quiet: true });
    });

    if ("serviceWorker" in navigator) {
      window.addEventListener("load", () => {
        navigator.serviceWorker.register("/sw.js").catch(() => {});
      });
    }
  }

  init();
})();
