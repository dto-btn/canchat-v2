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
 * Usage: node scripts/lint-i18n-attrs.mjs [--fix]
 */

import fs from 'node:fs';
import path from 'node:path';

const ROOT = process.argv[2] || '.';
const SHOULD_FIX = process.argv.includes('--fix');
let exitCode = 0;

// Attribute check: attr="hardcoded" (not using $i18n.t())
const ATTRS = ['aria-label', 'placeholder', 'title'];
const ATTR_PATTERN = new RegExp(
  `(${ATTRS.join('|')})\\s*=\\s*"([^"\\$<>{]+?)"`, 'g'
);

// Template text check: {'Hardcoded'} or {`Hardcoded`} (not inside $i18n.t())
const TEXT_PATTERN = /\{\s*['"`]([^'"`]{3,}?)['"`]\s*\}/g;

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
  } catch { /* skip unreadable */ }
  return results;
}

function lintFile(filePath) {
  const content = fs.readFileSync(filePath, 'utf-8');
  const lines = content.split('\n');
  const issues = [];

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const trimmed = line.trim();

    // Skip lines that already use $i18n.t()
    if (line.includes('$i18n.t(')) continue;

    // Skip non-template sections
    if (trimmed.startsWith('<script') || trimmed.startsWith('</script>') ||
        trimmed.startsWith('<style') || trimmed.startsWith('</style>') ||
        trimmed.startsWith('//') || trimmed.startsWith('/*') ||
        trimmed.startsWith('*') || trimmed.startsWith('<!--')) continue;

    // Check 1: Hardcoded attrs
    ATTR_PATTERN.lastIndex = 0;
    let match;
    while ((match = ATTR_PATTERN.exec(line)) !== null) {
      const [, attr, value] = match;
      if (value.trim().length >= 2) {
        issues.push({
          line: i + 1,
          column: line.indexOf(match[0]) + 1,
          message: `${attr}="${value}" should use {$i18n.t('${value}')}`,
          fix: () => content.replace(match[0], `${attr}={$i18n.t('${value}')}`)
        });
      }
    }

    // Check 2: Hardcoded template text {'Hello'} or {`Hello`}
    TEXT_PATTERN.lastIndex = 0;
    while ((match = TEXT_PATTERN.exec(line)) !== null) {
      const text = match[1].trim();

      // --- Exclusion rules for non-translatable strings ---
      if (text.length <= 2) continue;

      // Numbers, units, math expressions
      if (/^[\d\s,.%+\-—–/\\:;()]+$/.test(text)) continue;

      // Template literals with ${...} dynamic expressions (not translatable)
      if (/\$\{.+?\}/.test(text)) continue;

      // URLs and URL-like paths
      if (/^https?:\/\//.test(text)) continue;
      if (/^\/[\w./-]+(?:\?|$)/.test(text)) continue; // /path/to/file.png
      if (/(?:encodeURIComponent|encodeURI)/.test(text)) continue;

      // CSS selectors starting with #
      if (/^#/.test(text)) continue;

      // Tailwind/CSS class patterns
      // Matches: size-5, -translate-y-[1px], w-full, dark:bg-gray-900, shadow, dark:hover:bg-gray-100
      const TAILWIND_TOKEN = /^-?(?:[a-z]+:)*[a-z]+(?:-\[?[a-z0-9./]+]?)*$/;
      const tokens = text.split(/\s+/);
      if (tokens.every(t => TAILWIND_TOKEN.test(t))) continue;

      // Example/placeholder text with URLs or paths
      if (/e\.g\.|example|https?:\/\/|\.com|\.org|\.net|\.gov/.test(text)) continue;

      // Emoji shortcodes :reaction_name:
      if (/^:.+:$/.test(text)) continue;

      // Single words (likely variables, status values, CSS class parts)
      if (/^[a-z][a-z0-9]*$/.test(text) && text.length < 12) continue;
      if (/^[A-Z_]+$/.test(text)) continue; // CONSTANT_NAMES
      if (/^[a-z]+[-_][a-z0-9-]+$/.test(text)) continue; // kebab-case, snake_case

      // Debug/logging strings with commas or specific patterns
      if (/^[,'"`:;{}\[\]()]+/.test(text)) continue;

      const quote = match[0].includes('"') ? '"' : "'";
      issues.push({
        line: i + 1,
        column: line.indexOf(match[0]) + 1,
        message: `Hardcoded text '${text}' should use {$i18n.t('${text}')}`,
        fix: () => content.replace(match[0], `{$i18n.t(${quote}${text}${quote})}`)
      });
    }
  }

  return issues;
}

async function main() {
  const files = walkSvelteFiles(ROOT);
  let totalIssues = 0;
  let totalFixed = 0;

  console.log(`Checking ${files.length} .svelte files for i18n violations...\n`);

  for (const filePath of files) {
    const issues = lintFile(filePath);
    if (issues.length === 0) continue;

    totalIssues += issues.length;

    const relPath = path.relative(ROOT, filePath);
    console.log(`  ✗ ${relPath} (${issues.length} issues)`);
    for (const issue of issues) {
      console.log(`      ${issue.line}:${issue.column}  ${issue.message}`);
    }

    if (SHOULD_FIX) {
      let content = fs.readFileSync(filePath, 'utf-8');
      for (const issue of issues) {
        const newContent = issue.fix();
        if (newContent !== content) {
          content = newContent;
          totalFixed++;
        }
      }
      fs.writeFileSync(filePath, content, 'utf-8');
    }
  }

  if (totalIssues === 0) {
    console.log('  ✓ No i18n violations found.');
  } else {
    exitCode = 1;
    console.log(`\n${totalIssues} violation(s) found.${SHOULD_FIX ? ` ${totalFixed} auto-fixed.` : ' Use --fix to auto-fix.'}`);
  }

  process.exit(exitCode);
}

main().catch(err => {
  console.error(err);
  process.exit(1);
});
