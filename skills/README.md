# Your skills go here

This folder is on `SKILLS_DIRS` by default. Create one subfolder per skill, each holding a
`SKILL.md`:

```
skills/
  churn_analyst/
    SKILL.md
```

Scaffold one with:

```bash
agentheon new-skill churn_analyst
agentheon check                     # validates front matter and tool names
```

Only `*/SKILL.md` is loaded, so other files in here (including this README) are ignored.

The skills that ship with Agentheon live in `agentheon/builtin_skills/`, and the worked example
lives in `examples/skills/`. A skill defined here with the same `name` as a builtin overrides it.
