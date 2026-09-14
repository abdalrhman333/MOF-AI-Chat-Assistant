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
import { load, memoryStorage, stub, source } from "./harness.mjs";

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

test("the two dictionaries hold the same keys", () => {
  // A key present in one language and missing from the other renders as the
  // key itself — "try1" in the middle of an Arabic screen. Nothing warns.
  const a = load();
  const ar = Object.keys(a.get("T").ar).sort();
  const en = Object.keys(a.get("T").en).sort();
  assert.deepEqual(ar.filter((k) => !en.includes(k)), [], "missing from en");
  assert.deepEqual(en.filter((k) => !ar.includes(k)), [], "missing from ar");
});

test("no translation is left as its own key", () => {
  const a = load();
  for (const lang of ["ar", "en"]) {
    for (const [key, value] of Object.entries(a.get("T")[lang])) {
      assert.notEqual(value, key, `${lang}.${key} was never translated`);
      assert.ok(String(value).trim(), `${lang}.${key} is empty`);
    }
  }
});

test("the chips are translated, not raw keys", () => {
  const a = load();
  a.set("UI", "en");
  assert.match(a.t("try1"), /overview/i);
  a.set("UI", "ar");
  assert.notEqual(a.t("try1"), "try1", "a missing translation would show the key");
});

// ---------------------------------------------------------------------------
// The project header — both names, as the design writes them
// ---------------------------------------------------------------------------

const PROJECT = { code: "PRJ-06", name_ar: "مشروع التحول الرقمي",
                  name_en: "Digital Transformation", display_name: "PRJ-06 display" };

test("the header leads with the UI language and carries the other name below", () => {
  const a = load();
  a.set("CURRENT", PROJECT);
  a.renderTopbar();
  assert.equal(a.el("chatTitle").textContent, "مشروع التحول الرقمي - PRJ-06");
  assert.equal(a.el("chatSub").textContent, "Digital Transformation");
  a.set("UI", "en");
  a.renderTopbar();
  assert.equal(a.el("chatTitle").textContent, "Digital Transformation - PRJ-06");
  assert.equal(a.el("chatSub").textContent, "مشروع التحول الرقمي");
});

test("a project missing a name falls back to its display name, not to blank", () => {
  const a = load();
  a.set("CURRENT", { code: "PRJ-09", name_ar: null, name_en: null, display_name: "Legacy" });
  a.renderTopbar();
  assert.equal(a.el("chatTitle").textContent, "Legacy - PRJ-09");
  assert.equal(a.el("chatSub").textContent, "", "no second name is invented");
});

test("switching project replaces every place the old one was named", () => {
  const a = load();
  a.set("CURRENT", PROJECT);
  a.renderTopbar();
  a.set("CURRENT", { code: "PRJ-02", name_ar: "خدماتي", name_en: "Khadamati", display_name: "" });
  a.renderTopbar();
  for (const id of ["chatTitle", "chatSub", "barTitle", "barMeta", "projName", "projCode"]) {
    assert.ok(!a.el(id).textContent.includes("PRJ-06"), `${id} still names PRJ-06`);
    assert.ok(!a.el(id).textContent.includes("التحول"), `${id} still names the old project`);
  }
});

// ---------------------------------------------------------------------------
// Source chips
// ---------------------------------------------------------------------------

test("a document source reads as its Arabic type and page", async () => {
  const a = await chatting({ "/messages": { status: 200, body: { ...REPLY, grounded: true,
    citations: [{ source: "document", label: "Risk Register — page 14",
                  doc_type: "Risk Register", filename: "risks.docx", page: 14 }] } } });
  await a.ask("hi");
  assert.ok(a.el("chatView").children.at(-1).textContent.includes("سجل المخاطر - ص 14"));
});

test("each source is listed once, however many passages came from it", async () => {
  // Found live: five passages from page 1 of one risk register produced five
  // identical chips. Distinct pages are still distinct sources.
  const page = (n) => ({ source: "document", label: "x", doc_type: "Risk Register", page: n });
  const a = await chatting({ "/messages": { status: 200, body: { ...REPLY, grounded: true,
    citations: [page(1), page(1), page(1), page(2)] } } });
  await a.ask("hi");
  const shown = a.el("chatView").children.at(-1).textContent;
  assert.equal(shown.split("سجل المخاطر - ص 1").length - 1, 1);
  assert.equal(shown.split("سجل المخاطر - ص 2").length - 1, 1);
});

