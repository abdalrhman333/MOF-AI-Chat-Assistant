"""Ingestion pipeline for the MOF Enjaz Chatbot.

Stages (see Documentation/STATUS_AND_ROADMAP.md 5):
    0. scan   — walk the document root, hash files, upsert document_links
    1. ocr    — Document Intelligence prebuilt-layout -> document_content
    2. chunk  — chunker.py, then embed
    3. index  — upsert vectors into per-project Qdrant collections
"""
