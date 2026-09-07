/* A DOM small enough to read, honest enough to catch real mistakes.
 *
 * The frontend is one static file with an inline <script>. Rather than add a
 * bundler, jsdom and a package.json to test five screens, this loads the real
 * script out of the real file and runs it against a hand-written DOM.
 *
 * ⚠️ The element registry is seeded from the ids that actually appear in the
 * markup, and getElementById THROWS for anything else. That is the point: a
 * `$("chps")` typo is invisible in a browser until a user clicks the thing,
 * and invisible in a shim that invents elements on demand. Here it fails.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import vm from "node:vm";

const HTML = path.join(
  path.dirname(fileURLToPath(import.meta.url)), "..", "..",
  "Application", "frontend", "index.html");

export function source() {
  return readFileSync(HTML, "utf8");
}

function ids(html) {
  return new Set([...html.matchAll(/\sid="([^"]+)"/g)].map((m) => m[1]));
}

class ClassList {
  constructor(el) { this.el = el; }
  get _s() { return new Set(this.el.className.split(" ").filter(Boolean)); }
  _w(s) { this.el.className = [...s].join(" "); }
  add(...c) { const s = this._s; c.forEach((x) => s.add(x)); this._w(s); }
  remove(...c) { const s = this._s; c.forEach((x) => s.delete(x)); this._w(s); }
  contains(c) { return this._s.has(c); }
  toggle(c, on) { on === undefined ? (this.contains(c) ? this.remove(c) : this.add(c))
                                   : (on ? this.add(c) : this.remove(c)); }
}

export class El {
  constructor(tag = "div") {
    this.tagName = tag.toUpperCase();
    this.children = []; this.dataset = {}; this.style = {};
    this._class = ""; this._text = ""; this.dir = ""; this.value = "";
    this.disabled = false; this.placeholder = ""; this.type = "";
    this.scrollTop = 0; this.scrollHeight = 0;
    this.listeners = {}; this.classList = new ClassList(this);
    this.focused = 0; this.removed = false;
  }
  get className() { return this._class; }
  set className(v) { this._class = v || ""; }

  // textContent is the safe path and innerHTML is the dangerous one, so the
  // shim keeps them distinguishable instead of collapsing both into a string.
  get textContent() {
    return this._text + this.children.map((c) => c.textContent).join("");
  }
  set textContent(v) { this._text = v == null ? "" : String(v); this.children = []; }
  get innerHTML() { return this._html ?? ""; }
  set innerHTML(v) { this._html = String(v); this.children = []; this._text = ""; }

  append(...n) { for (const c of n) { c.parent = this; this.children.push(c); } }
  remove() { this.removed = true;
    if (this.parent) this.parent.children = this.parent.children.filter((c) => c !== this); }
  setAttribute(k, v) { this[k] = v; }
  focus() { this.focused += 1; }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  dispatch(type, event = {}) {
    const e = { preventDefault() {}, stopPropagation() {}, ...event };
    return Promise.all((this.listeners[type] || []).map((fn) => fn(e)));
  }
  querySelector() { return new El(); }
  querySelectorAll() { return []; }
}

/** Load the page's script into a fresh context. `fetch` is supplied by the
 *  test, so every network answer in these tests is one the test chose. */
export function load({ fetch = async () => ({ ok: true, status: 200, json: async () => ({}) }) } = {}) {
  const html = source();
  const script = html.slice(html.lastIndexOf("<script>") + 8,
                           html.lastIndexOf("</script>"));
  const known = ids(html);
  const els = new Map();
  const root = new El("html");

  const document = {
    documentElement: root,
    getElementById(id) {
      if (!known.has(id)) {
        throw new Error(`the page has no element with id "${id}"`);
      }
      if (!els.has(id)) { const e = new El(); e.id = id; els.set(id, e); }
      return els.get(id);
    },
    createElement: (tag) => new El(tag),
    querySelectorAll: () => [],
    body: new El("body"),
  };

  const ctx = vm.createContext({
    document, fetch, console,
    confirm: () => true, alert: () => {},
    setTimeout, clearTimeout, encodeURIComponent, JSON, Promise, Error, Math, Date,
  });
  vm.runInContext(`${script}\n;globalThis.__ = { t, api, ask, bubble, ` +
    `renderChips, endSession, toLogin, renderTopbar, refreshDocCount, ` +
    `startConversation, showView, applyLanguage, ` +
    `set: (k, v) => { eval(k + " = v"); }, get: (k) => eval(k) };`, ctx);
  return { ...ctx.__, document, el: (id) => document.getElementById(id) };
}

/** A fetch that answers with a fixed status/body, and records what it saw. */
export function stub(answers) {
  const calls = [];
  const fetch = async (path, options = {}) => {
    calls.push({ path, ...options });
    // The furthest-right match wins. "/api/conversations" is a PREFIX of
    // "/api/conversations/c1/messages", so first-match — and longest-match,
    // since the prefix is the longer string — would answer every question
    // with a welcome. Position is what actually distinguishes them.
    const key = Object.keys(answers)
      .filter((k) => path.includes(k))
      .sort((a, b) => path.lastIndexOf(b) - path.lastIndexOf(a))[0] ?? "*";
    const a = answers[key] ?? { status: 200, body: {} };
    return { ok: a.status < 400, status: a.status,
             json: async () => { if (a.bad) throw new Error("not json");
                                 return a.body ?? {}; } };
  };
  return { fetch, calls };
}
