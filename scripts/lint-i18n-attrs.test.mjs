import { describe, expect, it } from 'vitest';
import { applyFixes, hasI18nBinding, lintContent } from './lint-i18n-attrs.mjs';

const messages = (content) => lintContent(content).map((issue) => issue.message);

describe('lint-i18n-attrs', () => {
	it('flags a hardcoded attribute on a line that also uses $i18n.t()', () => {
		expect(messages(`<input placeholder={$i18n.t('X')} title="Hardcoded" />`)).toEqual([
			'title="Hardcoded" should use {$i18n.t("Hardcoded")}'
		]);
	});

	it('ignores script, style and comment contents', () => {
		const content = [
			'<script>',
			'\tlet title = "Hello world";',
			"\tconst x = {'Some JS string'};",
			'</script>',
			'<!-- <button title="Commented out">x</button> -->',
			'<style>',
			'\t.a { content: "x"; }',
			'</style>'
		].join('\n');
		expect(lintContent(content)).toEqual([]);
	});

	it('reports correct positions and collapses attribute values that span lines', () => {
		const content = '<script>\n</script>\n<input\n\taria-label="Search\n\t\tmodels"\n/>';
		const [issue] = lintContent(content);
		expect(issue).toMatchObject({ line: 4, column: 2 });
		expect(applyFixes(content, [issue])).toContain(`aria-label={$i18n.t("Search models")}`);
	});

	it('produces valid fixes for values containing apostrophes', () => {
		const content = `<button title="You're here">{'Hello there'}</button>`;
		expect(applyFixes(content, lintContent(content))).toBe(
			`<button title={$i18n.t("You're here")}>{$i18n.t('Hello there')}</button>`
		);
	});

	it('detects whether a file defines i18n', () => {
		expect(hasI18nBinding(`const i18n = getContext('i18n');`)).toBe(true);
		expect(hasI18nBinding(`import { i18n } from '$lib/stores';`)).toBe(true);
		expect(hasI18nBinding(`<div>{name}</div>`)).toBe(false);
	});
});
