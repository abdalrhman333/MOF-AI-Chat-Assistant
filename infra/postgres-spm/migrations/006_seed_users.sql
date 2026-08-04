-- Seed: 4 mock AD users from mock-data/spm_manifest.json

INSERT INTO users (username, full_name_ar, full_name_en, email) VALUES
('fahad.alqahtani', 'فهد بن سعد القحطاني', 'Fahad Saad Al-Qahtani', 'fahad.alqahtani@enjaz.gov.sa'),
('noura.alharbi', 'نورة بنت عبدالعزيز الحربي', 'Noura Abdulaziz Al-Harbi', 'noura.alharbi@enjaz.gov.sa'),
('abdullah.alshehri', 'عبدالله بن محمد الشهري', 'Abdullah Mohammed Al-Shehri', 'abdullah.alshehri@enjaz.gov.sa'),
('reem.aldosari', 'ريم بنت ناصر الدوسري', 'Reem Nasser Al-Dosari', 'reem.aldosari@enjaz.gov.sa')
ON CONFLICT (username) DO NOTHING;
