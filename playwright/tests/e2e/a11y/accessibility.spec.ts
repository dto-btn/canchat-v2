import { test, expect } from '../../../src/fixtures/base-fixture';
import AxeBuilder from '@axe-core/playwright';

/**
 * Summarize all aXe violations for logging.
 * Groups by impact level and prints each violation with affected nodes.
 */
function logAxeResults(label: string, results: Awaited<ReturnType<AxeBuilder['analyze']>>) {
	const byImpact = {
		critical: results.violations.filter((v) => v.impact === 'critical'),
		serious: results.violations.filter((v) => v.impact === 'serious'),
		moderate: results.violations.filter((v) => v.impact === 'moderate'),
		minor: results.violations.filter((v) => v.impact === 'minor')
	};

	const totalNodes = results.violations.reduce((sum, v) => sum + v.nodes.length, 0);

	console.log(
		`\n[a11y] ${label} — ${results.violations.length} rules violated (${totalNodes} nodes affected)`
	);
	console.log(
		`  Critical: ${byImpact.critical.length} | Serious: ${byImpact.serious.length} | Moderate: ${byImpact.moderate.length} | Minor: ${byImpact.minor.length}`
	);
	console.log(
		`  Passes: ${results.passes.length} | Incomplete (needs review): ${results.incomplete.length}`
	);

	for (const [impact, violations] of Object.entries(byImpact)) {
		if (violations.length === 0) continue;
		console.log(`\n  [${impact.toUpperCase()}]`);
		for (const v of violations) {
			console.log(`    - ${v.id}: ${v.description}`);
			console.log(`      Help: ${v.helpUrl}`);
			for (const node of v.nodes) {
				console.log(`        Target: ${JSON.stringify(node.target)}`);
			}
		}
	}

	if (results.incomplete.length > 0) {
		console.log('\n  [INCOMPLETE — needs manual review]');
		for (const v of results.incomplete) {
			console.log(`    - ${v.id}: ${v.description} (${v.nodes.length} nodes)`);
		}
	}
}

test.describe('Accessibility (aXe)', () => {
	test.setTimeout(60000);

	// Tag with @a11y to allow running a11y tests in isolation
	test('Auth page should have no critical accessibility violations @a11y', async ({
		guestPage
	}) => {
		const results = await new AxeBuilder({ page: guestPage.page })
			.withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'best-practice'])
			.analyze();

		logAxeResults('Auth Page', results);

		// Fail only on critical + serious
		const criticalViolations = results.violations.filter(
			(v) => v.impact === 'critical' || v.impact === 'serious'
		);

		expect(criticalViolations).toEqual([]);
	});

	test('Chat page should have no critical accessibility violations @a11y', async ({ userPage }) => {
		const results = await new AxeBuilder({ page: userPage.page })
			.withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'best-practice'])
			.analyze();

		logAxeResults('Chat Page', results);

		// Fail only on critical + serious
		const criticalViolations = results.violations.filter(
			(v) => v.impact === 'critical' || v.impact === 'serious'
		);

		expect(criticalViolations).toEqual([]);
	});
});
