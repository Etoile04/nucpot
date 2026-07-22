-- Migration: Enable pgvector extension
-- Date: 2026-07-23
-- Issue: NFM-1770 (parent: NFM-1764)
--
-- LightRAG vector storage requires the pgvector extension for vector
-- column types and similarity search operators.
-- Idempotent: IF NOT EXISTS guard ensures safe re-run.

CREATE EXTENSION IF NOT EXISTS vector;
