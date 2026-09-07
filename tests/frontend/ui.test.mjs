/* Tests for the one file that had none.
 *
 * The frontend renders decisions; it never makes them. So these tests are
 * mostly about that discipline holding: that nothing here decides who the
 * user is, that everything the server says is inserted as TEXT, and that the
 * two failures a user will actually hit — a rate limit and an expired
 * session — end somewhere they can act on rather than in a dead screen.
 */
import test from "node:test";
import assert from "node:assert/strict";
import { load, stub, source } from "./harness.mjs";

const OPENED = { conversation_id: "c1", welcome: "أهلاً نورة" };
const REPLY = { text: "الميزانية 12 مليون", outcome: "answered",
                intent: "project_overview", reply_language: "ar",
                citations: [{ label: "SPM" }], timings: { total_ms: 40 } };

function app(answers) {
  const s = stub(answers);
  const a = load({ fetch: s.fetch });
  a.calls = s.calls;
  return a;
}

async function chatting(answers = {}) {
  const a = app({ "/api/conversations": { status: 200, body: OPENED }, ...answers });
  a.set("ME", { full_name_ar: "نورة", full_name_en: "Noura" });
  a.set("CURRENT", { code: "PRJ-06", display_name: "مشروع" });
  await a.startConversation();
  return a;
}

// ---------------------------------------------------------------------------
// Identity is the server's to establish
// ---------------------------------------------------------------------------

test("no request ever carries a username", async () => {
  const a = await chatting({ "/messages": { status: 200, body: REPLY } });
  await a.ask("كم الميزانية؟");
  for (const call of a.calls) {
    const body = call.body ?? "";
    assert.ok(!/username|user_id|"email"/.test(body) || call.path.includes("/auth/login"),
      call.path + " sent identity the server should establish itself: " + body);
  }
});

test("every request sends the cookie and only the cookie", () => {
  const src = source();
  assert.match(src, /credentials: "same-origin"/);
  assert.ok(!/Authorization/i.test(src),
    "a bearer token here would be a second, weaker way to be someone");
});

// ---------------------------------------------------------------------------
// Server text is text
// ---------------------------------------------------------------------------

test("an answer is inserted as text, never as markup", async () => {
  const a = await chatting({ "/messages": { status: 200, body:
    { ...REPLY, text: "<img src=x onerror=alert(1)>" } } });
  await a.ask("hi");
  const shown = a.el("chatView").children.at(-1);
  assert.ok(shown.textContent.includes("<img"), "shown verbatim…");
  assert.equal(shown.innerHTML, "", "…and never parsed as HTML");
});

test("a citation label is inserted as text too", async () => {
  const a = await chatting({ "/messages": { status: 200, body: { ...REPLY,
    citations: [{ label: "<b>doc</b>" }] } } });
  await a.ask("hi");
  const node = a.el("chatView").children.at(-1);
  assert.ok(node.textContent.includes("<b>doc</b>"));
  assert.equal(node.innerHTML, "");
});

test("the welcome is inserted as text", async () => {
  const a = await chatting();
  assert.equal(a.el("chatView").children.at(-1).innerHTML, "");
});

test("innerHTML is only ever assigned a constant", () => {
  // The typing dots and the picker frame are markup written here; nothing
  // that came off the network may join them. A literal with no ${} in it
  // cannot carry a value, which is the property being checked — so a
  // template literal is allowed, but only while it stays constant.
  const src = source();
  for (const m of src.matchAll(/\.innerHTML\s*=\s*(`(?:[^`\\]|\\.)*`|[^;\n]+)/g)) {
    const value = m[1].trim();
    if (/^("[^"]*"|'[^']*')$/.test(value)) continue;
    assert.ok(value.startsWith("`") && !value.includes("${"),
      "innerHTML assigned something that can carry network text: " + value.slice(0, 90));
  }
});

// ---------------------------------------------------------------------------
// Reply direction comes from the server's detection
// ---------------------------------------------------------------------------

test("the server's detected language beats the script of the text", async () => {
  // An English answer that names an Arabic project: the script heuristic
  // sees Arabic letters and would flip the whole bubble to RTL. The model
  // already decided the reply language, so that decision wins — the
  // heuristic is only for text the server never labelled.
  const a = await chatting({ "/messages": { status: 200, body:
    { ...REPLY, text: "The budget for مشروع الرقمنة is 12M SAR.",
      reply_language: "en" } } });
  await a.ask("hi");
  assert.equal(a.el("chatView").children.at(-1).dir, "ltr",
    "the model detected the language; the page must not re-decide it");
});

test("a message with no stated language is laid out from its own script", async () => {
  const a = await chatting();
  a.bubble("مرحبا", "user");
  assert.equal(a.el("chatView").children.at(-1).dir, "rtl");
});

// ---------------------------------------------------------------------------
// Rate limiting — a wait, not a failure
// ---------------------------------------------------------------------------

test("a 429 is explained in the user's language", async () => {
  const a = await chatting({ "/messages": { status: 429, body: { detail: "Too many" } } });
  await a.ask("hi");
  const last = a.el("chatView").children.at(-1);
  assert.equal(last.textContent, a.t("tooFast"));
  assert.ok(!last.textContent.includes("Too many"),
    "an English detail string must not reach an Arabic reader");
});

