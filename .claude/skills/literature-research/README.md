# literature-research Claude Code Skill

This package combines a Claude-authored semantic research loop with the deterministic
`my-search-harness` V1 Runtime. Claude decides what to research, how to synthesize it,
whether to request completion, and how to write. Python enforces lifecycle authority,
stable references, revision-safe commands, provenance, accounting, persistence,
citations, and artifact validation.

`REPORT_WRITING_GUIDE.md` governs organization, prose, synthesis, and readability.
`RESEARCH_INTEGRITY_GUIDE.md` separately governs claim strength, evidence boundaries,
comparison validity, and benchmark interpretation.

## Install a standalone export

1. Copy or install the `literature-research` directory into your Skills folder.
2. Create the Skill-local runtime environment once:

   ```text
   python scripts/setup.py
   ```

   On Windows PowerShell, Linux, and macOS, use the local Python command
   available in that environment. If your Windows installation exposes Python
   as `py`, use `py scripts/setup.py`.

3. Configure the DeepXiv credential once (interactive, no echo):

   ```text
   python scripts/harness.py configure-token
   ```

   The Harness stores the credential in your user-local configuration directory
   and re-injects it into the process environment at bootstrap. This is a
   convenience for persistent local configuration, not a requirement: an
   explicit `DEEPXIV_TOKEN` environment variable still works and takes
   precedence. Never put the token in a `.env` file, a JSON input file, the
   Skill directory, the workspace, or any script.

4. Verify the installation:

   ```text
   python scripts/doctor.py --workspace PATH
   ```

Runtime workspace is user or project data and must remain outside the Skill
installation directory. A project may instead use its own `workspace/` directory.

Return to the project where the research data should live before starting Claude
Code; do not use a relative `workspace/` while the Skill installation directory is
the current directory. Then invoke:

```text
/literature-research 调研 LLM KV Cache 优化的主要技术路线和最新进展
```

The project-local version is discovered from `.claude/skills/literature-research` and
uses the repository Runtime source. A standalone export uses `runtime/src` bundled by
the release packager. In both modes, invoke all research commands through the single
Python entrypoint `python "<SKILL_DIR>/scripts/harness.py"`; do not manipulate the
workspace files directly.

## Export from the repository

From the repository root:

```bash
python scripts/package_skill.py
```

The command recreates ignored `dist/literature-research/` from the tracked Skill and the
single authoritative `src/my_search_harness/` source tree. Generated `dist` content is
not committed.

## Layout

```text
literature-research/
├── SKILL.md
├── README.md
├── references/
├── scripts/
└── runtime/
    ├── requirements.txt
    └── src/my_search_harness/
```

See `references/RUNTIME_API.md` for command schemas and
`references/RESEARCH_PROTOCOL.md` for operating guidance. Writing and integrity remain
separate authorities in their respective reference guides.
