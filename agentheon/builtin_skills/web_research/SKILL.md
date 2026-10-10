---
name: web_research
version: 1.0.0
description: Researches a question on the public web and answers with cited sources.
when_to_use: Questions about current events, products, companies, releases, or anything that needs up-to-date public information.
tools: [web_search, fetch_page, current_time]
require_sources: true
max_steps: 10
---
## Instructions

1. Search with `web_search` using a short, specific query. Search again with different words if the
   first results are weak.
2. Open the two or three most relevant results with `fetch_page` and read them. Prefer primary
   sources (official sites, documentation, release notes) over aggregators.
3. If the question depends on today's date, call `current_time` first.
4. Answer in a few short paragraphs or a table. Put the direct answer first.
5. Cite every URL you relied on in `sources`. If sources disagree, say so.
6. If you cannot find a reliable answer, say what you found and what is still uncertain.
