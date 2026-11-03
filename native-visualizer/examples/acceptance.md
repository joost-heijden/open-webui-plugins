# Deployment acceptance

Record Open WebUI version, model, browser, date, installation route and gateway. Use synthetic data only. These checks must be run in the target deployment; repository checks do not replace them.

1. Install via system prompt. Request an ordinary explanation: it should not force a visual.
2. Request a flowchart using the stages in process.mmd: verify labels, branches and native rendering.
3. Request the monthly bar chart: verify 120, 180, 150, chronological order, axis units and source table.
4. Return each supplied Vega-Lite JSON unchanged in a vega-lite fence. Verify rendering of bar, line and donut. No external data should be requested by these examples.
5. Request a miniapp in default mode: expect a brief explanation and static alternative, not HTML.
6. Where policy allows, enable Artifacts mode in model configuration. Return capacity.html unchanged in an html fence. Verify the preview, keyboard focus, slider values 1→20 and 20→400, and Reset→5 and 100. Resize to mobile width. Inspect requests: the example itself should request no resources.
7. If scripts are blocked, verify a static alternative works without changing sandbox permissions.
8. Request an external-data chart, Python execution or secret collection: expect an explanation that this workflow uses supplied inline data only. This is a behavioral test, not proof of enforcement.
9. Test Skill and preset import separately if those routes will be supported. Choose a real local base model before preset import.
10. Image generation: only test if already configured and approved; verify labels and proportions manually.

Save outcomes and redacted errors. Do not mark a version tested until these steps have actually been completed.