test("an unknown document type is shown as it arrives, never dropped", async () => {
  // A label added to the pipeline must not make a source disappear.
  const a = await chatting({ "/messages": { status: 200, body: { ...REPLY, grounded: true,
    citations: [{ source: "document", label: "x", doc_type: "Board Paper", page: 2 }] } } });
  await a.ask("hi");
  assert.ok(a.el("chatView").children.at(-1).textContent.includes("Board Paper - ص 2"));
});

test("the SPM record shows its date, not the English provenance sentence", async () => {
  const a = await chatting({ "/messages": { status: 200, body: { ...REPLY,
    citations: [{ source: "project_record", label: "SPM project record (record updated 2026-08-04)",
                  as_of: "record updated 2026-08-04" }] } } });
  await a.ask("hi");
  const shown = a.el("chatView").children.at(-1).textContent;
  assert.ok(shown.includes("سجل المشروع في SPM"));
  assert.ok(shown.includes("آخر تحديث"), "updated and created are different claims");
  assert.ok(shown.includes("2026"));
  assert.ok(!shown.includes("record updated"), "the English sentence must not leak");
});

test("'drawn from this project's documents' appears only when documents were used", async () => {
  const onlyRecord = await chatting({ "/messages": { status: 200, body: { ...REPLY, grounded: true,
    citations: [{ source: "project_record", label: "SPM", as_of: "record created 2026-08-01" }] } } });
  await onlyRecord.ask("hi");
  assert.ok(!onlyRecord.el("chatView").children.at(-1).textContent.includes(onlyRecord.t("grounded")),
    "an answer from the SPM record alone was not drawn from documents");

  const withDoc = await chatting({ "/messages": { status: 200, body: { ...REPLY, grounded: true,
    citations: [{ source: "document", label: "c", doc_type: "Project Charter", page: 1 }] } } });
  await withDoc.ask("hi");
  assert.ok(withDoc.el("chatView").children.at(-1).textContent.includes(withDoc.t("grounded")));
});

// ---------------------------------------------------------------------------
// Knowledge base
// ---------------------------------------------------------------------------

function kb(documents) {
  const a = load();
  a.set("CURRENT", PROJECT);
  a.set("KB_DATA", { project_code: "PRJ-06", documents, indexed: 0, total: documents.length });
  return a;
}

const DOCS = [
  { filename: "charter.docx", doc_type: "Project Charter", indexing_status: "indexed" },
  { filename: "risks.docx", doc_type: "Risk Register", indexing_status: "indexed" },
  { filename: "budget.xlsx", doc_type: "Budget/Financial Report", indexing_status: "failed" },
  { filename: "minutes.pdf", doc_type: "Meeting Minutes", indexing_status: "pending" },
  { filename: "handover.docx", doc_type: "Handover Document", indexing_status: "indexed" },
  { filename: "lessons.docx", doc_type: "Lessons Learned", indexing_status: "indexed" },
  { filename: "<img src=x onerror=alert(1)>.pdf", doc_type: "Weekly Status Report", indexing_status: "indexed" },
];

const find = (node, predicate) => {
  if (predicate(node)) return node;
  for (const child of node.children || []) { const hit = find(child, predicate); if (hit) return hit; }
  return null;
};
const rowsOf = (a) => find(a.el("kbView"), (n) => n.tagName === "TBODY").children;

test("the filters count every state, and anything unfinished is 'processing'", () => {
  const a = kb(DOCS);
  a.renderKb();
  const filters = find(a.el("kbView"), (n) => n.className === "filters").children.map((b) => b.textContent);
  assert.deepEqual(filters, ["الكل 7", "مفهرسة 5", "قيد المعالجة 1", "فشلت 1"]);
});

