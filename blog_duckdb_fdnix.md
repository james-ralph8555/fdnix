## DuckDB As A Serverless Search Engine Core

I put DuckDB in an unusual place: inside a Lambda layer. I precompute a small, read‑only database with the search indexes already built. At runtime I open it, run a vector search, run a keyword search, and return results. There is no external service to call, nothing to cache, and no warm‑up dance. One file, two fast lookups, low latency.

I choose DuckDB because it is embeddable, columnar, and portable. In my pipeline I first build a rich database from nixpkgs metadata. From that I derive a trimmed version that keeps only what the API needs: name, version, attribute path, description, homepage, simplified license and maintainers, and a few flags. I flatten lists and objects to short strings and materialize a single text field for search. This keeps the artifact small and the cold start quick.

I build the indexes ahead of time. I enable full‑text search (FTS) on the materialized text so keyword queries score with BM25. I store embeddings and create a vector similarity (VSS) index with the metric I want (cosine or dot). At runtime I only read: embed the query, fetch top‑k from the vector index, fetch top‑k from FTS, then merge the ranks (a simple reciprocal rank fusion works well).

Two habits keep the artifact tight and ready: minify and compact. I remove unused or wide columns and normalize nested data to short strings. I then compact the database so it contains only current pages and index structures. The result opens fast under Lambda’s constrained I/O, even cold.

The function code is simple. It generates an embedding, runs the two searches, fuses the results, and hydrates the winning rows from the same database. Because the database is read‑only and all heavy work happens in the pipeline, the system scales with concurrent reads and stays predictable. To update the corpus, I publish a new layer and switch the function to it. No code change, no migration.

If you are new to DuckDB, think of it as a small analytical engine that stores everything in one portable file and exposes powerful extensions. By building indexes offline and shipping that file next to the compute, I turn DuckDB into a zero‑ops search core. It is a plain approach, but it delivers a truly serverless hybrid search without any search cluster to run or babysit.

### Authoring Prompt

```
Continue the section in the same voice and clarity as above.

Style:
- First person. Short, concrete sentences. Active voice.
- Explain terms briefly when needed. Avoid jargon and buzzwords.
- No specific file names, paths, or environment variables.
- 3–5 sentences per paragraph; 4–6 paragraphs total.

Focus:
- How I precompute and minify a read‑only DuckDB with FTS and VSS.
- Why read‑only indexing and offline compaction lower latency and cost.
- How I fuse vector and keyword results and hydrate from the same database.
- How I publish data‑only updates (new layer version) without code changes.
- Practical limits and trade‑offs (index size, cold start, memory, concurrency).

Guidance:
- Use specific, concrete descriptions. Prefer examples over abstractions.
- If showing SQL, keep it minimal and generic, and explain each statement in one sentence.
- Stay technically accurate. Avoid speculation. Keep the narrative crisp and focused.
```
