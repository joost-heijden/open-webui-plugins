import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile, readdir} from 'node:fs/promises';
const root = new URL('../', import.meta.url);
const read = (path) => readFile(new URL(path, root), 'utf8');

test('distributable instructions are synchronized and preset is import-shaped', async () => {
  const prompt = await read('native-visualizer/system-prompt.md');
  const skill = await read('native-visualizer/SKILL.md');
  assert.equal(skill.split('---\n\n')[1], prompt);
  const presets = JSON.parse(await read('native-visualizer/model-preset.json'));
  assert.equal(presets.length, 1);
  assert.equal(presets[0].params.system, prompt);
  assert.equal(presets[0].base_model_id, 'SELECT_YOUR_BASE_MODEL');
  assert.equal(presets[0].meta.capabilities.code_interpreter, false);
  assert.equal(presets[0].meta.capabilities.builtin_tools, false);
});

test('numerical examples embed values and reference existing fields', async () => {
  for (const file of ['bar', 'line', 'donut']) {
    const chart = JSON.parse(await read(`native-visualizer/examples/${file}.vega-lite.json`));
    assert.ok(chart.data.values.length > 0);
    for (const channel of Object.values(chart.encoding)) {
      if (channel.field) for (const row of chart.data.values) assert.ok(channel.field in row);
    }
    const inspect = (value) => {
      if (!value || typeof value !== 'object') return;
      for (const [key, child] of Object.entries(value)) {
        assert.ok(!['url', 'href', 'expr', 'calculate', 'signal', 'params'].includes(key), `Unexpected ${key}`);
        inspect(child);
      }
    };
    inspect(chart);
  }
  const bar = JSON.parse(await read('native-visualizer/examples/bar.vega-lite.json'));
  assert.deepEqual(bar.data.values.map(row => row.requests), [120,180,150]);
});

test('interactive artifact computes slider changes and reset without host APIs', async () => {
  const html = await read('native-visualizer/examples/capacity.html');
  assert.doesNotMatch(html, /\b(fetch|XMLHttpRequest|WebSocket|localStorage|eval)\s*[.(]|\b(parent|top)\s*\.|https?:\/\/|<script[^>]+src=/i);
  const vm = await import('node:vm');
  const elements = Object.fromEntries(['people','count','capacity','utilization','formula','reset'].map(id => [id, {value: id === 'people' ? '5' : '', handlers: {}, addEventListener(event, fn) {this.handlers[event] = fn;}}]));
  vm.runInNewContext(html.match(/<script>([\s\S]*?)<\/script>/)[1], {document: {getElementById: id => elements[id]}});
  assert.equal(elements.capacity.textContent, '100 requests per day');
  elements.people.value = '20'; elements.people.handlers.input();
  assert.equal(elements.capacity.textContent, '400 requests per day');
  elements.people.value = '1'; elements.people.handlers.input();
  assert.equal(elements.utilization.value, 20);
  elements.people.value = 'NaN'; elements.people.handlers.input();
  assert.equal(elements.utilization.value, 20);
  elements.reset.handlers.click();
  assert.equal(elements.people.value, '5');
  assert.equal(elements.capacity.textContent, '100 requests per day');
});

test('local documentation links resolve', async () => {
  async function walk(directory) {
    for (const entry of await readdir(new URL(directory, root), {withFileTypes:true})) {
      if (entry.name === '.git') continue;
      const path = directory + entry.name;
      if (entry.isDirectory()) await walk(path + '/');
      else if (path.endsWith('.md')) {
        const body = await read(path);
        for (const match of body.matchAll(/\]\(([^\s)]+)\)/g)) {
          if (/^(https?:|#)/.test(match[1])) continue;
          const destination = new URL(match[1].split('#')[0], new URL(path, root));
          await readFile(destination).catch(async error => {
            if (error.code !== 'EISDIR') throw new Error(`${path}: missing ${match[1]}`);
          });
        }
      }
    }
  }
  await walk('');
});
