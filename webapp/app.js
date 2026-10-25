/* Squad Radar web app. Vanilla JS, no build step. Talks to web.py's JSON API and polls for new messages. */
(function () {
  "use strict";

  // ---------- tiny helpers ----------
  const $app = document.getElementById("app");
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode */ } },
    del(k) { try { localStorage.removeItem(k); } catch (e) { /* private mode */ } },
  };
  function h(tag, attrs, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") el.className = v;
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else if (k === "html") el.innerHTML = v;
      else el.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids.flat(Infinity)) {
      if (kid === null || kid === undefined || kid === false) continue;
      el.appendChild(typeof kid === "string" || typeof kid === "number" ? document.createTextNode(String(kid)) : kid);
    }
    return el;
  }
  const svg = (inner, vb = "0 0 24 24") => {
    const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    s.setAttribute("viewBox", vb); s.setAttribute("aria-hidden", "true"); s.innerHTML = inner; return s;
  };
  const MARK = '<rect width="32" height="32" rx="7" fill="currentColor"/><path d="M9 22a9 9 0 0 1 14 0" stroke="var(--mark-ink,#fff)" stroke-width="2.4" fill="none" stroke-linecap="round"/><path d="M12.5 18.6a4.6 4.6 0 0 1 7 0" stroke="var(--mark-ink,#fff)" stroke-width="2.4" fill="none" stroke-linecap="round"/><circle cx="16" cy="22" r="1.8" fill="var(--mark-ink,#fff)"/>';
  const BOT_ICON = '<path d="M5 17a9 9 0 0 1 14 0" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round"/><path d="M8.5 13.8a4.6 4.6 0 0 1 7 0" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round"/><circle cx="12" cy="17" r="1.6" fill="currentColor"/>';
  const ICON_CAL = '<rect x="3.5" y="5" width="17" height="15" rx="2.5" stroke="currentColor" stroke-width="1.8" fill="none"/><path d="M3.5 10h17M8 3v4M16 3v4" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>';
  const ICON_PLAY = '<path d="M8 5.5v13l11-6.5z" fill="currentColor"/>';
  const ICON_STOP = '<rect x="7" y="7" width="10" height="10" rx="1.5" fill="currentColor"/>';
  const ICON_MENU = '<path d="M4 7h16M4 12h16M4 17h16" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>';
  const wordmark = () => h("div", { class: "wordmark" }, (() => { const s = svg(MARK, "0 0 32 32"); s.style.color = "#274B7A"; return s; })(), "Squad Radar");

  const PALETTE = ["#4C6A92", "#5B7F6B", "#8A6D4C", "#7A5C7E", "#5E7381", "#8B5E5E", "#4F7C7A", "#6B6F95"];
  function avatar(name, small) {
    let n = 0; for (const c of name || "?") n = (n * 31 + c.charCodeAt(0)) >>> 0;
    const initials = (name || "?").split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase();
    return h("div", { class: "avatar" + (small ? " small" : ""), style: "background:" + PALETTE[n % PALETTE.length], "aria-hidden": "true" }, initials);
  }
  const botAvatar = () => { const a = h("div", { class: "avatar bot", "aria-hidden": "true" }); a.appendChild(svg(BOT_ICON)); return a; };

  const fmtTime = (iso) => new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }).toLowerCase().replace(" ", "");
  const sameDay = (a, b) => a.toDateString() === b.toDateString();
  function dayLabel(iso) {
    const d = new Date(iso), now = new Date(), y = new Date(now); y.setDate(now.getDate() - 1);
    if (sameDay(d, now)) return "Today";
    if (sameDay(d, y)) return "Yesterday";
    return d.toLocaleDateString([], { weekday: "long", month: "short", day: "numeric" });
  }
  const money = (c) => "$" + (c / 100).toFixed(2);
  const firstName = (n) => (n || "").split(/\s+/)[0];
  function toast(text) {
    const t = h("div", { class: "toast", role: "status" }, text);
    document.body.appendChild(t);
    setTimeout(() => t.remove(), 2600);
  }

  // ---------- api ----------
  const S = { token: store.get("squad.token"), me: null, rooms: [], code: null, room: null, msgs: [], lastId: 0, board: null,
              boardKey: "", llm: null, persona: "", view: "chat", tr: {}, lang: store.get("squad.lang") || "spanish", inboxSeen: +(store.get("squad.inboxSeen") || 0), inboxLast: 0, timer: null };

  async function api(method, path, body) {
    const res = await fetch(path, { method, headers: { "Content-Type": "application/json", "X-Squad-Token": S.token || "" },
                                    body: body ? JSON.stringify(body) : undefined });
    let data = {};
    try { data = await res.json(); } catch (e) { /* empty */ }
    if (!res.ok) { const err = new Error(data.error || "The server didn't answer. Check your connection and try again."); err.status = res.status; throw err; }
    return data;
  }

  // ---------- routing ----------
  function codeFromPath() { const m = location.pathname.match(/^\/r\/([a-z0-9]{3,32})/i); return m ? m[1].toLowerCase() : null; }

  async function boot() {
    const lm = location.pathname.match(/^\/l\/([\w-]+)/);
    if (lm) {  // a private sign-in link from Discord ("@squad board")
      history.replaceState(null, "", "/");
      try {
        const r = await api("POST", "/api/link", { code: lm[1] });
        S.token = r.token; store.set("squad.token", r.token);
        await refreshMe();
        return openRoom(r.room || (S.rooms[0] && S.rooms[0].code));
      } catch (e) { if (!S.token) return welcome(null, e.message); toast(e.message); }
    }
    const invited = codeFromPath();
    if (!S.token) return welcome(invited);
    try {
      const me = await api("GET", "/api/me");
      Object.assign(S, { me: me.me, rooms: me.rooms, llm: me.llm });
    } catch (e) {
      if (e.status === 401) { store.del("squad.token"); S.token = null; return welcome(invited); }
      return welcome(invited, e.message);
    }
    if (invited) {
      try { await api("POST", "/api/rooms/" + invited + "/join"); await refreshMe(); return openRoom(invited); }
      catch (e) { toast(e.message); }
    }
    if (S.rooms.length) return openRoom(S.rooms[0].code);
    return welcome(null, null, true);
  }
  async function refreshMe() { const me = await api("GET", "/api/me"); Object.assign(S, { me: me.me, rooms: me.rooms, llm: me.llm }); return me; }

  // ---------- welcome ----------
  async function welcome(invited, error, named) {
    stopPolling();
    let inviteName = null;
    if (invited) { try { inviteName = (await api("GET", "/api/rooms/" + invited)).name; } catch (e) { invited = null; } }
    const sample = [
      ["Jake", "anyone free this weekend??"],
      ["Squad", "Checked everyone's classes. 1. Sat 7pm, movie night on Hayden Lawn (free pizza)  2. Sun 5pm, boba on Palm Walk"],
      ["Priya", "1"],
      ["Squad", "Locked. Calendar invite sent, reminder at 5pm."],
    ];
    const intro = h("section", { class: "welcome-intro" },
      wordmark(),
      h("div", {},
        h("h1", {}, "Your group chat, with the busywork handled."),
        h("p", { class: "lede" }, "Squad Radar sits in the chat. It finds times that fit everyone's classes, splits the bill to the cent, runs move-out sales and tells you which of the 200 messages you missed actually matter."),
        h("div", { class: "welcome-sample", "aria-label": "Example conversation" },
          sample.map(([w, t]) => h("div", { class: "row" }, h("b", { class: w === "Squad" ? "bot" : "" }, w), h("span", {}, t))))),
      h("p", { class: "foot" }, "Built for the Cubbie Hackathon, group chat assistant track."));
    const right = h("section", { class: "welcome-form" });
    const page = h("main", { class: "welcome" }, intro, right);
    $app.replaceChildren(page);

    if (!S.token || !named) {
      if (S.token && S.me) return choices(right, error);
      const err = h("div", { class: "error", role: "alert" }, error || "");
      const input = h("input", { class: "field", id: "name", name: "name", autocomplete: "given-name", required: true, maxlength: "24", placeholder: "First name" });
      const btn = h("button", { class: "btn primary", type: "submit" }, inviteName ? "Join " + inviteName : "Continue");
      const form = h("form", { onsubmit: async (e) => {
        e.preventDefault(); btn.disabled = true; err.textContent = "";
        try {
          const r = await api("POST", "/api/join", { name: input.value });
          S.token = r.token; store.set("squad.token", r.token); S.me = r.me;
          if (invited) { await api("POST", "/api/rooms/" + invited + "/join"); await refreshMe(); return openRoom(invited); }
          await refreshMe(); choices(right);
        } catch (x) { err.textContent = x.message; btn.disabled = false; }
      } },
        h("h2", {}, inviteName ? "You're invited to " + inviteName : "What should your friends call you?"),
        h("p", { class: "sub" }, inviteName ? "Enter your first name to join the chat." : "Your name is the only thing you need. No email, no password."),
        h("div", { class: "stack" }, h("div", {}, h("label", { class: "lab", for: "name" }, "First name"), input), btn),
        err);
      right.replaceChildren(form);
      input.focus();
      return;
    }
    choices(right, error);
  }

  function choices(right, error) {
    const err = h("div", { class: "error", role: "alert" }, error || "");
    const squadName = h("input", { class: "field", placeholder: "Squad name, like Apartment 4B", maxlength: "40", "aria-label": "Squad name" });
    const code = h("input", { class: "field", placeholder: "Invite code", maxlength: "12", "aria-label": "Invite code" });
    const go = async (fn) => { err.textContent = ""; try { await fn(); } catch (x) { err.textContent = x.message; } };
    right.replaceChildren(h("div", { class: "choices" },
      h("h2", {}, "Hi " + firstName(S.me.name) + ". Where to?"),
      h("p", { class: "sub" }, "Try it with sample friends first, or start a real squad and send the link around."),
      h("div", { class: "stack" },
        S.rooms.some((r) => r.demo) ? h("button", { class: "choice", style: "border-color: var(--accent)", onclick: () => openRoom(S.rooms.find((r) => r.demo).code) },
          h("strong", {}, "Open the Discord demo"),
          h("span", {}, S.rooms.filter((r) => r.demo).length + " channels synced live with a Discord server. Watch the bot work, then try the assistant.")) : null,
        h("button", { class: "choice", onclick: () => go(async () => { const r = await api("POST", "/api/rooms/sample"); await refreshMe(); openRoom(r.code); }) },
          h("strong", {}, "Open the sample squad"),
          h("span", {}, "Priya, Jake and Sam are already mid-conversation. You can type as any of them.")),
        h("div", { class: "choice", style: "cursor:default" },
          h("strong", {}, "Start a squad"),
          h("span", {}, "You'll get a link to send to your friends."),
          h("form", { class: "inline", style: "margin-top:10px", onsubmit: (e) => { e.preventDefault(); go(async () => {
            const r = await api("POST", "/api/rooms", { name: squadName.value }); await refreshMe(); openRoom(r.code); }); } },
            squadName, h("button", { class: "btn primary", type: "submit" }, "Start"))),
        h("div", { class: "choice", style: "cursor:default" },
          h("strong", {}, "Join with a code"),
          h("form", { class: "inline", style: "margin-top:10px", onsubmit: (e) => { e.preventDefault(); go(async () => {
            const c = code.value.trim().toLowerCase().replace(/.*\/r\//, "");
            await api("POST", "/api/rooms/" + c + "/join"); await refreshMe(); openRoom(c); }); } },
            code, h("button", { class: "btn", type: "submit" }, "Join")))),
      err));
  }

  // ---------- app shell ----------
  let els = {};
  function shell() {
    if (els.shell && document.body.contains(els.shell)) return;
    els = {};
    els.rail = h("nav", { class: "rail", "aria-label": "Squads" });
    els.chatHead = h("header", { class: "chat-head" });
    els.stream = h("div", { class: "stream", role: "log", "aria-label": "Messages" });
    els.composer = composer();
    els.chatWrap = h("div", { class: "chat-wrap" }, els.stream, els.composer);
    els.chat = h("section", { class: "chat", "aria-label": "Chat" }, els.chatHead, els.chatWrap);
    els.assist = h("div");
    els.boardBody = h("div");
    els.board = h("aside", { class: "board", "aria-label": "Board" }, els.assist, els.boardBody);
    els.mobileTitle = h("div", { class: "t" });
    const menuBtn = h("button", { class: "btn quiet small", "aria-label": "Squads", onclick: () => els.shell.classList.toggle("rail-open") });
    menuBtn.appendChild(svg(ICON_MENU)); menuBtn.firstChild.style.width = "20px";
    els.youBadge = h("span", { class: "status due", hidden: true }, "new");
    const mobileBar = h("div", { class: "mobile-bar" }, menuBtn, els.mobileTitle);
    els.seg = h("div", { class: "seg", role: "group", "aria-label": "View" },
      ["chat", "board", "you"].map((v) => h("button", { "data-v": v, "aria-pressed": String(v === S.view), onclick: () => setView(v) },
        v === "chat" ? "Chat" : v === "board" ? "Board" : "You")));
    els.shell = h("div", { class: "shell", "data-view": S.view }, els.rail, mobileBar, els.seg, els.chat, els.board);
    els.shell.addEventListener("click", (e) => {
      if (els.shell.classList.contains("rail-open") && !els.rail.contains(e.target) && !e.target.closest(".mobile-bar")) els.shell.classList.remove("rail-open");
    });
    $app.replaceChildren(els.shell);
  }
  function setView(v) {
    if (v === "you") return openYou();
    S.view = v; els.shell.dataset.view = v;
    els.seg.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.v === v)));
    if (v === "chat") scrollDown(true);
  }

  function renderRail() {
    const roomList = S.rooms.map((r) => h("button", { class: "squad-link", "aria-current": String(r.code === S.code), onclick: () => { els.shell.classList.remove("rail-open"); openRoom(r.code); } },
      h("span", { class: "n" }, r.name),
      r.demo ? h("span", { class: "p" }, "Discord demo, open to everyone") : null,
      r.demo ? null : h("span", { class: "p" }, r.last ? (r.last.who ? r.last.who + ": " : "") + (r.last.text || "Voice note") : "No messages yet")));
    const hasSample = S.rooms.some((r) => r.sample);
    els.rail.replaceChildren(
      h("div", { class: "rail-head" }, wordmark()),
      h("div", { class: "rail-section" }, h("div", { class: "rail-title" }, "Your squads"), roomList),
      h("div", { class: "rail-actions" },
        h("button", { class: "btn quiet small", onclick: newSquad }, "Start a squad"),
        h("button", { class: "btn quiet small", onclick: joinSquad }, "Join with a code"),
        hasSample ? null : h("button", { class: "btn quiet small", onclick: async () => { const r = await api("POST", "/api/rooms/sample"); await refreshMe(); openRoom(r.code); } }, "Open the sample squad")),
      h("div", { class: "rail-foot" },
        h("button", { class: "me-btn", onclick: openYou }, avatar(S.me.name, true),
          h("span", {}, h("span", { class: "t" }, S.me.name, " "), els.youBadge, h("br"), h("span", { class: "s" }, "Your schedule and private messages"))),
        modelStatus()));
    updateBadge();
  }
  function modelStatus() {
    const m = S.llm || {};
    let dot = "off", title, detail;
    if (!m.enabled) { title = "Language model off"; detail = "Set XAI_API_KEY on the server to turn on summaries, translation and free-form answers. Everything else works without it."; }
    else if (m.ok) { dot = "ok"; title = (m.provider || "Model") + " connected"; detail = (m.model || "") + ", " + m.ok + (m.ok === 1 ? " answer" : " answers") + " since the server started"; }
    else if (m.last_error) { title = "Can't reach " + (m.provider || "the model"); detail = String(m.last_error).slice(0, 110); }
    else { dot = "wait"; title = (m.provider || "Model") + " ready"; detail = (m.model || "Choosing a model") + ". Ask @squad anything to use it."; }
    return h("div", { class: "model", title: "The language model behind @squad" }, h("div", { class: "state" }, h("span", { class: "dot " + dot }), title), detail);
  }
  function updateBadge() { if (els.youBadge) els.youBadge.hidden = !(S.inboxLast > S.inboxSeen); }

  async function newSquad() {
    const name = prompt("Name your squad", "");
    if (name === null) return;
    try { const r = await api("POST", "/api/rooms", { name }); await refreshMe(); openRoom(r.code); } catch (e) { toast(e.message); }
  }
  async function joinSquad() {
    const c = prompt("Paste the invite link or code", "");
    if (!c) return;
    const code = c.trim().toLowerCase().replace(/.*\/r\//, "").replace(/[^a-z0-9]/g, "");
    try { await api("POST", "/api/rooms/" + code + "/join"); await refreshMe(); openRoom(code); } catch (e) { toast(e.message); }
  }

  // ---------- open a squad ----------
  async function openRoom(code) {
    shell();
    Object.assign(S, { code, msgs: [], lastId: 0, board: null, boardKey: "", persona: "", tr: {} });
    buildAssistant();
    if (location.pathname !== "/r/" + code) history.replaceState(null, "", "/r/" + code);
    els.stream.replaceChildren(h("div", { class: "empty-chat" }, "Loading the chat..."));
    renderRail();
    await poll(true);
    startPolling();
    if (window.matchMedia("(min-width: 961px)").matches) els.input.focus();
  }
  function startPolling() { stopPolling(); S.timer = setInterval(() => poll(false), 2000); }
  function stopPolling() { if (S.timer) clearInterval(S.timer); S.timer = null; }

  async function poll(first) {
    if (!S.code) return;
    let f;
    try { f = await api("GET", "/api/rooms/" + S.code + "/feed?after=" + S.lastId); }
    catch (e) {
      if (e.status === 401) { store.del("squad.token"); S.token = null; return welcome(null, e.message); }
      if (first) { toast(e.message); if (e.status === 403 || e.status === 404) { await refreshMe(); return S.rooms[0] ? openRoom(S.rooms[0].code) : welcome(null, null, true); } }
      return;
    }
    const prevName = S.room && S.room.name;
    S.room = f.room; S.inboxLast = f.inbox_last; updateBadge();
    const llmChanged = JSON.stringify(f.llm) !== JSON.stringify(S.llm);
    S.llm = f.llm;
    if (first || prevName !== f.room.name) renderHead();
    if (llmChanged && !first) renderRail();
    if (f.messages.length) addMessages(f.messages, first);
    else if (first) renderStream();
    setThinking(f.room.thinking || S.sending);
    const key = JSON.stringify(f.board);
    if (key !== S.boardKey) { S.boardKey = key; S.board = f.board; renderBoard(); }
  }

  function renderHead() {
    const r = S.room;
    els.mobileTitle.textContent = r.name;
    const names = r.members.map((m) => (m.you ? "You" : m.name));
    let right = null;
    if (r.sample) {
      const sel = h("select", { "aria-label": "Type as", onchange: (e) => { S.persona = e.target.value; els.input.placeholder = placeholder(); } },
        h("option", { value: "" }, firstName(S.me.name) + " (you)"),
        r.members.filter((m) => m.sample).map((m) => h("option", { value: m.name }, m.name)));
      sel.value = S.persona;
      right = h("label", { class: "persona" }, "Type as", sel);
    } else if (r.source === "discord") {
      right = h("span", { class: "status plain", title: "Messages here and in the Discord channel stay in sync" }, "Synced with Discord");
    } else {
      right = h("button", { class: "btn small", onclick: async () => {
        const link = location.origin + "/r/" + r.code;
        try { await navigator.clipboard.writeText(link); toast("Invite link copied"); } catch (e) { prompt("Copy this invite link", link); }
      } }, "Copy invite link");
    }
    els.chatHead.replaceChildren(h("div", { class: "grow" }, h("h2", {}, r.name), h("div", { class: "members" }, names.join(", "))), right);
    els.input.placeholder = placeholder();
  }
  const placeholder = () => window.matchMedia("(max-width: 960px)").matches
    ? (S.persona ? "Message as " + S.persona : "Message, or @squad ...")
    : "Message " + (S.room ? S.room.name : "") + (S.persona ? " as " + S.persona : "") + ". Type @squad to ask the bot.";

  // ---------- messages ----------
  function addMessages(list, first) {
    const nearBottom = els.stream.scrollHeight - els.stream.scrollTop - els.stream.clientHeight < 140;
    for (const m of list) {
      const i = S.msgs.findIndex((x) => x.id === m.id);
      if (i >= 0) S.msgs[i] = m; else S.msgs.push(m);
      S.lastId = Math.max(S.lastId, m.id);
    }
    S.msgs.sort((a, b) => a.id - b.id);
    renderStream();
    scrollDown(first || nearBottom);
    const r = S.rooms.find((x) => x.code === S.code);
    const last = S.msgs[S.msgs.length - 1];
    if (r && last) { r.last = { who: last.who, text: last.text, at: last.at }; }
  }
  function setThinking(on) {
    let row = els.stream.querySelector(".thinking");
    if (on && !row) {
      row = h("div", { class: "thinking", role: "status" }, botAvatar(), h("span", {}, "Squad Radar is thinking", h("span", { class: "dots", "aria-hidden": "true" }, h("i"), h("i"), h("i"))));
      const near = els.stream.scrollHeight - els.stream.scrollTop - els.stream.clientHeight < 140;
      els.stream.appendChild(row);
      scrollDown(near);
    } else if (!on && row) row.remove();
  }
  function scrollDown(force) { if (force) requestAnimationFrame(() => { els.stream.scrollTop = els.stream.scrollHeight; }); }

  function myNames() {
    const n = S.persona || S.me.name;
    return [n, firstName(n)].filter((x, i, a) => x && a.indexOf(x) === i);
  }
  function renderStream() {
    if (!S.msgs.length) { els.stream.replaceChildren(h("div", { class: "empty-chat" }, "Say hi. Type @squad help to see what the bot can do.")); return; }
    const frag = document.createDocumentFragment();
    let prev = null;
    for (const m of S.msgs) {
      if (!prev || !sameDay(new Date(prev.at), new Date(m.at))) frag.appendChild(h("div", { class: "day" }, dayLabel(m.at)));
      if (m.kind === "event") { frag.appendChild(h("div", { class: "event" }, m.text)); prev = m; continue; }
      const cont = prev && prev.kind === m.kind && prev.who === m.who && new Date(m.at) - new Date(prev.at) < 5 * 60000 && !m.voice && !m.file && prev.kind !== "event";
      frag.appendChild(messageEl(m, cont));
      prev = m;
    }
    els.stream.replaceChildren(frag);
    if (S.thinkingShown) setThinking(true);
  }

  const MENTION_RE = /(https?:\/\/[^\s]+)|(@[A-Za-z][\w'-]*(?:\s\d+)?)/g;
  function richText(text) {
    const out = [];
    let last = 0, m;
    const mine = myNames().map((n) => n.toLowerCase());
    MENTION_RE.lastIndex = 0;
    while ((m = MENTION_RE.exec(text))) {
      if (m.index > last) out.push(text.slice(last, m.index));
      if (m[1]) out.push(h("a", { href: m[1], target: "_blank", rel: "noopener noreferrer" }, m[1]));
      else {
        const who = m[2].slice(1).toLowerCase();
        out.push(h("span", { class: "at" + (mine.includes(who) || ["everyone", "all", "here"].includes(who) ? " you" : "") }, m[2]));
      }
      last = m.index + m[0].length;
    }
    if (last < text.length) out.push(text.slice(last));
    return out;
  }
  function tagsMe(m) {
    if (m.kind !== "user" || myNames().includes(m.who)) return false;
    return myNames().some((n) => new RegExp("(^|[^\\w])@?" + n.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "\\b", "i").test(m.text || ""));
  }

  const REACTS = [["love", "❤️", "Love"], ["like", "👍", "Like"], ["laugh", "😂", "Laugh"], ["emphasize", "‼️", "Emphasize, which also pins it"], ["question", "❓", "Question"]];
  function messageEl(m, cont) {
    const isBot = m.kind === "bot";
    const meName = S.persona || S.me.name;
    const reacts = Object.entries(m.reactions || {});
    const grouped = {};
    for (const [who, k] of reacts) (grouped[k] = grouped[k] || []).push(who);
    const reactRow = reacts.length ? h("div", { class: "reacts" }, Object.entries(grouped).map(([k, who]) => {
      const r = REACTS.find((x) => x[0] === k) || [k, "✨", k];
      return h("button", { class: "react-chip" + (who.includes(meName) ? " mine" : ""), title: who.join(", "), "aria-label": r[2] + " from " + who.join(", "),
                           onclick: () => react(m, k) }, r[1], h("span", { class: "num" }, who.length));
    })) : null;
    const tools = h("div", { class: "tools", role: "toolbar", "aria-label": "Message actions" },
      REACTS.map(([k, e, label]) => h("button", { "aria-label": label, title: label, onclick: () => react(m, k) }, e)),
      m.text ? h("span", { class: "tool-sep", "aria-hidden": "true" }) : null,
      m.text ? h("button", { class: "word", title: "Translate into " + cap(S.lang), onclick: () => translateMsg(m) }, "Translate") : null,
      m.text ? h("button", { class: "word", title: "Read it out loud", onclick: () => speak(m.text, "english") }, "Listen") : null);
    const meta = cont ? null : h("div", { class: "meta" },
      h("span", { class: "who" }, isBot ? "Squad Radar" : m.who),
      h("span", { class: "time" }, fmtTime(m.at)),
      isBot && m.ai ? h("span", { class: "model-tag", title: "This reply was written by the language model" }, "Written by " + m.ai) : null);
    const attachments = [];
    if (m.file && m.file.kind === "invite") {
      const ic = h("div", { class: "icon" }); ic.appendChild(svg(ICON_CAL));
      attachments.push(h("div", { class: "attach" }, ic, h("div", { class: "grow" }, h("div", { class: "t" }, "Calendar invite"), h("div", { class: "s" }, "Opens in Apple Calendar, Google Calendar or Outlook")),
        h("a", { class: "btn small", href: m.file.url, download: m.file.name }, "Add to calendar")));
    }
    if (m.voice) attachments.push(voiceEl(m.voice));
    if (S.tr[m.id]) attachments.push(h("div", { class: "translation" }, h("div", { class: "lbl" }, cap(S.tr[m.id].lang)), S.tr[m.id].text,
      h("button", { class: "linkish", onclick: () => speak(S.tr[m.id].text, S.tr[m.id].lang) }, "Listen")));
    return h("article", { class: "msg" + (isBot ? " bot" : "") + (cont ? " cont" : "") + (tagsMe(m) ? " tagged" : ""), "data-id": m.id },
      isBot ? botAvatar() : avatar(m.who),
      h("div", {}, meta, m.text ? h("div", { class: "body" }, richText(m.text)) : null, attachments, reactRow),
      tools);
  }

  // ---------- voice notes (read out by the device's own voice) ----------
  const LANG = { english: "en-US", spanish: "es-ES", hindi: "hi-IN", tamil: "ta-IN", telugu: "te-IN", french: "fr-FR", german: "de-DE",
                 mandarin: "zh-CN", chinese: "zh-CN", japanese: "ja-JP", korean: "ko-KR", portuguese: "pt-BR", arabic: "ar-SA", italian: "it-IT",
                 russian: "ru-RU", vietnamese: "vi-VN", kannada: "kn-IN", malayalam: "ml-IN", marathi: "mr-IN", bengali: "bn-IN", gujarati: "gu-IN", urdu: "ur-PK" };
  let speaking = null;
  function voiceEl(v) {
    const secs = Math.max(3, Math.round(v.text.split(/\s+/).length / 2.6));
    const ic = h("div", { class: "icon" }); ic.appendChild(svg(ICON_PLAY));
    const label = h("span", {}, "Play");
    const btn = h("button", { class: "btn small", onclick: () => {
      if (!("speechSynthesis" in window)) return toast("This browser can't play voice notes.");
      if (speaking === btn) { speechSynthesis.cancel(); return; }
      speechSynthesis.cancel();
      const u = new SpeechSynthesisUtterance(v.text);
      const tag = LANG[(v.lang || "english").toLowerCase()] || "en-US";
      u.lang = tag; u.rate = 1.02;
      const voices = speechSynthesis.getVoices().filter((x) => x.lang.replace("_", "-").startsWith(tag.slice(0, 2)));
      const nice = voices.find((x) => /premium|enhanced|natural|samantha|ava|google us/i.test(x.name)) || voices[0];
      if (nice) u.voice = nice;
      u.onend = u.onerror = () => { speaking = null; label.textContent = "Play"; ic.replaceChildren(svg(ICON_PLAY)); };
      speaking = btn; label.textContent = "Stop"; ic.replaceChildren(svg(ICON_STOP));
      speechSynthesis.speak(u);
    } }, label);
    const lang = (v.lang || "english");
    return h("div", { class: "attach" }, ic, h("div", { class: "grow" }, h("div", { class: "t" }, "Voice note"),
      h("div", { class: "s" }, "About " + secs + " seconds" + (lang !== "english" ? ", in " + lang[0].toUpperCase() + lang.slice(1) : ""))), btn);
  }

  // ---------- listening and translating ----------
  const cap = (x) => (x || "")[0] ? x[0].toUpperCase() + x.slice(1) : x;
  function speak(text, lang) {
    if (!("speechSynthesis" in window)) return toast("This browser can't read text out loud.");
    speechSynthesis.cancel();
    const clean = String(text).replace(/https?:\/\/\S+/g, "a link").replace(/[\u{1F000}-\u{1FAFF}\u{2300}-\u{27BF}\u{2B00}-\u{2BFF}\uFE0F]/gu, "")
      .replace(/#(\d+)/g, "number $1").replace(/@squad/g, "squad");
    const u = new SpeechSynthesisUtterance(clean);
    const tag = LANG[(lang || "english").toLowerCase()] || "en-US";
    u.lang = tag;
    const voices = speechSynthesis.getVoices().filter((x) => x.lang.replace("_", "-").startsWith(tag.slice(0, 2)));
    const nice = voices.find((x) => /premium|enhanced|natural|samantha|ava|google/i.test(x.name)) || voices[0];
    if (nice) u.voice = nice;
    speechSynthesis.speak(u);
  }
  async function assist(tool, extra) {
    return api("POST", "/api/rooms/" + S.code + "/assist", Object.assign({ tool }, extra || {}));
  }
  async function translateMsg(m) {
    toast("Translating into " + cap(S.lang) + "...");
    try { const r = await assist("translate", { text: m.text, lang: S.lang }); S.tr[m.id] = { text: r.text, lang: S.lang }; renderStream(); }
    catch (e) { toast(e.message); }
  }
  const LANG_CHOICES = ["english", "spanish", "hindi", "tamil", "telugu", "mandarin", "korean", "japanese", "vietnamese", "arabic", "french", "portuguese"];
  function langSelect(onchange) {
    const sel = h("select", { class: "lang", "aria-label": "Language", onchange: (e) => { S.lang = e.target.value; store.set("squad.lang", S.lang); if (onchange) onchange(); } },
      LANG_CHOICES.map((l) => h("option", { value: l }, cap(l))));
    sel.value = S.lang;
    return sel;
  }
  function buildAssistant() {
    if (!els.assist) return;
    const out = h("div", { class: "assist-out", "aria-live": "polite" });
    let current = null;
    const show = (r, label) => {
      current = r;
      out.replaceChildren(h("div", { class: "assist-result" },
        h("div", { class: "assist-label" }, label, r.ai ? h("span", { class: "model-tag" }, "Written by " + r.ai) : null),
        h("div", { class: "assist-text" }, r.text || "Nothing to show yet."),
        h("div", { class: "assist-actions" },
          h("button", { class: "btn small", onclick: () => speak(current.speech || current.text, current.lang || "english") }, "Listen"),
          langSelect(),
          h("button", { class: "btn small", onclick: async () => {
            try { const t = await assist("translate", { text: current.text, lang: S.lang }); show(Object.assign({}, t, { lang: S.lang, speech: t.text }), label + " in " + cap(S.lang)); }
            catch (e) { toast(e.message); }
          } }, "Translate"))));
    };
    const run = (tool, label) => async (e) => {
      const b = e.currentTarget; b.disabled = true;
      out.replaceChildren(h("div", { class: "thinking" }, botAvatar(), h("span", {}, "Working on it", h("span", { class: "dots" }, h("i"), h("i"), h("i")))));
      try { show(await assist(tool), label); } catch (x) { out.replaceChildren(h("p", { class: "error" }, x.message)); }
      finally { b.disabled = false; }
    };
    const what = h("input", { class: "field small", placeholder: "Remind the squad about...", maxlength: "200", "aria-label": "Reminder" });
    const at = h("input", { class: "field small", type: "datetime-local", "aria-label": "When" });
    const d = new Date(Date.now() + 3600000); d.setMinutes(0, 0, 0);
    at.value = new Date(d - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
    const remindForm = h("form", { class: "remind", onsubmit: async (e) => {
      e.preventDefault();
      try { const r = await assist("remind", { text: what.value, at: at.value }); what.value = ""; toast(r.text); poll(false); }
      catch (x) { toast(x.message); }
    } }, what, h("div", { class: "inline" }, at, h("button", { class: "btn small", type: "submit" }, "Set reminder")));
    els.assist.replaceChildren(h("section", { class: "sec assistant" },
      h("h3", {}, "Assistant", h("span", { class: "c" }, "only you see this")),
      h("div", { class: "assist-buttons" },
        h("button", { class: "btn small primary", onclick: run("summary", "Summary") }, "Summarize"),
        h("button", { class: "btn small", onclick: run("highlights", "What matters") }, "What matters"),
        h("button", { class: "btn small", onclick: run("mentions", "Your mentions") }, "My mentions")),
      out, remindForm));
  }

  // ---------- sending ----------
  function composer() {
    const ta = h("textarea", { rows: "1", "aria-label": "Message", maxlength: "1500" });
    els.input = ta;
    const send = h("button", { class: "btn primary", type: "submit" }, "Send");
    const cmdBtn = h("button", { class: "btn", type: "button", "aria-expanded": "false", "aria-label": "Show commands", onclick: () => toggleCommands(cmdBtn) }, "Commands");
    ta.addEventListener("input", () => { ta.style.height = "auto"; ta.style.height = Math.min(ta.scrollHeight, 160) + "px"; });
    ta.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); form.requestSubmit(); } });
    const form = h("form", { onsubmit: async (e) => {
      e.preventDefault();
      const text = ta.value.trim();
      if (!text) return;
      send.disabled = true;
      try { await sendText(text); ta.value = ""; ta.style.height = "auto"; }
      catch (x) { toast(x.message); }
      finally { send.disabled = false; ta.focus(); }
    } }, cmdBtn, ta, send);
    return h("div", { class: "composer" }, form, h("div", { class: "note" }, "Enter to send, Shift+Enter for a new line"));
  }
  async function sendText(text) {
    const asksBot = /^\s*(@?squad\b|hey squad)/i.test(text);
    S.sending = asksBot;
    if (asksBot) { S.thinkingShown = true; setThinking(true); }
    try {
      const r = await api("POST", "/api/rooms/" + S.code + "/messages", { text, as: S.persona || undefined });
      S.sending = false; S.thinkingShown = false; setThinking(false);
      addMessages(r.messages, true);
    } finally { S.sending = false; S.thinkingShown = false; setThinking(false); }
    poll(false);
  }
  async function react(m, kind) {
    try { const r = await api("POST", "/api/rooms/" + S.code + "/react", { id: m.id, kind, as: S.persona || undefined }); addMessages([r.message], false); poll(false); }
    catch (e) { toast(e.message); }
  }

  const COMMANDS = [
    ["Plans", [["@squad plan this weekend", "Find times that fit everyone's classes"], ["@squad decide tacos, sushi, pizza", "Pick one fairly"], ["@squad weather", "Heat check for outdoor plans"]]],
    ["Catch up", [["@squad highlights", "The messages that matter"], ["@squad mentions", "Everything that tagged you"], ["@squad tldr", "Summary since your last message"], ["@squad voice highlights", "Listen instead of reading"], ["@squad pins", "Pinned messages"]]],
    ["Money", [["@squad bill tax 8% tip 18%\nPriya: pasta 16\nJake: burger 14\nshared: fries 8", "Split a bill to the cent"], ["@squad food run boba 15", "Collect orders for a run"], ["@squad owes", "Who still owes who"]]],
    ["Move-out market", [["@squad sell desk $40, lamp $10 pickup by friday @ Tooker", "List things for sale"], ["@squad market under $30", "Browse what's left"], ["@squad looking for mini fridge under $50", "Get pinged when it's listed"]]],
    ["Home and school", [["@squad chores dishes, trash, bathroom", "Weekly rotation"], ["@squad add oat milk, paper towels", "Shared grocery list"], ["@squad project psych survey due 11/20", "Split a group project"], ["@squad ride to the game", "Carpool"]]],
    ["This squad", [["@squad stats", "Who's talking this week"], ["@squad wrapped", "The week in one message"], ["@squad translate on", "Auto-translate other languages"], ["@squad help", "Everything the bot can do"]]],
  ];
  function toggleCommands(btn) {
    const open = els.chatWrap.querySelector(".popover");
    if (open) { open.remove(); btn.setAttribute("aria-expanded", "false"); return; }
    const pop = h("div", { class: "popover", role: "dialog", "aria-label": "Commands" },
      COMMANDS.map(([group, items]) => [h("h4", {}, group), items.map(([cmd, what]) => h("button", { class: "cmd", onclick: () => {
        els.input.value = cmd; els.input.dispatchEvent(new Event("input")); pop.remove(); btn.setAttribute("aria-expanded", "false"); els.input.focus();
      } }, h("code", {}, cmd.split("\n")[0]), h("span", {}, what)))]));
    pop.addEventListener("keydown", (e) => { if (e.key === "Escape") { pop.remove(); btn.setAttribute("aria-expanded", "false"); btn.focus(); } });
    els.chatWrap.appendChild(pop);
    btn.setAttribute("aria-expanded", "true");
    pop.querySelector("button").focus();
  }

  // ---------- the board ----------
  const TAG = { "🙋": "for you", "📌": "pinned", "⏰": "deadline", "💸": "money", "✅": "decision", "❓": "question", "📅": "date", "🔗": "link" };
  function sec(title, count, ...body) { return h("section", { class: "sec" }, h("h3", {}, title, count != null ? h("span", { class: "c" }, count) : null), body); }
  function when(iso) { const d = new Date(iso); return d.toLocaleDateString([], { weekday: "short" }) + " " + fmtTime(iso); }

  function renderBoard() {
    const b = S.board || {};
    const parts = [h("div", { class: "board-head" }, h("h2", {}, "Board"), h("span", { class: "hint" }, "Updates as the chat happens"))];
    parts.push(planSection(b.plan));

    const waiting = (b.mentions || []).filter((x) => !x.replied && x.kind.indexOf("asked") >= 0);
    if (waiting.length || (b.reminders || []).length) {
      parts.push(sec("Waiting on you", null, h("ul", { class: "list" },
        waiting.map((x) => h("li", { class: "hl" }, h("span", { class: "who" }, x.who + " asked you"), h("span", { class: "when" }, when(x.at)), h("div", { class: "txt" }, x.text))),
        (b.reminders || []).map((r) => h("li", { class: "hl" }, h("span", { class: "who" }, "Reminder"), h("span", { class: "when" }, when(r.at)), h("div", { class: "txt" }, r.text.replace(/^🔔\s*/, "")))))));
    }
    if ((b.highlights || []).length) {
      parts.push(sec("What matters today", b.highlights.length + " of the last 24 hours", h("ol", { class: "list" }, b.highlights.map((x) =>
        h("li", { class: "hl" }, h("span", { class: "who" }, x.who), h("span", { class: "when" }, when(x.at)), h("div", { class: "txt" }, x.text),
          h("div", { class: "tags" }, x.tags.map((t) => TAG[t] ? h("span", { class: "tag k-" + TAG[t].replace(" ", "-") }, TAG[t]) : null)))))));
    }
    const mo = b.money || {};
    if ((mo.owes || []).length || mo.bill || mo.food) {
      parts.push(sec("Money", (mo.owes || []).length ? money(mo.owes.reduce((a, x) => a + x.cents, 0)) + " outstanding" : null,
        (mo.owes || []).length ? h("table", { class: "ledger" }, h("tbody", {}, mo.owes.map((x) => h("tr", {},
          h("td", {}, x.from + " owes " + x.to, h("div", { class: "s" }, x.note)), h("td", { class: "amt" }, money(x.cents)))))) : null,
        mo.bill ? h("p", { class: "hint" }, "Open bill from " + mo.bill.payer + ": " + mo.bill.items.length + " items. Reply \"claim 1 3\" with your item numbers.") : null,
        mo.food ? h("div", { style: "margin-top:8px" }, h("div", { class: "hint" }, mo.food.runner + " is doing a " + mo.food.place + " run, closes " + fmtTime(mo.food.closes) + ". Reply \"+ your order $price\"."),
          h("ul", { class: "list", style: "margin-top:6px" }, mo.food.orders.map((o) => h("li", {}, o.who + ": " + o.what + (o.cents ? ", " + money(o.cents) : ""))))) : null,
        mo.settled ? h("p", { class: "hint", style: "margin:8px 0 0" }, mo.settled + " settled") : null));
    }
    if ((b.market || []).length) {
      const live = b.market.filter((x) => x.status !== "sold");
      parts.push(sec("Move-out market", live.length + " available", h("ul", { class: "list" }, b.market.map((x) => {
        const st = x.status === "sold" ? h("span", { class: "status plain" }, "Sold" + (x.sold_to ? " to " + x.sold_to : ""))
          : x.hold ? h("span", { class: "status wait" }, "On hold for " + x.hold) : h("span", { class: "status ok" }, "Available");
        const sub = [x.seller, x.pickup_by ? "pick up by " + new Date(x.pickup_by).toLocaleDateString([], { month: "short", day: "numeric" }) : null, x.line ? x.line + " in line" : null].filter(Boolean).join(", ");
        return h("li", {}, h("div", { class: "item" }, h("span", { class: "id" }, "#" + x.id),
          h("div", {}, h("div", { class: "t" }, x.title), h("div", { class: "s" }, sub), h("div", { style: "margin-top:4px" }, st)),
          h("div", { class: "price" }, x.price ? "$" + x.price : "Free")));
      }))));
    }
    if (b.rides) {
      const r = b.rides;
      parts.push(sec("Carpool to " + r.to, r.status === "matched" ? "Matched" : "Collecting",
        r.assign ? h("ul", { class: "list" }, Object.entries(r.assign).map(([d, riders]) => h("li", {}, h("b", {}, d), riders.length ? " picks up " + riders.join(", ") : " is driving solo")))
          : h("p", { class: "hint" }, Object.keys(r.drivers).length + " driving, " + r.riders.length + " need a ride. Say \"@squad match rides\" when ready.")));
    }
    if (b.project) {
      const p = b.project, done = p.tasks.filter((t) => t.done).length;
      parts.push(sec(p.name, done + " of " + p.tasks.length + " done",
        h("div", { class: "meter", style: "margin:0 0 10px" }, h("i", { style: "width:" + (p.tasks.length ? 100 * done / p.tasks.length : 0) + "%" })),
        h("ul", { class: "list" }, p.tasks.map((t) => h("li", { class: "check" + (t.done ? " done" : "") }, h("span", { class: "box" }),
          h("div", {}, h("div", { class: "t" }, t.name), h("div", { class: "hint" }, (t.owner || "Unclaimed") + (t.deadline ? ", due " + when(t.deadline) : "")))))) ));
    }
    if ((b.chores || []).length) {
      parts.push(sec("Chores this week", null, h("ul", { class: "list" }, b.chores.map((c) => h("li", { class: "check" + (c.done ? " done" : "") }, h("span", { class: "box" }), h("span", { class: "t" }, c.job + ", " + c.who))))));
    }
    if ((b.groceries || []).length) {
      parts.push(sec("Grocery list", b.groceries.length + " items", h("ul", { class: "list" }, b.groceries.map((g) => h("li", { class: "check" }, h("span", { class: "box" }), h("span", {}, g.item, h("span", { class: "hint" }, "  added by " + g.by)))))));
    }
    if ((b.pins || []).length) {
      parts.push(sec("Pinned", null, h("ul", { class: "list" }, b.pins.slice().reverse().map((x) => h("li", { class: "hl" }, h("span", { class: "who" }, x.who), h("span", { class: "when" }, when(x.at)), h("div", { class: "txt" }, x.text))))));
    }
    if ((b.stats || []).length) {
      const max = Math.max(...b.stats.map((x) => x.count));
      parts.push(sec("Who's talking", "this week", h("div", { class: "bars" }, b.stats.map((x) => h("div", { class: "bar-row" },
        h("span", { class: "n" }, x.name), h("span", { class: "bar-track" }, h("i", { style: "width:" + (100 * x.count / max) + "%" })), h("span", { class: "v" }, x.count)))),
        h("button", { class: "btn small", style: "margin-top:12px", onclick: () => sendText("@squad stats").catch((e) => toast(e.message)) }, "Post full stats in the chat")));
    }
    els.boardBody.replaceChildren(...parts);
    if (!els.assist.childNodes.length) buildAssistant();
  }

  function planSection(p) {
    const ask = (t) => () => sendText(t).catch((e) => toast(e.message));
    if (!p || p.status === "closed" || (p.status === "locked" && p.past)) {
      return sec("Next plan", null, h("div", { class: "plan" }, h("p", { class: "none", style: "margin:0 0 12px" }, "Nothing planned yet. The bot checks everyone's classes and finds two times that work."),
        h("button", { class: "btn primary small", onclick: ask("@squad plan this weekend") }, "Find a time this weekend")));
    }
    if (p.status === "locked") {
      const o = p.options[p.winner];
      const s = new Date(o.start), e = new Date(o.end);
      return sec("Next plan", null, h("div", { class: "plan locked" },
        h("div", { class: "when-day" }, s.toLocaleDateString([], { weekday: "long", month: "long", day: "numeric" })),
        h("div", { class: "when" }, fmtTime(o.start) + "–" + fmtTime(o.end)),
        h("div", { class: "what" }, o.what[0].toUpperCase() + o.what.slice(1)),
        h("div", { class: "where" }, [o.where, o.price ? "about $" + o.price : "free", o.free_food ? "free food" : null].filter(Boolean).join(", ")),
        h("div", { class: "people", "aria-label": "Going" }, p.ins.map((n) => h("span", { title: n }, avatar(n, true)))),
        h("div", { class: "actions" }, p.invite ? h("a", { class: "btn small", href: p.invite, download: "" }, "Add to calendar") : null,
          h("span", { class: "status ok" }, p.ins.length + " going"))));
    }
    if (p.status === "locking") {
      const o = p.options[p.winner];
      const need = Math.floor(p.members.length / 2) + 1;
      return sec("Next plan", null, h("div", { class: "plan" },
        h("div", { class: "when-day" }, "Option " + (p.winner + 1) + " won the vote"),
        h("div", { class: "what", style: "margin:4px 0" }, when(o.start) + ", " + o.what),
        h("p", { class: "hint", style: "margin:0 0 12px" }, p.ins.length + " of " + need + " needed are in. It locks and sends the invite when enough people say in."),
        h("div", { class: "actions" }, h("button", { class: "btn primary small", onclick: ask("in") }, "I'm in"), h("button", { class: "btn small", onclick: ask("out") }, "Can't make it"))));
    }
    const voted = new Set(p.options.flatMap((o) => o.votes)).size;
    return sec("Next plan", "voting, " + voted + " of " + p.members.length + " voted", h("div", { class: "plan" },
      p.options.map((o, i) => h("div", { class: "opt" },
        h("div", {}, h("div", { class: "t" }, (i + 1) + ". " + when(o.start) + "–" + fmtTime(o.end)),
          h("div", { class: "s" }, o.what + (o.free_food ? ", free food" : "") + (o.missing.length ? ". Can't make it: " + o.missing.join(", ") : "")),
          h("div", { class: "meter" }, h("i", { style: "width:" + (p.members.length ? 100 * o.votes.length / p.members.length : 0) + "%" })),
          o.votes.length ? h("div", { class: "s", style: "margin-top:4px" }, o.votes.join(", ")) : null),
        h("button", { class: "btn small", onclick: ask(String(i + 1)), "aria-label": "Vote for option " + (i + 1) }, "Vote")))));
  }

  // ---------- you: private setup ----------
  async function openYou() {
    let me;
    try { me = await refreshMe(); } catch (e) { return toast(e.message); }
    S.inboxSeen = S.inboxLast = (me.inbox.length ? me.inbox[me.inbox.length - 1].id : 0);
    store.set("squad.inboxSeen", String(S.inboxSeen)); updateBadge();
    const p = me.profile;
    const facts = [["Classes", p.classes.join("\n") || "None yet"], ["Exams", p.exams.join("\n") || "None yet"],
      ["Budget", p.budget ? "$" + p.budget + " per hangout" : "Not set"], ["Food", p.diet.join(", ") || "No restrictions"],
      ["Language", p.language || "English"], ["Nicknames", p.nicknames.join(", ") || "None"], ["Venmo", p.venmo ? "@" + p.venmo : "Not set"],
      ["Dorm", p.dorm || "Not set"], ["Birthday", p.birthday || "Not set"], ["Stats", p.hide_stats ? "Hidden from stats" : "Shown in stats"],
      ["Reasons", p.share_reasons ? "Friends see why you're busy" : "Friends only see busy"]];
    const input = h("input", { class: "field", placeholder: "classes MW 10:30-11:45 CSE 310", "aria-label": "Add to your setup", maxlength: "200" });
    const err = h("div", { class: "error", role: "alert" });
    const inbox = h("ul", { class: "list inbox" });
    const fillInbox = (items) => inbox.replaceChildren(...(items.length ? items.map((x) => h("li", {},
      x.text.startsWith("You: ") ? h("span", { class: "mine" }, x.text) : [h("div", { class: "from" }, (x.to ? "To " + x.to + ", " : "") + "Squad Radar, " + when(x.at)), x.text])) : [h("li", { class: "hint" }, "Nothing here yet. Private replies and secret birthday planning show up here.")]));
    fillInbox(me.inbox);
    const examples = ["classes TTh 1:30-2:45 MAT 343", "exam nov 12 9am BIO midterm", "budget 15", "vegetarian", "hours 6", "skills research, writing",
      "language tamil", "nickname ammu", "venmo @your-name", "dorm Tooker", "birthday nov 10", "flight dec 12 2pm", "study buddy on", "hide my stats"];
    const close = () => { back.remove(); drawer.remove(); if (S.view === "you") setView("chat"); };
    const form = h("form", { class: "inline", onsubmit: async (e) => {
      e.preventDefault(); err.textContent = "";
      if (!input.value.trim()) return;
      try { await api("POST", "/api/me/setup", { text: input.value }); input.value = ""; close(); openYou(); }
      catch (x) { err.textContent = x.message; }
    } }, input, h("button", { class: "btn primary", type: "submit" }, "Save"));
    const back = h("div", { class: "drawer-back", onclick: close });
    const drawer = h("div", { class: "drawer", role: "dialog", "aria-modal": "true", "aria-label": "Your setup" },
      h("div", { class: "drawer-head" }, h("h2", {}, S.me.name), h("button", { class: "btn quiet small", onclick: close }, "Close")),
      h("div", { class: "drawer-body" },
        h("div", { class: "note-box" }, "This is private. When the bot plans, your friends only learn whether you're free, never what fills your week."),
        h("div", {}, h("h3", {}, "Your setup"), h("dl", { class: "facts" }, facts.map(([k, v]) => [h("dt", {}, k), h("dd", { style: "white-space:pre-line" }, v)]))),
        h("div", {}, h("h3", {}, "Add or change something"), form, err,
          h("div", { class: "chips" }, examples.map((x) => h("button", { class: "chip", type: "button", onclick: () => { input.value = x; input.focus(); } }, x)))),
        h("div", {}, h("h3", {}, "Private messages"), inbox),
        h("div", {}, h("button", { class: "btn quiet small", onclick: () => { if (confirm("Sign out on this device? You'll need your name to come back, and you'll start as a new person.")) { store.del("squad.token"); location.href = "/"; } } }, "Sign out"))));
    drawer.addEventListener("keydown", (e) => { if (e.key === "Escape") close(); });
    document.body.append(back, drawer);
    input.focus();
  }

  window.addEventListener("popstate", boot);
  boot();
})();
