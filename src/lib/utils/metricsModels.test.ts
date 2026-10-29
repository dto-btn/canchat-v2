import { describe, it, expect } from 'vitest';
import { OTHER_FAMILY, getModelFamily, groupModelsByFamily } from './metricsModels';

const model = (id: string, name = id) => ({ id, name });
const family = (id: string, name = id) => getModelFamily(model(id, name));

describe('getModelFamily', () => {
	it.each([
		// GPT, including o-series names
		['GPT 4o', 'GPT'],
		['gpt-4o-2024-11-20', 'GPT'],
		['gpt-35-turbo', 'GPT'],
		['o3-mini', 'GPT'],
		['azure.o1', 'GPT'],
		// Cohere, including Command and the retired "poseidon" (Command-A)
		['Cohere Command-4', 'Cohere'],
		['command-4', 'Cohere'],
		['command-a-fp8-no-sd-tp2', 'Cohere'],
		['poseidon', 'Cohere'],
		['claude-3-5-sonnet', 'Claude'],
		['Gemini Flash 2.5', 'Gemini'],
		['gemini-2.5-flash', 'Gemini'],
		['Llama 3.2 Vision (USA/É-U)', 'Llama'],
		['hosted_vllm/ssc-dsai/gc-llm-llama-3-8b', 'Llama'],
		['meta-llama/llama-3.2-90b-vision-instruct', 'Llama'],
		['mistral-large', 'Mistral'],
		['deepseek-r1', 'DeepSeek'],
		['phi-4', 'Phi'],
		['qwen2.5-72b', 'Qwen'],
		// No family
		['hr-qa', OTHER_FAMILY],
		['management-taskings', OTHER_FAMILY]
	])('%s → %s', (id, expected) => {
		expect(family(id)).toBe(expected);
	});

	it('does not read "phi" inside another word', () => {
		expect(family('dolphin-mixtral')).toBe('Mistral');
		expect(family('sophia')).toBe(OTHER_FAMILY);
	});

	it('does not read "o1"-style names inside another word', () => {
		expect(family('hello1')).toBe(OTHER_FAMILY);
	});

	it('checks the id before the display name', () => {
		// The display name mentions GPT, but the id is a Llama model
		expect(family('llama-3-8b', 'GPT-style helper')).toBe('Llama');
	});

	it('falls back to the display name when the id matches no family', () => {
		expect(family('custom-hr', 'Claude HR Assistant')).toBe('Claude');
		expect(family('custom-hr', 'HR Assistant')).toBe(OTHER_FAMILY);
	});
});

describe('groupModelsByFamily', () => {
	it('groups in family order, with Other last and empty families left out', () => {
		const groups = groupModelsByFamily([
			model('hr-qa'),
			model('command-4'),
			model('GPT 4o'),
			model('gemini-2.5-flash'),
			model('gpt-4o-2024-11-20')
		]);

		expect(groups.map((g) => g.name)).toEqual(['GPT', 'Cohere', 'Gemini', OTHER_FAMILY]);
	});

	it('keeps the input order inside each group', () => {
		const [gpt] = groupModelsByFamily([model('GPT 4o'), model('gpt-4o-2024-11-20')]);

		expect(gpt.models.map((m) => m.id)).toEqual(['GPT 4o', 'gpt-4o-2024-11-20']);
	});

	it('returns no groups for no models', () => {
		expect(groupModelsByFamily([])).toEqual([]);
	});
});
