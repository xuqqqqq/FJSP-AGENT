# LightRAG Generated Cards

This directory stores Markdown knowledge cards generated from LightRAG retrieval.

Cards are organized by problem family and retrieval stage:

```text
knowledge/rag_generated_cards/<family_id>/<stage>/<stage>_<cache_key>.md
knowledge/rag_generated_cards/<family_id>/manifest.json
```

These cards are runtime-generated knowledge assets. Once created, later rounds
reuse the card directly instead of querying LightRAG again for the same family,
stage, tag set, query template, and retrieval configuration.
