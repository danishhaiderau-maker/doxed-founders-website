import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import {
  nativeSkillForWorkMode,
  nativeSkillReceipt,
  nativeSkillSystem,
  nativeSkillToolTurnsUsed,
} from '../upstream/overlay/src/vs/workbench/contrib/void/electron-main/llmMessage/founderNativeSkills';

describe('Founder native workflow skills', () => {
  it('attaches one versioned skill to every visible work mode', () => {
    const modes = ['ask', 'plan', 'build', 'debug', 'team'] as const;
    const skills = modes.map(nativeSkillForWorkMode);
    assert.deepEqual(skills.map(skill => skill.mode), modes);
    assert.ok(skills.every(skill => skill.version === 1));
    assert.equal(new Set(skills.map(skill => skill.id)).size, modes.length);
  });

  it('keeps Ask tool-free and Plan read-only', () => {
    assert.equal(nativeSkillForWorkMode('ask').toolPolicy, 'none');
    assert.equal(nativeSkillForWorkMode('ask').maxToolTurns, 0);
    assert.equal(nativeSkillForWorkMode('plan').toolPolicy, 'read_only');
  });

  it('makes Build, Debug, and Team evidence-bearing editing-owner workflows', () => {
    for (const mode of ['build', 'debug', 'team'] as const) {
      const skill = nativeSkillForWorkMode(mode);
      assert.equal(skill.toolPolicy, 'editing_owner');
      assert.ok(skill.evidence.length >= 3);
      assert.ok(skill.output.length >= 4);
      assert.ok(skill.maxToolTurns > 0);
    }
  });

  it('produces a stable prompt contract and visible receipt', () => {
    const skill = nativeSkillForWorkMode('debug');
    const system = nativeSkillSystem(skill);
    assert.match(system, /founder\.root-cause-debug@1/);
    assert.match(system, /Maximum tool turns/);
    assert.match(system, /Required evidence/);
    assert.match(system, /Required result sections/);
    assert.match(nativeSkillReceipt(skill, 2), /tools 2\/14/);
  });

  it('counts tool turns only after the latest founder request', () => {
    assert.equal(
      nativeSkillToolTurnsUsed([
        { role: 'user' },
        { role: 'tool' },
        { role: 'assistant' },
        { role: 'user' },
        { role: 'assistant' },
        { role: 'tool' },
        { role: 'tool' },
      ]),
      2,
    );
  });
});
