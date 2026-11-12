-- A tenth project, PRJ-09, assigned to noura.alharbi.
--
-- WHY THIS EXISTS
--   Added on 2026-11-12 to exercise the path nothing had exercised: a
--   project arriving AFTER the corpus was built. Every other project was
--   seeded at the start, so "a new project appears, its documents are
--   ingested, and exactly one user can see it" had never been run end to
--   end. Qdrant creates the collection on first index, Postgres gets its
--   rows here, and row-level security needs no change -- the policies in
--   013/019 are per TABLE, not per project, so a new project row is
--   covered the moment it exists. That last claim is the one worth
--   re-checking rather than trusting: tests/test_rls.py asks the catalog.
--
-- ⚠️ THE SOURCE DOCUMENT IS NOT AN ENJAZ DOCUMENT.
--   It is a Dubai Land Department RFP -- a different country, ministry and
--   domain, marked CONFIDENTIAL by its author. It is here because the
--   pipeline was built to be "dynamic, not static to any specific case"
--   (STATUS_AND_ROADMAP §7) and this is the strongest available test of
--   that: 55 pages of dense procurement English, nothing like the seeded
--   corpus. It is NOT demo material for the Enjaz client, and the corpus
--   stays out of git as it always has.
--
-- WHAT IS DELIBERATELY NULL
--   budget_sar and planned_end_date. The RFP asks vendors to propose cost
--   and runs "4 calendar months from contract signing", which is not
--   signed -- so there is no approved budget and no planned end date to
--   record. Inventing either would put a figure in the system of record
--   that no document supports, which is the one thing this assistant must
--   never do. The assistant already answers "not recorded in SPM" for
--   missing fields, so this also exercises that path honestly.

INSERT INTO projects (
    code, name_ar, name_en, description_ar, description_en, status,
    start_date, planned_end_date, budget_sar,
    sponsoring_department_ar, sponsoring_department_en,
    owner_ar, owner_en, manager_ar, manager_en,
    completion_percent, phase_ar, phase_en, stage_ar, stage_en
) VALUES (
    'PRJ-09',
    'منصة الشفافية العقارية المتكاملة',
    'Integrated Real Estate Transparency Platform (IRETP)',
    'منصة بوابتين لبيانات القطاع العقاري: بوابة عامة متعددة اللغات للمستثمرين تضم لوحات تفاعلية وخرائط ووكيل ذكي للبيانات، ومنصة داخلية تشمل نظام الإنذار المبكر للمخاطر وتقييم أداء المطورين. الهدف المعلن رفع تصنيف الشفافية العقارية وتعزيز ثقة المستثمرين. المشروع في مرحلة الطرح والمنافسة.',
    'A two-pillar real estate data platform: a multilingual public portal for investors with interactive dashboards, a GIS map and an AI data agent, and an internal platform carrying an Early Warning Risk System and developer performance rating. Its stated objective is to advance the jurisdiction''s standing in the JLL Global Real Estate Transparency Index and reinforce investor confidence. Currently at the request-for-proposal stage.',
    'on-track',
    DATE '2026-04-01',          -- the RFP's own issue date
    NULL,                       -- see the note above
    NULL,                       -- see the note above
    'دائرة الأراضي والأملاك',
    'Dubai Land Department (DLD)',
    NULL, NULL,                 -- the RFP names no project owner
    NULL, NULL,                 -- nor a project manager
    0,
    'الطرح والمنافسة', 'Procurement',
    'تقييم العروض', 'Proposal Evaluation'
);

-- The assignment is the access boundary: without this row the project is
-- invisible to every user, which is what PRJ-07, PRJ-08 and TEST-PROJECT
-- are left as. One row, one user.
INSERT INTO project_assignments (username, project_code)
VALUES ('noura.alharbi', 'PRJ-09');
