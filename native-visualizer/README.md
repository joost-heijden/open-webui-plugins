# Native Visualizer

**0.1.0 preview** — visual explanations using Open WebUI's existing renderers. No Python plugin, frontend patch or custom container image.

| Need | Route | Output |
| --- | --- | --- |
| Processes, decisions, architecture | Mermaid | Diagram in the conversation |
| Numerical comparisons and trends | Vega-Lite | Chart in the conversation |
| Miniapps, sliders, local calculations | Artifacts, administrator opt-in | HTML in the existing side panel |
| Illustrations and infographics | Optional native image generation | Image, requiring your configured provider |

This is an instruction package, not a new rendering engine. The default is **data and diagrams**. Interactive HTML requires an explicit administrator opt-in and existing permission for JavaScript artifacts. It does not reproduce Inline Visualizer's streaming or chat bridges.

## Install (choose one route)

### Existing model: simplest route

1. Open Workspace → Models and edit or clone your chosen model.
2. Append [system-prompt.md](system-prompt.md) to its system prompt, preserving your organization's existing instructions.
3. Save and start a new chat. No tools need to be enabled for this package.

### Import a model preset

1. Download [model-preset.json](model-preset.json).
2. In the JSON file, replace `SELECT_YOUR_BASE_MODEL` with the exact ID of a model available on your instance. Do not put API credentials in this file.
3. In Workspace → Models, use Import JSON and select the file. If `native-visualizer` already exists, use the existing-model route or change the preset ID to avoid a conflict.
4. Inspect the imported model's base model and system prompt before starting a new chat.

The JSON is an array of model configurations, following upstream's current import structure. Actual import into an Open WebUI deployment remains to be verified; if your version rejects it, use the existing-model route.

### Optional Skill

Import [SKILL.md](SKILL.md) using Workspace → Skills, then explicitly select it in the chat (for example using a $ mention where supported). It contains the same instructions. Do not install both the Skill and model preset for the same chat. If lazy loading would require a tool call through your gateway, use the system-prompt route instead.

## Enable interactive Artifacts

An administrator can append this line after the instructions, only where existing policy permits JavaScript artifacts:

> Native Visualizer administrator configuration: ARTIFACTS mode is enabled. Use self-contained HTML/CSS/JavaScript for requested interactive miniapps.

Use Open WebUI's existing artifact detection/preview settings. Do not enable same-origin access or change sandbox/CSP settings for this package. If scripts cannot run, keep the default mode.

## Try it

- “Draw a Mermaid flowchart: receive request, check completeness, approve or request details.”
- “Make a bar chart: January 120, February 180, March 150. Unit: requests. Include the source table.”
- In Artifacts mode: “Make a self-contained capacity calculator with a slider and a reset button. No external resources.”

Use the [acceptance checklist](examples/acceptance.md) and supplied [bar](examples/bar.vega-lite.json), [line](examples/line.vega-lite.json), [donut](examples/donut.vega-lite.json), [diagram](examples/process.mmd) and [interactive artifact](examples/capacity.html). All example data is fictional. For a deterministic renderer check, ask the model to return an example unchanged inside its matching code fence.

## Compatibility and validation

| Check | Status |
| --- | --- |
| Instruction/preset consistency and example policy checks | Automated local and GitHub checks |
| Actual model behavior and data fidelity | Requires manual acceptance in your environment |
| Open WebUI import, Skill loading, Artifacts and SVG rendering | Not yet tested in a live Open WebUI instance |
| Gateway acceptance | Not yet tested; no guarantee is made |

Target standard Open WebUI versions with native Mermaid/Vega-Lite rendering and Artifacts. For Vega, use an updated release with upstream's external-resource restrictions (fixed in 0.11.0); this is not a claim that all later versions have been tested. Record exact tested versions in this table after running acceptance.

Sources: [Models](https://docs.openwebui.com/features/workspace/models/), [Skills](https://docs.openwebui.com/features/workspace/skills/), [Artifacts](https://docs.openwebui.com/features/chat-conversations/chat-features/code-execution/artifacts/), [Mermaid](https://docs.openwebui.com/features/chat-conversations/chat-features/code-execution/mermaid/), [native renderer](https://github.com/open-webui/open-webui/blob/main/src/lib/utils/index.ts), [Vega resource-loading fix](https://github.com/advisories/GHSA-rffm-9q57-q649).

## Limits and troubleshooting

- A prompt is not an enforced allowlist. Review outputs and respect deployment policies. See [security](../SECURITY.md).
- Charts use values provided by the model; check them against the source. SVG output does not retain a live Vega application.
- Code shown instead of a chart: check the exact fence language (`mermaid`, `vega-lite`, `html`), JSON syntax and platform preview settings.
- Blank artifact or inactive controls: test the standalone example, then inspect the browser error under existing policy. Use a static route if JavaScript is blocked.
- Skill not applied: explicitly select it or use the model system prompt.
- Gateway refusal: capture the redacted error and route used. Do not obfuscate content or change permissions to bypass it.
- Image generation is optional; no provider, keys or image tool is configured by this package. Generated charts need manual checking of labels and proportions.

## Update or remove

Read [CHANGELOG.md](CHANGELOG.md), then replace the appended instructions or reimport the Skill. Keep local administrator configuration separately. To remove, delete the preset/Skill or remove the appended prompt. Existing chat history is unaffected; start a new chat after updating.

## Development

Edit `system-prompt.md`, then run `node scripts/build.mjs` from the repository root and `npm test`. Users only install Markdown/JSON configuration; development scripts are not imported into Open WebUI.
