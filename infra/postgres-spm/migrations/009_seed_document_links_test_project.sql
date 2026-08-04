-- Seed: 13 real document links for TEST-PROJECT. doc_type/language/
-- contains_table assessed from actually reading each file's content
-- (python-docx/python-pptx/pdfplumber extraction) during triage.
-- NULL = genuinely unknown (e.g. the scanned PDF has no extractable text
-- yet — that's exactly what the OCR pipeline is for).
INSERT INTO document_links (project_code, filename, file_path, doc_type, language, contains_table, indexing_status) VALUES
('TEST-PROJECT', 'SDD_AI_Project_Charter_Generator_OnPrem.docx', 'D:\MOF_DataSample\TEST-PROJECT\SDD_AI_Project_Charter_Generator_OnPrem.docx', 'Software Design Document', 'English', TRUE, 'pending'),
('TEST-PROJECT', '__وثيقة المتطلبات التفصيلية - ميثاق المشروع.docx', 'D:\MOF_DataSample\TEST-PROJECT\__وثيقة المتطلبات التفصيلية - ميثاق المشروع.docx', 'Business Requirements Document', 'Arabic', TRUE, 'pending'),
('TEST-PROJECT', 'Tanmiah_GenAI_POC_Proposal_Technical 1.pptx', 'D:\MOF_DataSample\TEST-PROJECT\Tanmiah_GenAI_POC_Proposal_Technical 1.pptx', 'Technical Proposal', 'mixed', NULL, 'pending'),
('TEST-PROJECT', '_نموذج الأثر الاستباقي  2026  -  v2.pdf', 'D:\MOF_DataSample\TEST-PROJECT\_نموذج الأثر الاستباقي  2026  -  v2.pdf', 'Status Dashboard Export', 'Arabic', TRUE, 'pending'),
('TEST-PROJECT', 'TMO001001_DATA WAREHOUSE, BI & REPORTING BUSINESS INTELLIGENCE_Project Charter_16 July 2025_v0.1 1.docx', 'D:\MOF_DataSample\TEST-PROJECT\TMO001001_DATA WAREHOUSE, BI & REPORTING BUSINESS INTELLIGENCE_Project Charter_16 July 2025_v0.1 1.docx', 'Project Charter', 'English', TRUE, 'pending'),
('TEST-PROJECT', '_المنهجية التقنية للمشروع.pdf', 'D:\MOF_DataSample\TEST-PROJECT\_المنهجية التقنية للمشروع.pdf', 'Technical Methodology', NULL, NULL, 'pending'),
('TEST-PROJECT', 'MOZN Project Charter-V0.1.docx', 'D:\MOF_DataSample\TEST-PROJECT\MOZN Project Charter-V0.1.docx', 'Project Charter', 'English', TRUE, 'pending'),
('TEST-PROJECT', 'Emdad - Project Charter - V1 1.docx', 'D:\MOF_DataSample\TEST-PROJECT\Emdad - Project Charter - V1 1.docx', 'Project Charter', 'English', TRUE, 'pending'),
('TEST-PROJECT', 'JEDCO002001 - Project Charter - Employee internal Portal V 1.0 .docx', 'D:\MOF_DataSample\TEST-PROJECT\JEDCO002001 - Project Charter - Employee internal Portal V 1.0 .docx', 'Project Charter', 'English', TRUE, 'pending'),
('TEST-PROJECT', 'وثيقة المتطلبات التفصيلية PowerBI وإعادة التصميم 01-07-2026.pdf', 'D:\MOF_DataSample\TEST-PROJECT\وثيقة المتطلبات التفصيلية PowerBI وإعادة التصميم 01-07-2026.pdf', 'Business Requirements Document', 'Arabic', TRUE, 'pending'),
('TEST-PROJECT', 'client_template.pptx', 'D:\MOF_DataSample\TEST-PROJECT\client_template.pptx', 'Risk Management Plan Template (Generic)', 'English', NULL, 'pending'),
('TEST-PROJECT', 'IC-Project-Risk-Management-Plan-Example-11690_WORD.docx', 'D:\MOF_DataSample\TEST-PROJECT\IC-Project-Risk-Management-Plan-Example-11690_WORD.docx', 'Risk Management Plan Template (Generic)', 'English', TRUE, 'pending'),
('TEST-PROJECT', 'sample-project-charter.pdf', 'D:\MOF_DataSample\TEST-PROJECT\sample-project-charter.pdf', 'Project Charter Template (Generic)', 'English', TRUE, 'pending')
ON CONFLICT (project_code, filename) DO NOTHING;
