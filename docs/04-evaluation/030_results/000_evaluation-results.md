# Results

This section presents the results of the evaluation.

We performed evaluations using popular AI coding agents in representative scenarios — different
agents, different programming languages, and different codebases — to show that the results are
not specific to a single setup.

Most published runs used a **JetBrains IDE plugin** backend that is no longer shipped; findings about symbolic tools vs. built-ins still apply to the current **LSP-only** Serena. Re-run the [evaluation prompt](../020_prompts/010_evaluation-prompt.md) on your stack to compare.

- [Claude Code (Opus 4.6) on a large Python codebase](010_cc_on_tianshou)
- [Codex (GPT 5.4) on a Java codebase](020_codex_on_jbplugin)
- [Copilot CLI (GPT 5.4) on a large, multi-language monorepo](030_copilot_cli_on_ente.md)
- [GLM 5.1 in Claude Code](040_glm_on_tianshou)
- [JetBrains Junie with Opus 4.6](050_junie_plugin_on_tianshou.md)

You can run your own evaluation on a project of your choice by reusing our
[evaluation prompt](../020_prompts/010_evaluation-prompt.md).
