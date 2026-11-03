import { readFile, writeFile } from 'node:fs/promises';
const root = new URL('../native-visualizer/', import.meta.url);
const prompt = await readFile(new URL('system-prompt.md', root), 'utf8');
const header = '---\nname: native-visualizer\ndescription: Create diagrams, data charts and administrator-enabled miniapps with native Open WebUI renderers.\n---\n\n';
await writeFile(new URL('SKILL.md', root), header + prompt);
const preset = [{
  id: 'native-visualizer',
  base_model_id: 'SELECT_YOUR_BASE_MODEL',
  name: 'Native Visualizer',
  meta: {
    description: 'Native Mermaid and Vega-Lite visualization; HTML Artifacts require administrator opt-in. Preview 0.1.0.',
    tags: [{name: 'visualization'}],
    capabilities: {vision: false, file_upload: false, web_search: false, image_generation: false, code_interpreter: false, builtin_tools: false}
  },
  params: {system: prompt},
  is_active: true
}];
await writeFile(new URL('model-preset.json', root), JSON.stringify(preset, null, 2) + '\n');
console.log('Generated Skill and model preset from canonical instructions.');