test("a 429 keeps the user signed in and able to try again", async () => {
  const a = await chatting({ "/messages": { status: 429, body: {} } });
  await a.ask("hi");
  assert.ok(a.get("ME"), "being told to slow down is not being signed out");
  assert.equal(a.el("sendBtn").disabled, false);
});

// ---------------------------------------------------------------------------
// An expired session
// ---------------------------------------------------------------------------

test("a 403 while chatting returns to sign-in and says why", async () => {
  const a = await chatting({ "/messages": { status: 403, body: {} } });
  await a.ask("hi");
  assert.equal(a.el("login").classList.contains("hidden"), false);
  assert.ok(a.el("app").classList.contains("hidden"));
  assert.equal(a.el("loginError").textContent, a.t("expired"));
});

test("the ended session takes its conversation with it", async () => {
  const a = await chatting({ "/messages": { status: 403, body: {} } });
  await a.ask("hi");
  assert.equal(a.get("CONV"), null);
  assert.equal(a.get("ME"), null);
  assert.equal(a.el("chatView").children.length, 0,
    "those turns belong to a session that no longer exists");
});

test("a 403 before sign-in is a login failure, not an expiry", async () => {
  // Nothing to expire yet; the sign-in screen owns that message. /auth/me
  // refuses too, which is what "not signed in" looks like on a real load.
  const a = app({ "/api/auth/me": { status: 403, body: {} },
                  "/api/conversations": { status: 403, body: {} } });
  a.set("ME", null);
  a.set("CURRENT", { code: "PRJ-06", display_name: "م" });
  await a.startConversation();
  assert.equal(a.el("loginError").textContent, "");
});

// ---------------------------------------------------------------------------
// Suggestion chips
// ---------------------------------------------------------------------------

test("the chips appear with the welcome", async () => {
  const a = await chatting();
  assert.equal(a.el("chips").children.length, 3);
  assert.ok(!a.el("chips").classList.contains("hidden"));
});

test("a chip is an ordinary question, not a pre-decided intent", async () => {
  const a = await chatting({ "/messages": { status: 200, body: REPLY } });
  await a.el("chips").children[0].dispatch("click");
  const sent = a.calls.find((c) => c.path.includes("/messages"));
  assert.deepEqual(JSON.parse(sent.body), { text: a.t("try1") },
    "the chip sends text and nothing else; the model still classifies it");
});

test("the chips go away once the conversation has started", async () => {
  const a = await chatting({ "/messages": { status: 200, body: REPLY } });
  await a.ask("hi");
  assert.ok(a.el("chips").classList.contains("hidden"));
});

test("the chips are translated, not raw keys", () => {
  const a = load();
  a.set("UI", "en");
  assert.match(a.t("try1"), /overview/i);
  a.set("UI", "ar");
  assert.notEqual(a.t("try1"), "try1", "a missing translation would show the key");
});

// ---------------------------------------------------------------------------
// The indexed-document count
// ---------------------------------------------------------------------------

test("the header shows the indexed count once it is known", async () => {
  const a = app({ "/documents": { status: 200, body: { indexed: 14, total: 16 } } });
  a.set("CURRENT", { code: "PRJ-06", display_name: "مشروع" });
  await a.refreshDocCount();
  assert.match(a.el("barMeta").textContent, /PRJ-06/);
  assert.match(a.el("barMeta").textContent, /14/);
});

test("a failed lookup leaves the code standing rather than claiming zero", async () => {
  const a = app({ "/documents": { status: 500, body: {} } });
  a.set("CURRENT", { code: "PRJ-06", display_name: "مشروع" });
  a.renderTopbar();
  await a.refreshDocCount();
  assert.equal(a.el("barMeta").textContent, "PRJ-06",
    "'0 documents' would read as an answer about the project");
});

test("switching project does not carry the old count across", () => {
  const a = load();
  a.set("CURRENT", { code: "PRJ-06", display_name: "أ" });
  a.set("DOC_COUNT", 14);
  a.renderTopbar();
  assert.match(a.el("barMeta").textContent, /14/);
  a.set("CURRENT", { code: "PRJ-02", display_name: "ب" });
  a.set("DOC_COUNT", null);
  a.renderTopbar();
  assert.equal(a.el("barMeta").textContent, "PRJ-02");
});

// ---------------------------------------------------------------------------
// Housekeeping
// ---------------------------------------------------------------------------

test("one question at a time", async () => {
  const a = await chatting({ "/messages": { status: 200, body: REPLY } });
  a.set("BUSY", true);
  await a.ask("second");
  assert.equal(a.calls.filter((c) => c.path.includes("/messages")).length, 0);
});

test("the send button comes back after a failure", async () => {
  const a = await chatting({ "/messages": { status: 500, body: {} } });
  await a.ask("hi");
  assert.equal(a.el("sendBtn").disabled, false);
});

test("leaving the chat hides the chips with the composer", async () => {
  const a = await chatting();
  a.showView("kb");
  assert.ok(a.el("chips").classList.contains("hidden"));
});
