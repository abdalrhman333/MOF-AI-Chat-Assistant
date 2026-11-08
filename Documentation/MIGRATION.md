# Moving this solution to another device

**Last verified:** 2026-11-08, by inventorying the running system on the
machine it was built on.

This is not a rebuild. The repository already carries the Compose file, the
`Dockerfile`, all 20 migrations and every seed, so most of the system
reconstructs itself from a clone. Four things it deliberately does not carry
— and one of those is a decision rather than a copy.

Read `START_HERE.md` first if you have not. This file assumes you know what
the system is.

---

## 1. What actually has to travel

Everything lives in three places on `D:`. Nothing is on `C:` except Docker
Desktop itself.

| What | Where | Size | Carry it? |
|---|---|---|---|
| The repository | `D:\MOF Enjaz Chatbot` | 4.4 MB (211 files) + 5.9 MB git history | **Yes.** Today it is the only copy |
| `infra/.env` | inside the repo, gitignored | small | **Yes, by hand.** 78 settings including every secret |
| The document corpus | `D:\MOF_DataSample` | 9.6 MB, 58 files | **Yes.** Not in git, not regenerable. See §4 |
| Brand fonts | `Application/frontend/assets/fonts/*.ttf` | 3 files | **Yes.** Licensed, gitignored |
| Container data | `D:\MOF_Installations\volumes` | 4.4 GB | Optional. See §5 |
| Langfuse ClickHouse | 2 named volumes in Docker's own disk | 4.1 GB | Optional — trace history only |
| DBeaver portable | `D:\MOF_Installations\tools` | 202 MB | No. Reinstall if wanted |
| Docker engine disk | `D:\MOF_Installations\docker-data` | 44 GB | No. Docker rebuilds it |

The 43 GB backup that `STATUS_AND_ROADMAP.md` §3 listed for cleanup
(`docker-data-backup-2026-09-13`) **no longer exists** — checked 2026-11-08.

### The repository has no remote

`git remote -v` returns nothing. The whole build exists as one directory on
one disk, with no clone and no backup. **Fix that before moving anything
else**: it turns the move into `git clone` plus three hand-copied files, and
it is the only step here that is worth doing even if the move never happens.

---

## 2. The short version

On the new device, once §3 has been done:

```powershell
git clone <remote> "D:\MOF Enjaz Chatbot"
```

