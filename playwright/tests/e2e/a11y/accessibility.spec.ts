import type { Page, TestInfo } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { test, expect } from '../../../src/fixtures/base-fixture';

const WCAG_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'best-practice'];

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

/**
 * Run aXe on the current page, log and attach the results to the HTML report,
 * and return only the critical + serious violations (the ones that fail the test).
 */
async function runAxe(page: Page, label: string, testInfo: TestInfo) {
	const results = await new AxeBuilder({ page }).withTags(WCAG_TAGS).analyze();

	logAxeResults(label, results);
	await testInfo.attach(`${label.toLowerCase().replace(/\s+/g, '-')}-axe.json`, {
		body: JSON.stringify(
			{ violations: results.violations, incomplete: results.incomplete },
			null,
			2
		),
		contentType: 'application/json'
	});

	return results.violations.filter((v) => v.impact === 'critical' || v.impact === 'serious');
}

// Tag with @a11y to allow running a11y tests in isolation
test.describe('Accessibility (aXe) @a11y', () => {
	test.setTimeout(60000);

	test('Auth page should have no critical accessibility violations', async ({
		guestPage
	}, testInfo) => {
		// The auth form only renders once the page's onMount sets `loaded`
		await expect(guestPage.page.getByRole('main')).toBeVisible();

		expect(await runAxe(guestPage.page, 'Auth Page', testInfo)).toEqual([]);
	});

	test('Chat page should have no critical accessibility violations', async ({
		userPage
	}, testInfo) => {
		await expect(userPage.page.locator('#chat-input')).toBeVisible();
		await userPage.dismissBlockingModal();

		expect(await runAxe(userPage.page, 'Chat Page', testInfo)).toEqual([]);
	});

	test('Settings modal should have no critical accessibility violations', async ({
		userPage
	}, testInfo) => {
		await userPage.openGlobalSettings();
		await expect(userPage.page.locator('div.modal')).toBeVisible();
		await userPage.waitToSettle(500);

		expect(await runAxe(userPage.page, 'Settings Modal', testInfo)).toEqual([]);
	});

	test('Workspace page should have no critical accessibility violations', async ({
		adminPage
	}, testInfo) => {
		await adminPage.goto('/workspace/models');
		await expect(adminPage.page.locator('a[href="/workspace/models"]').first()).toBeVisible();
		await adminPage.waitToSettle(500);

		expect(await runAxe(adminPage.page, 'Workspace Page', testInfo)).toEqual([]);
	});

	test('Admin settings page should have no critical accessibility violations', async ({
		adminPage
	}, testInfo) => {
		await adminPage.goto('/admin/settings');
		await expect(adminPage.page.locator('#admin-settings-tabs-container')).toBeVisible();
		await adminPage.waitToSettle(500);

		expect(await runAxe(adminPage.page, 'Admin Settings Page', testInfo)).toEqual([]);
	});
});
