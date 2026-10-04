# Intelligent GitHub Issue Refinement & Duplicate Detection System

## Problem Statement

Open-source repositories frequently receive issue submissions that suffer from ambiguous descriptions, poor grammar, or duplicate problem reports. Maintainers spend substantial time manually triaging, clarifying, and closing repetitive reports, which creates maintainer fatigue and delays resolution of genuine defects.

## Project Objective

The objective of this project is to build an Agentic AI system that:
1. Cleans and refines incoming GitHub issue drafts into clear, structured, and professional technical bug/feature reports without hallucinating details.
2. Identifies duplicate and near-duplicate reports against historical repository issues using semantic retrieval (RAG) and LLM-driven reasoning.
3. Provides an actionable recommendation (`DUPLICATE`, `POSSIBLE_DUPLICATE`, or `READY_TO_PUBLISH`) along with references to the contributing historical issues.

## Planned High-Level Workflow

1. **Issue Ingestion & Sync**: Retrieve and incrementally synchronize historical issues from a target GitHub repository.
2. **Data Normalization & Embedding**: Normalize issue content, generate embeddings, and store them in a local vector database.
3. **Issue Intake & Refinement**: Accept an unrefined issue draft, correcting spelling and grammar while strictly preserving technical terminology and facts.
4. **Candidate Retrieval (RAG)**: Retrieve top semantically similar historical issues from the vector store.
5. **Agentic Reasoning & Classification**: Evaluate retrieved candidates with an LLM agent to classify the submission as `DUPLICATE`, `POSSIBLE_DUPLICATE`, or `READY_TO_PUBLISH`.
6. **Transparent Reporting**: Present the refined issue text, classification decision, and supporting historical issue links.

## Current Development Status

**Status: Stage 1B — Incremental GitHub Issue Synchronization**

Stage 1B implements incremental synchronization for GitHub issues, enabling differential updates and content hashing to prepare for efficient downstream RAG embedding and vector indexing.

## Issue Ingestion & Incremental Synchronization

### Why Incremental Synchronization Matters for RAG

In later stages, historical issues will be embedded using an embedding model and stored in a vector database (`pgvector`) for similarity search. Embedding large volumes of text is computationally expensive and introduces unnecessary latency if repeated unconditionally.

Incremental synchronization solves this by:
- Generating a deterministic SHA-256 content hash over semantic text fields (`title` and `body`).
- Comparing newly fetched issues against `data/sync_state.json`.
- Classifying each issue as `NEW`, `CHANGED`, or `UNCHANGED`.
- Ensuring only new or genuinely modified issues are slated for embedding/re-indexing, while metadata-only GitHub updates (such as label or timestamp changes) do not trigger redundant semantic reprocessing.

### Generated Local Files

Runtime files generated in `data/` are excluded from version control via `.gitignore`:
- `data/issues.json`: The local dataset containing normalized GitHub issues (deduplicated by issue ID and ordered deterministically by issue number).
- `data/sync_state.json`: Synchronization metadata tracking repository name, `last_sync_time`, and a dictionary of synchronized issues with their latest `updated_at` timestamps and `content_hash` digests.

### Running Issue Collection & Synchronization

Ensure your virtual environment is active and `GITHUB_TOKEN` is exported in your environment:

1. **Initial Issue Collection (Stage 1A)**:
   ```bash
   python -m ingestion.issue_collector
   ```
   Inspect available options:
   ```bash
   python -m ingestion.issue_collector --help
   ```

2. **Incremental Issue Synchronization (Stage 1B)**:
   ```bash
   python -m ingestion.synchronizer
   ```
   Inspect available options:
   ```bash
   python -m ingestion.synchronizer --help
   ```