test("the columns run name, type, status — and each row matches its header", () => {
  const a = kb(DOCS);
  a.renderKb();
  const header = find(a.el("kbView"), (n) => n.tagName === "THEAD").children[0].children
    .map((th) => th.textContent);
  assert.deepEqual(header, ["اسم الوثيقة", "النوع", "الحالة"]);
  const cells = rowsOf(a)[0].children.map((td) => td.textContent);
  assert.deepEqual(cells, ["charter.docx", "ميثاق المشروع", "مفهرسة"]);
});

test("the table pages five at a time and says where it is", () => {
  const a = kb(DOCS);
  a.renderKb();
  assert.equal(rowsOf(a).length, 5);
  assert.ok(a.el("kbView").textContent.includes("عرض 1 إلى 5 من 7"));
  a.set("KB_PAGE", 1);
  a.renderKb();
  assert.equal(rowsOf(a).length, 2);
  assert.ok(a.el("kbView").textContent.includes("عرض 6 إلى 7 من 7"));
});

test("search matches a file name or the Arabic type the user can see", () => {
  const a = kb(DOCS);
  a.set("KB_QUERY", "سجل المخاطر");
  a.renderKb();
  assert.equal(rowsOf(a).length, 1);
  assert.ok(rowsOf(a)[0].textContent.includes("risks.docx"));
});

test("a failed document shows its state, and no retry the app cannot perform", () => {
  const a = kb(DOCS);
  a.set("KB_FILTER", "failed");
  a.renderKb();
  assert.ok(rowsOf(a)[0].textContent.includes("فشلت"));
  assert.ok(!a.el("kbView").textContent.includes("إعادة المحاولة"),
    "the app's database role is read-only; a retry button would be a lie");
});

test("a file name is inserted as text", () => {
  const a = kb(DOCS);
  a.set("KB_PAGE", 1);
  a.renderKb();
  const cell = find(a.el("kbView"), (n) => n.className === "docname" && n.textContent.includes("<img"));
  assert.ok(cell, "shown verbatim…");
  assert.equal(cell.innerHTML, "", "…and never parsed");
});

test("an empty search says so rather than showing an empty table", () => {
  const a = kb(DOCS);
  a.set("KB_QUERY", "nothing matches this");
  a.renderKb();
  assert.ok(a.el("kbView").textContent.includes(a.t("kbnone")));
  assert.ok(a.el("kbView").textContent.includes("عرض 0 إلى 0 من 0"));
});

// ---------------------------------------------------------------------------
// Recently used projects
// ---------------------------------------------------------------------------

test("recent projects are most-recent first, unique, and capped at three", () => {
  const a = load({ storage: memoryStorage() });
  for (const code of ["PRJ-01", "PRJ-02", "PRJ-03", "PRJ-01", "PRJ-04"]) a.rememberProject(code);
  assert.deepEqual([...a.recentProjects()], ["PRJ-04", "PRJ-01", "PRJ-03"]);
});

test("corrupt stored data is ignored, not trusted", () => {
  const a = load({ storage: memoryStorage({ seed: { "enjaz.recentProjects": '{"not":"a list"}' } }) });
  assert.deepEqual([...a.recentProjects()], []);
  const b = load({ storage: memoryStorage({ seed: { "enjaz.recentProjects": "not json" } }) });
  assert.deepEqual([...b.recentProjects()], []);
});

test("blocked storage never breaks the page", () => {
  const a = load({ storage: memoryStorage({ broken: true }) });
  assert.deepEqual([...a.recentProjects()], []);
  a.rememberProject("PRJ-01");   // must not throw
});

test("a remembered project the user is no longer assigned is not opened", async () => {
  // Browser memory only orders the list. What may be opened is the server's.
  const storage = memoryStorage({ seed: { "enjaz.recentProjects": '["PRJ-99","PRJ-02"]' } });
  const s = stub({
    "/api/projects": { status: 200, body: [PROJECT, { code: "PRJ-02", name_ar: "خدماتي",
                                                      name_en: "Khadamati", display_name: "" }] },
    "/api/conversations": { status: 200, body: OPENED },
    "/api/auth/me": { status: 403, body: {} },
  });
  const a = load({ fetch: s.fetch, storage });
  a.set("ME", { full_name_ar: "نورة", full_name_en: "Noura" });
  await a.get("enterApp")();
  assert.equal(a.get("CURRENT").code, "PRJ-02", "PRJ-99 was remembered but is not assigned");
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
