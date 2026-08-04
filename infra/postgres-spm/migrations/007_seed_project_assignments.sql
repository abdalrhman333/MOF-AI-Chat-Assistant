-- Seed: user-project assignments from mock-data/spm_manifest.json

INSERT INTO project_assignments (username, project_code) VALUES
('fahad.alqahtani', 'PRJ-01'),
('noura.alharbi', 'PRJ-02'),
('noura.alharbi', 'PRJ-05'),
('noura.alharbi', 'PRJ-06'),
('abdullah.alshehri', 'PRJ-03'),
('reem.aldosari', 'PRJ-04')
ON CONFLICT (username, project_code) DO NOTHING;
