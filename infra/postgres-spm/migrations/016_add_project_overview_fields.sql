-- The five Project Overview fields (QA1.1) that SPM did not yet carry.
--
-- The requirement asks the assistant to produce, on request: project name,
-- status, owner, manager, completion %, budget, start date, end date, phase
-- and stage. Five of those already existed; these are the other five.
--
-- ⚠️ **Seeded to agree with the corpus, not invented alongside it.** The
-- manager and owner values below were read out of each project's own
-- charter before being written here. That matters more than it looks: the
-- assistant is instructed to surface disagreement between SPM and a
-- document rather than resolve it (SOLUTION_ARCHITECTURE.md §3.3), so a
-- name typed carelessly here would make the assistant report a conflict
-- that only exists because of this file.
--
-- Where a charter does not state a value, the column is left NULL rather
-- than filled with something plausible. "Not recorded in SPM" is a true
-- answer; a manufactured one is not — the same reasoning as §16b, which
-- refused to invent a last-updated timestamp the mock SPM never had.

ALTER TABLE projects ADD COLUMN IF NOT EXISTS owner_ar text;
ALTER TABLE projects ADD COLUMN IF NOT EXISTS owner_en text;
ALTER TABLE projects ADD COLUMN IF NOT EXISTS manager_ar text;
ALTER TABLE projects ADD COLUMN IF NOT EXISTS manager_en text;
ALTER TABLE projects ADD COLUMN IF NOT EXISTS completion_percent integer;
ALTER TABLE projects ADD COLUMN IF NOT EXISTS phase_ar text;
ALTER TABLE projects ADD COLUMN IF NOT EXISTS phase_en text;
ALTER TABLE projects ADD COLUMN IF NOT EXISTS stage_ar text;
ALTER TABLE projects ADD COLUMN IF NOT EXISTS stage_en text;

-- A percentage that is not a percentage is a data bug that reads as a
-- model bug once it reaches an answer.
ALTER TABLE projects DROP CONSTRAINT IF EXISTS projects_completion_range;
ALTER TABLE projects ADD CONSTRAINT projects_completion_range
  CHECK (completion_percent IS NULL
         OR (completion_percent >= 0 AND completion_percent <= 100));

UPDATE projects SET
  owner_ar = v.owner_ar, owner_en = v.owner_en,
  manager_ar = v.manager_ar, manager_en = v.manager_en,
  completion_percent = v.pct,
  phase_ar = v.phase_ar, phase_en = v.phase_en,
  stage_ar = v.stage_ar, stage_en = v.stage_en
FROM (VALUES
  -- code, owner (راعي المشروع), manager (مدير المشروع), completion %,
  -- phase (المرحلة — lifecycle), stage (الطور — where inside that phase)
  ('PRJ-01',
   NULL, NULL,
   'الأستاذ عبدالعزيز بن محمد الدوسري', 'Abdulaziz bin Mohammed Al-Dosari',
   62, 'التنفيذ', 'Execution', 'توسعة النطاق', 'Scope Expansion'),

  ('PRJ-02',
   NULL, NULL,
   'م. فيصل العتيبي', 'Eng. Faisal Al-Otaibi',
   48, 'التنفيذ', 'Execution', 'التكامل مع الأنظمة', 'Systems Integration'),

  ('PRJ-03',
   'أ. نورة بنت خالد العتيبي', 'Noura bint Khaled Al-Otaibi',
   NULL, NULL,
   35, 'التنفيذ', 'Execution', 'ترحيل البيانات', 'Data Migration'),

  ('PRJ-04',
   'أ. سارة بنت محمد القحطاني', 'Sarah bint Mohammed Al-Qahtani',
   'م. فيصل بن سعد العتيبي', 'Eng. Faisal bin Saad Al-Otaibi',
   100, 'الإغلاق', 'Closing', 'الإغلاق الإداري', 'Administrative Closure'),

  ('PRJ-05',
   'د. سلطان بن عبدالعزيز المطيري', 'Dr. Sultan bin Abdulaziz Al-Mutairi',
   'م. نورة بنت فهد العتيبي', 'Eng. Noura bint Fahd Al-Otaibi',
   21, 'التخطيط', 'Planning', 'تصميم المؤشرات', 'Indicator Design'),

  ('PRJ-06',
   NULL, NULL,
   'م. سلطان بن عبدالعزيز الدوسري', 'Eng. Sultan bin Abdulaziz Al-Dosari',
   74, 'التنفيذ', 'Execution',
   'اختبار قبول المستخدم', 'User Acceptance Testing'),

  ('PRJ-07',
   NULL, NULL,
   NULL, NULL,
   40, 'التنفيذ', 'Execution', 'إعادة تحديد النطاق', 'Scope Re-baselining'),

  ('PRJ-08',
   'أ. منيرة العتيبي', 'Munira Al-Otaibi',
   NULL, NULL,
   100, 'الإغلاق', 'Closing', 'الدروس المستفادة', 'Lessons Learned')
) AS v(code, owner_ar, owner_en, manager_ar, manager_en, pct,
       phase_ar, phase_en, stage_ar, stage_en)
WHERE projects.code = v.code;

-- TEST-PROJECT is deliberately untouched. It is a bag of thirteen unrelated
-- real documents filed as one "project" (STATUS_AND_ROADMAP.md §13b), not a
-- project with an owner, a manager or a completion percentage — and giving
-- it any would make the overview answer confidently wrong.
