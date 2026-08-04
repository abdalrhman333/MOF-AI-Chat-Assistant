# المساعد الرقمي الذكي — منصة إنجاز — Solution Scope

Source: وثيقة المتطلبات التفصيلية الموحدة – الوكيل الذكي (01/07/2026),
Section 3 (المساعد الرقمي الذكي / شات بوت — منصة انجاز), National Center
for Government Governance Regulation (NCGR).

This document describes WHAT the solution must be and do. It contains no
technology choices, no architecture, no build steps. Read this to
understand the target; implementation decisions come later and separately.

---

## Purpose

منصة إنجاز needs an AI-powered digital assistant that acts as an indexed
knowledge manager over each user's assigned project documents. Project
managers should be able to ask questions about their projects in natural
language — Arabic, English, or mixed — and get answers grounded in the
actual project documentation, without manually searching through SharePoint.

The goal: reduce time-to-information and unify how project managers
interact with project data, currently scattered across SharePoint documents
requiring manual search.

## Scope — in

1. A web application embedded within منصة إنجاز, authenticating users via
   the organization's internal Active Directory, that lets them interact
   with their project documents.
2. A project switcher that confines each session to one of the projects
   assigned to that user in SPM (SharePoint-based Project Management
   system) — with no cross-project access under any circumstance.
3. A chat interface accepting natural-language questions in Arabic,
   English, or mixed, returning answers grounded in project documents.
4. Ingestion and indexing of project documents from SPM/SharePoint as the
   primary source.
5. The ability to upload supplementary documents directly within the chat
   window.
6. On-demand generation of a concise project status overview.
7. A knowledge base page (view-only) showing which documents are linked to
   a project, their type, and their indexing status.
8. Full bilingual support (Arabic/English) with a language switcher and
   right-to-left (RTL) text support.
9. Authentication via Active Directory with single sign-on (SSO), one-time
   password (OTP) as a second factor, and role-based access control (RBAC).

## Scope — out

1. Enterprise identity governance (provided by the existing AD service —
   not built here).
2. Creating or managing project metadata (SPM remains the system of
   record).
3. Editing or managing source documents (read-only relationship to source
   content).
4. Voice interaction (text only, this phase).
5. Personalized responses or access to a user's personal account data
   outside project scope.
6. Executing transactions or any write operations against other systems —
   the assistant is purely informational.

## Actors / stakeholders

| Role | Relationship |
|---|---|
| Project managers | End users |
| System / IT administrator | Manages access, document-project linking, indexing rules |
| NCGR IT team | Implementing technical party |
| System owner | Project sponsor |
| Business analysis team | Requirements owner |

## User stories and acceptance criteria

### 1 — Conversation flow
A user opens a session in منصة إنجاز and types a question. The system
greets them, detects the input language, verifies it's supported, analyzes
the query intent (routing to either a "special" handling path or a
"general" path that retrieves from the SPM knowledge base), generates a
response, and allows further questions or session end — without needing to
restart the conversation.

- **AC01** — The system verifies the input language and rejects
  unsupported languages with a clear apology message.
- **AC02** — The system can distinguish between queries needing special
  handling versus general SPM-knowledge-base retrieval.
- **AC03** — The system supports session continuity (a loop) — the user
  can ask multiple consecutive questions without restarting.

### 2 — Session start and welcome
On opening a session, the assistant immediately shows a bilingual
(Arabic/English) welcome message and accepts text input, auto-detecting
the query language and replying in kind.

- **AC04** — A bilingual welcome message appears immediately on session
  start.
- **AC05** — The assistant replies in the same language as the query
  (Arabic or English).

### 3 — Informational query handling
The assistant analyzes intent, classifies it as in-scope or out-of-scope,
retrieves information strictly from the approved indexed content, generates
a grounded response citing its source, and gives a clear, safe fallback
when knowledge is missing or the query is out of scope.

- **AC06** — The assistant's answers are grounded only in indexed
  documents, with the source cited.
- **AC07** — A clear, appropriate fallback response is sent for
  out-of-scope queries.
- **AC08** — Response time does not exceed 5 seconds under normal
  conditions.

### 4 — Access control and identity verification
The system integrates with Active Directory, supporting SSO and OTP as an
additional security layer. It retrieves the user's project list from SPM
automatically at session start and strictly (منعاً تاماً — absolutely)
prevents any access to documents of projects not assigned to that user,
under any circumstance.

- **AC08** — An authorized user can sign in via SSO/OTP and reach the
  assistant. *(Note: AC08 is used for both response-time and
  auth-success criteria in the source document — reported faithfully as
  written, not merged or renumbered.)*
- **AC09** — No user can view documents of a project not assigned to them.

### 5 — Knowledge base and document management
Project documents are indexed automatically from SPM/SharePoint as the
primary source. The project manager can upload supplementary documents
directly in the chat. The knowledge base page (view-only) shows linked
documents, their type, and indexing status. The system administrator can
link documents to projects and configure indexing rules. Source documents
are never edited within the assistant. The assistant reads project-linked
documents and performs OCR on them to retrieve their content — contingent
on a structured relationship existing that links each project to its
documents.

- **AC10** — The knowledge base page shows documents linked to the
  project, their type, and indexing status.
- **AC11** — The assistant reads project-linked documents and performs OCR
  to retrieve their content, provided a structured project-to-document
  relationship exists.

## Dependencies

1. Active Directory must be active and functioning for identity and
   access management.
2. The SPM system on SharePoint — project documents must be available in
   indexable formats.
3. Users must be correctly linked to their projects within SPM.
4. A stable internal network connection between the user, the assistant
   server, and the databases.

## Assumptions

1. An effective internal Active Directory environment is available for
   identity and access management.
2. Project documents in SPM/SharePoint are in indexable formats.
3. Answer quality is bounded by the completeness and accuracy of the
   indexed documents.
4. The assistant is available via the منصة إنجاز website only, in this
   first phase.
5. Interaction is text-only in this phase — no voice interaction.
6. The assistant does not access external or public information sources
   outside the institutional knowledge base.
7. SPM remains the reference system of record for project data — the
   assistant does not create new project metadata.

## Constraints

1. No transactions — the assistant is purely informational and performs
   no write operations against any system.
2. Internal sources only — no access to external sources or the public
   internet.
3. The knowledge base page is view-only — managing source documents stays
   outside the assistant's scope.

## Practical note for a demo/development build

Real Active Directory and a live SPM/SharePoint connection are external
dependencies not available during development. A demo build should
simulate these — mock users standing in for AD/RBAC, a mock document
corpus mirroring realistic SPM project-folder structure standing in for
the live SharePoint connection — while satisfying the same acceptance
criteria above wherever feasible against that mock data. Where a real
integration is a hard dependency (e.g. true OTP, true SPM API), that
should be clearly flagged as deferred rather than faked in a way that
looks production-ready.
