# Contributing

Keep each plugin self-contained in a top-level folder. Include a README with installation, requirements, removal, examples, limitations and compatibility evidence. Add a CHANGELOG and update the root catalog. Do not create empty future plugin folders.

Use fictional data in examples. Never include keys, organization settings or chat exports. Explain any new network requests, script execution or tool requirements. Do not claim compatibility without recording a real test.

Native Visualizer's canonical instructions live in `native-visualizer/system-prompt.md`. Run `node scripts/build.mjs` after editing them to regenerate the Skill and preset. Run `npm test` before submitting. Development uses Node.js 22+; users do not need Node.js.

Use plugin-specific release tags such as `native-visualizer-v0.1.0`. Describe changes and validation in pull requests. Include Open WebUI version and reproduction steps in bug reports.
