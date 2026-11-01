import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import ts from 'typescript';

// Use the project's installed compiler; no browser or extra test dependencies.
const source = readFileSync(new URL('../src/localmedia/uiLogic.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2020 },
});
const { localDateBoundary, settingsPatch } = await import(
  'data:text/javascript;base64,' + Buffer.from(outputText).toString('base64')
);

for (const [zone, day, start, end] of [
  ['Asia/Shanghai', '2026-11-01', '2026-10-31T16:00:00Z', '2026-11-01T16:00:00Z'],
  ['America/New_York', '2026-03-08', '2026-03-08T05:00:00Z', '2026-03-09T04:00:00Z'],
  ['America/New_York', '2026-11-01', '2026-11-01T04:00:00Z', '2026-11-02T05:00:00Z'],
]) {
  test(`local date range includes the entire calendar day: ${zone} ${day}`, () => {
    const previous = process.env.TZ;
    process.env.TZ = zone;
    try {
      const after = localDateBoundary(day);
      const before = localDateBoundary(day, true);
      assert.equal(after, Date.parse(start) / 1000);
      assert.equal(before, Date.parse(end) / 1000);
      assert.ok(new Date(`${day}T00:01:00`).getTime() / 1000 >= after);
      assert.ok(new Date(`${day}T23:59:59`).getTime() / 1000 < before);
    } finally {
      if (previous === undefined) delete process.env.TZ;
      else process.env.TZ = previous;
    }
  });
}

test('offline resource changes retain saved models and credentials', () => {
  const saved = {model:'saved-vision', summary_model:'saved-text', embedding_model:'saved-embedding',
    codex_effort:'high', api_key_configured:true, embedding_key_configured:true,
    active_profile:'active-space', video_frame_mode:'quality', max_cpu_cores:0};
  const form = {...saved, video_frame_mode:'fast', max_cpu_cores:2, api_key:'', embedding_key:''};
  assert.deepEqual(settingsPatch(form, saved), {video_frame_mode:'fast', max_cpu_cores:2});
});

test('initial offline setup can save local settings before choosing any model', () => {
  assert.deepEqual(settingsPatch({model:'',embedding_model:'',video_frame_mode:'fast'},
    {model:'',embedding_model:'',video_frame_mode:'quality'}), {video_frame_mode:'fast'});
});
