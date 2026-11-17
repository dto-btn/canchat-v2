#!/usr/bin/env node
/**
 * Lint check for i18n violations in .svelte templates.
 * Fails with exit code 1 if any issues are found.
 *
 * Checks:
 *   1. Hardcoded aria-label/placeholder/title="text"
 *      → must be attr={$i18n.t('text')}
 *   2. Hardcoded template text: {'Hello'} or {`Hello`}
 *      → must be {$i18n.t('Hello')}
 *
 * <script>, <style> and HTML comment blocks are ignored.
 *
 * Usage: node scripts/lint-i18n-attrs.mjs [dir] [--fix]   (dir defaults to src)
 */

import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

// Attribute check: attr="hardcoded" (not using $i18n.t()); must follow whitespace so
// data-title="..." or JS like `foo.title="..."` is not matched.
const ATTRS = ['aria-label', 'placeholder', 'title'];
const ATTR_PATTERN = new RegExp(`(?<=\\s)(${ATTRS.join('|')})\\s*=\\s*"([^"\\$<>{]+?)"`, 'g');

// Template text check: {'Hardcoded'} or {`Hardcoded`} (not inside $i18n.t())
const TEXT_PATTERN = /\{\s*['"`]([^'"`]{3,}?)['"`]\s*\}/g;

const NON_TEMPLATE_BLOCKS =
	/<script\b[\s\S]*?<\/script\s*>|<style\b[\s\S]*?<\/style\s*>|<!--[\s\S]*?-->/gi;
const TAILWIND_TOKEN = /^-?(?:[a-z]+:)*[a-z]+(?:-\[?[a-z0-9./]+]?)*$/;
const I18N_BINDING = /\bi18n\s*=\s*getContext\(|import\s*\{[^}]*\bi18n\b[^}]*\}/;

/**
 * Blank out script/style/comment blocks while keeping newlines, so offsets and
 * line numbers still map onto the original file.
 */
export function maskNonTemplate(content) {
	return content.replace(NON_TEMPLATE_BLOCKS, (block) => block.replace(/[^\n]/g, ' '));
}

function positionOf(content, index) {
	const before = content.slice(0, index);
	const line = before.split('\n').length;
	return { line, column: index - before.lastIndexOf('\n') };
}

function isTranslatableText(text) {
	if (text.length <= 2) return false;

	// Numbers, units, math expressions
	if (/^[\d\s,.%+\-—–/\\:;()]+$/.test(text)) return false;

	// Template literals with ${...} dynamic expressions
	if (/\$\{.+?\}/.test(text)) return false;

	// URLs and URL-like paths
	if (/^https?:\/\//.test(text)) return false;
	if (/^\/[\w./-]+(?:\?|$)/.test(text)) return false;
	if (/(?:encodeURIComponent|encodeURI)/.test(text)) return false;

	// CSS selectors starting with #
	if (/^#/.test(text)) return false;

	// Tailwind/CSS class lists, e.g. size-5, -translate-y-[1px], dark:hover:bg-gray-100
	if (text.split(/\s+/).every((token) => TAILWIND_TOKEN.test(token))) return false;

	// Example/placeholder text with URLs or paths
	if (/e\.g\.|example|https?:\/\/|\.com|\.org|\.net|\.gov/.test(text)) return false;

	// Emoji shortcodes :reaction_name:
	if (/^:.+:$/.test(text)) return false;

	// Single words, CONSTANT_NAMES, kebab-case, snake_case
	if (/^[a-z][a-z0-9]*$/.test(text) && text.length < 12) return false;
	if (/^[A-Z_]+$/.test(text)) return false;
	if (/^[a-z]+[-_][a-z0-9-]+$/.test(text)) return false;

	// Debug/logging strings starting with punctuation
	if (/^[,'"`:;{}[\]()]+/.test(text)) return false;

	return true;
}

