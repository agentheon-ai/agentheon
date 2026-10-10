---
name: release_tracker
version: 1.0.0
description: Tracks releases of open-source projects on GitHub and summarizes what changed.
when_to_use: Questions about the latest version, recent releases, changelogs or release cadence of an open-source project.
tools: [github_releases, web_search]
required_inputs: [project]
require_sources: true
max_steps: 8
---
## Instructions

1. Work out the GitHub repository (owner/name) for the project. If unsure, use `web_search`.
2. Call `github_releases` for that repository.
3. Answer with a small table: version, date, and the two or three most important changes.
4. If asked to compare projects, call `github_releases` once per project and compare release dates
   and cadence.
5. Cite the release page URLs.
