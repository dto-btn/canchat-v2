import { readFileSync } from 'node:fs';
import { JavascriptLexer } from 'i18next-parser';
import { describe, expect, it } from 'vitest';

describe('Svelte translation extraction', () => {
	it.each([
		['workspace/Models.svelte', 'Import Models'],
		['workspace/Tools.svelte', 'Delete tool?'],
		['workspace/Prompts.svelte', 'Import Prompts'],
		['admin/Functions.svelte', 'Discover a function']
	])('extracts translations after localized links in %s', (component, key) => {
		const path = `src/lib/components/${component}`;
		const source = readFileSync(path, 'utf8');
		const keys = new JavascriptLexer().extract(source, path).map((entry) => entry.key);
		expect(keys).toContain(key);
	});
});