export function lintContent(content) {
	const template = maskNonTemplate(content);
	const issues = [];
	let match;

	ATTR_PATTERN.lastIndex = 0;
	while ((match = ATTR_PATTERN.exec(template)) !== null) {
		const [, attr, value] = match;
		// Collapse whitespace so values wrapped across lines become a single i18n key
		const key = value.replace(/\s+/g, ' ').trim();
		if (key.length < 2) continue;
		const literal = JSON.stringify(key);
		issues.push({
			...positionOf(content, match.index),
			message: `${attr}="${key}" should use {$i18n.t(${literal})}`,
			start: match.index,
			end: match.index + match[0].length,
			replacement: `${attr}={$i18n.t(${literal})}`
		});
	}

	TEXT_PATTERN.lastIndex = 0;
	while ((match = TEXT_PATTERN.exec(template)) !== null) {
		const text = match[1].trim();
		if (!isTranslatableText(text)) continue;

		// TEXT_PATTERN excludes quotes from the captured text, so single quotes are always safe here
		issues.push({
			...positionOf(content, match.index),
			message: `Hardcoded text '${text}' should use {$i18n.t('${text}')}`,
			start: match.index,
			end: match.index + match[0].length,
			replacement: `{$i18n.t('${text}')}`
		});
	}

	return issues.sort((left, right) => left.start - right.start);
}

export function applyFixes(content, issues) {
	let fixed = content;
	for (const issue of [...issues].sort((left, right) => right.start - left.start)) {
		fixed = fixed.slice(0, issue.start) + issue.replacement + fixed.slice(issue.end);
	}
	return fixed;
}

export function hasI18nBinding(content) {
	return I18N_BINDING.test(content);
}

function walkSvelteFiles(dir) {
	const results = [];
	try {
		const entries = fs.readdirSync(dir, { withFileTypes: true });
		for (const entry of entries) {
			if (entry.name === 'node_modules' || entry.name.startsWith('.')) continue;
			const fullPath = path.join(dir, entry.name);
			if (entry.isDirectory()) {
				results.push(...walkSvelteFiles(fullPath));
			} else if (entry.name.endsWith('.svelte')) {
				results.push(fullPath);
			}
		}
	} catch {
		/* skip unreadable */
	}
	return results;
}

function main(argv) {
	const root = argv.find((arg) => arg !== '--fix') || 'src';
	const shouldFix = argv.includes('--fix');
	const files = walkSvelteFiles(root);
	let totalIssues = 0;
	let totalFixed = 0;
	const missingI18n = [];

	console.log(`Checking ${files.length} .svelte files for i18n violations...\n`);

	for (const filePath of files) {
		const content = fs.readFileSync(filePath, 'utf-8');
		const issues = lintContent(content);
		if (issues.length === 0) continue;

		totalIssues += issues.length;

		const relPath = path.relative(process.cwd(), filePath);
		console.log(`  ✗ ${relPath} (${issues.length} issues)`);
		for (const issue of issues) {
			console.log(`      ${issue.line}:${issue.column}  ${issue.message}`);
		}

		if (shouldFix) {
			fs.writeFileSync(filePath, applyFixes(content, issues), 'utf-8');
			totalFixed += issues.length;
			if (!hasI18nBinding(content)) missingI18n.push(relPath);
		}
	}

	if (totalIssues === 0) {
		console.log('  ✓ No i18n violations found.');
		return 0;
	}

	console.log(
		`\n${totalIssues} violation(s) found.${shouldFix ? ` ${totalFixed} auto-fixed.` : ' Use --fix to auto-fix.'}`
	);

	if (shouldFix) {
		if (missingI18n.length > 0) {
			console.log(
				"\n⚠ These files now use $i18n but don't define it; add `const i18n = getContext('i18n');`:"
			);
			for (const relPath of missingI18n) console.log(`    ${relPath}`);
		}
		console.log(
			'\nRun `npm run i18n:parse` to add the new keys to the locale files, then translate them.'
		);
	}

	return 1;
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? '').href) {
	process.exit(main(process.argv.slice(2)));
}
