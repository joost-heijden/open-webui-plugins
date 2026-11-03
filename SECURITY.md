# Security and privacy

Native Visualizer is a set of instructions and configuration. It does not install a Python tool, enforce a schema on model responses, or bypass gateway policy. Prompt instructions are not a security boundary.

Artifacts may execute model-generated JavaScript in Open WebUI's iframe. Use them only under your existing organizational policy and platform sandbox/CSP settings. Do not enable same-origin access or relax security settings for this package. The supplied example needs no access to the parent chat, storage, APIs or external libraries.

The default mode requests Mermaid and Vega-Lite only. All chart data should be embedded as values. This does not guarantee that a model follows the instruction. Check source data and outputs. Your existing model provider still receives the conversation under your deployment's rules; self-contained examples do not make inference offline.

Image generation requires a separately approved provider and can introduce additional data transmission and cost. It is optional and not configured by this package.

For a vulnerability, use GitHub's private vulnerability reporting if enabled. Otherwise contact the repository owner privately before sharing exploit details. Ordinary bugs can be reported as issues with synthetic reproduction data.