Copy in the three untracked assets (§3), then:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1 -CheckOnly
```

```powershell
powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1
```

`scripts/bootstrap.ps1` checks the prerequisites and the four uncarried
assets, brings up all 14 containers, applies the 20 migrations and runs the
test suite. It **does not run the ingestion pipeline**, because Stage 1 is
billed per page; it prints that command for you to run deliberately (§5).

`-CheckOnly` changes nothing and starts nothing, so it is safe to run first,
and safe to re-run while fixing what it reports.

---

## 3. Preparing the move, on this machine

### 3.1 Push to a private remote

Private, not public. The repository itself holds no secrets — `.env` is
gitignored and `.env.example` carries empty values, pinned by
`tests/test_env_example.py` — but it does hold the full architecture of a
ministry system, and there is no reason for that to be public.

```bash
git remote add origin <your-private-remote-url>
```

```bash
git push -u origin master
```

⚠️ The branch here is `master`. Git's default for a new repository is now
`main`; if the remote expects `main`, rename before pushing rather than
ending up with both.

### 3.2 Do not commit the corpus

`D:\MOF_DataSample` is gitignored and must stay that way. §4 explains why
this is not merely a size decision.

### 3.3 Decide how the secrets travel

`infra/.env` holds 78 settings: both Postgres roles, the Qdrant key, Redis,
the whole Langfuse stack, all three Azure keys and `API_SESSION_SECRET`.

| Option | When it fits |
|---|---|
| **Copy the file by hand** | Two devices, one owner. Unglamorous and entirely adequate |
| **Azure Key Vault** | The moment this runs anywhere but a personal PC. Fits the all-Azure decision already made, and gives real rotation |
| SOPS / git-crypt | Makes the clone self-contained, but you then move an age or GPG key out of band instead. Relocates the problem |

Rotation is the argument for a vault, not tidiness: **all three Azure roles
share one resource and one key** (`START_HERE.md`, §22), so rotating it
today means editing `.env` on every device that exists.

⚠️ Never commit `.env`. `API_SESSION_SECRET` signs the session cookie —
anyone holding it can mint a session for any seeded username, which is the
AC09 boundary. It has no default anywhere on purpose, so sign-in refuses
rather than falling back.

---

## 4. The corpus is not all mock data

`D:\MOF_DataSample` holds 58 documents across 9 project folders. 45 are
fictional, generated for this build. **The 13 under `TEST-PROJECT` are
real** (`STATUS_AND_ROADMAP.md` §7): project charters for MOZN, Emdad,
JEDCO, Tanmiah and TMO, a منصة همة status export, and a formal NCGR-style
Arabic BRD for a PowerBI redesign.

**Do not push those to GitHub**, private repository or not. Once committed
they are in the history permanently, replicated to that provider's
infrastructure, and removable only by rewriting history. Whether real client
and ministry documents may leave the tenancy is a question for MOF, not a
convenience trade-off at move time.

Move the corpus as:

1. **A direct copy** — USB, network share, or the same disk if the drive is
   moving. Simplest, and keeps the material where it already is.
2. **A private Azure Blob container** — fits the all-Azure decision, keeps
   the documents inside the tenancy that governs them, and survives the
   device. A small fetch step would then belong beside the pipeline.

Git LFS works mechanically and changes nothing about the above.

---

## 5. The 4.4 GB of container data: carry or rebuild

The database is **not** something you need to carry. Migrations `001`–`020`
recreate and seed it: 9 projects, 4 users, 6 assignments, 58 document links,
and the registers (64 risks, 21 issues, 12 change requests, 27
deliverables). That is a deliberate property of how this was built, and it
is what makes the move small.

What migrations do not rebuild is the **extracted text** and the
**vectors**.

| | Rebuild it | Carry it |
|---|---|---|
| Postgres (`postgres-spm`, 82 MB) | Re-runs OCR: **~$1.50** for 58 documents / ~154 pages | Copy, and nothing is re-billed |
| Qdrant (4.0 GB) | Re-embeds: **under a cent** | Copy |
| Langfuse + Grafana (~4.2 GB) | Starts empty | Only if you want the old traces |

**Recommended: carry Postgres, rebuild Qdrant.** The 82 MB directory holds
the `document_content` OCR cache, which is the only expensive thing here.
Qdrant's 4.0 GB is almost entirely preallocated write-ahead-log files
(`open-1`, `open-2` per collection), not the 678 vectors, which are tiny —
so copying it moves mostly empty space to save a fraction of a cent.

⚠️ **Stop the stack before copying any data directory.** `docker compose
down` first. Copying a live Postgres data directory produces a corrupt one,
and it will not announce itself until something reads the wrong page.

```bash
cd infra && docker compose down
```

Then copy `D:\MOF_Installations\volumes\postgres-spm` to the same relative
place under the new machine's `DATA_ROOT`.

---

## 6. On the new device, in order

1. **Install Docker Desktop.** Point its disk at a non-`C:` drive before
   doing anything else. ⚠️ A Docker Desktop update can reset that to `C:`
   (`INFRASTRUCTURE.md` §4.4b). Set "Start Docker Desktop when you sign in"
   while you are in Settings — it is off on the original machine and nothing
   comes back after a reboot until Docker is opened by hand, the app
   included.
2. **Install Python 3.13** (the build machine runs 3.13.12).
3. **Clone the repository.**
4. **Copy in `infra/.env`**, then correct the three absolute paths:
   `DATA_ROOT`, `DOCUMENT_ROOT`, `DOCUMENT_ROOT_HOST`. On a device without a
   `D:` drive all three are wrong, and `DOCUMENT_ROOT` is the one that fails
   loudly — the pipeline refuses to run rather than marking 58 documents
   missing.
5. **Copy in the corpus** and the three `.ttf` files.
6. **Create the venv.** Never copy `.venv` — it hardcodes absolute paths.
   ```powershell
   python -m venv .venv
   ```
   ```powershell
   .venv\Scripts\python -m pip install -r requirements.txt
   ```
7. **Run the checks**, and fix whatever they report:
   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1 -CheckOnly
   ```
8. **Bring it up.**
   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1
   ```
9. **Build the knowledge base** — only now, and only deliberately. Free
   first:
   ```bash
   docker compose exec pipeline-worker python -m pipeline.ocr --dry-run
   ```
   If you carried Postgres, that reports `candidates : 0` and the run below
   re-bills nothing.
   ```bash
   docker compose exec pipeline-worker python -m pipeline.tasks ingest --wait
   ```
10. **Verify against §3 of `START_HERE.md`**, not against this file: 14
    containers, 20 migrations applied, 9 projects, 9 Qdrant collections /
    678 points, 762 tests, and a real sign-in at http://localhost:8000.

---

## 7. What is deliberately not part of this

**A container registry (GHCR).** You could push `mof-enjaz-pipeline` and
`mof-enjaz-chat` and pull them instead of building. `docker compose up -d
--build` takes a couple of minutes from the one `Dockerfile`, and a registry
adds auth, tags, and the chance of an image drifting from the source that
made it. Not worth it for two machines.

**Codespaces or a devcontainer.** The genuinely zero-setup option, and a
poor fit: 14 containers under Docker-in-Docker, a corpus that would have to
live in the cloud (§4), and it dissolves the "runs on the owner's own PC"
property that this demo partly rests on. The Azure endpoints would work fine
from a cloud host; everything else fights you.

**Moving the Azure resources.** Nothing here improves on the single shared
key (§22). The new device inherits the same single point of failure, and the
move is not the occasion to change it.
