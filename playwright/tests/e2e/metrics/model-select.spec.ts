import { test, expect } from '../../../src/fixtures/base-fixture';
import type { Locator, Page } from '@playwright/test';
import type { AdminPage } from '../../../src/pages/admin.page';

// Fixed model list so the dropdown looks the same in every environment.
// Shape matches GET /api/v1/metrics/models.
const MODELS = {
	active_models: [
		{ id: 'Cohere Command-4', name: 'Cohere Command-4' },
		{ id: 'Gemini Flash 2.5', name: 'Gemini Flash 2.5' },
		{ id: 'GPT 4o', name: 'GPT 4o' }
	],
	archived_models: [
		{ id: 'command-4', name: 'command-4' },
		{ id: 'gpt-4o-2024-11-20', name: 'gpt-4o-2024-11-20' },
		{ id: 'hr-qa', name: 'hr-qa' },
		{ id: 'poseidon', name: 'poseidon' }
	]
};

// Rows in the panel. Model rows are matched by their title (the model id),
// because the Metrics page reformats digits in visible text ("2024" → "2,024").
const groupToggle = (panel: Locator, name: string) =>
	panel.locator('button[aria-expanded]', { hasText: name }).first();
const modelRow = (panel: Locator, id: string) =>
	panel.locator(`button[aria-pressed][title="${id}"]`);

async function openModelAnalysis(adminPage: AdminPage) {
	const page: Page = adminPage.page;
	await page.route('**/api/v1/metrics/models', (route) =>
		route.fulfill({
			json: { models: [], ...MODELS }
		})
	);
	await adminPage.goto('/workspace/metrics');
	await page
		.getByRole('button', { name: adminPage.getTranslation('Model Analysis'), exact: true })
		.click();

	const trigger = page.locator('#model-select');
	await expect(trigger).toBeVisible();
	return { page, trigger, panel: page.locator('#model-select-panel') };
}

test.describe('Metrics: Model Analysis model dropdown', () => {
	test('METRICS-MODEL-TC001: groups Active and Archived models by family', async ({
		adminPage
	}) => {
		const { trigger, panel } = await openModelAnalysis(adminPage);

		// Default is the first model shown: the first Active family (GPT)
		await expect(trigger).toContainText('GPT 4o');

		await trigger.click();
		await expect(panel).toBeVisible();
		await expect(panel).toContainText(adminPage.getTranslation('Active'));

		// Active families, in family order
		const activeToggles = panel.locator('button[aria-expanded]');
		await expect(activeToggles).toHaveText([/GPT/, /Cohere/, /Gemini/, /Archived|Archivés/]);

		// Archived opens to its own family groups
		await groupToggle(panel, adminPage.getTranslation('Archived')).click();
		await expect(activeToggles).toHaveText([
			/GPT/,
			/Cohere/,
			/Gemini/,
			/Archived|Archivés/,
			/GPT/,
			/Cohere/,
			/Other|Autre/
		]);
	});

	test('METRICS-MODEL-TC002: arrow, Home and End keys move through the rows', async ({
		adminPage
	}) => {
		const { page, trigger, panel } = await openModelAnalysis(adminPage);

		// Down arrow on the trigger opens the panel and focuses the selected model
		await trigger.focus();
		await page.keyboard.press('ArrowDown');
		await expect(panel).toBeVisible();
		await expect(modelRow(panel, 'GPT 4o')).toBeFocused();
		await expect(modelRow(panel, 'GPT 4o')).toHaveAttribute('aria-pressed', 'true');

		// Rows: GPT toggle, GPT 4o, Cohere toggle, Gemini toggle, Archived toggle
		await page.keyboard.press('ArrowDown');
		await expect(groupToggle(panel, 'Cohere')).toBeFocused();

		await page.keyboard.press('Home');
		await expect(groupToggle(panel, 'GPT')).toBeFocused();

		await page.keyboard.press('End');
		await expect(groupToggle(panel, adminPage.getTranslation('Archived'))).toBeFocused();

		// Wraps around at both ends
		await page.keyboard.press('ArrowDown');
		await expect(groupToggle(panel, 'GPT')).toBeFocused();
		await page.keyboard.press('ArrowUp');
		await expect(groupToggle(panel, adminPage.getTranslation('Archived'))).toBeFocused();
	});

	test('METRICS-MODEL-TC003: Escape closes the panel and returns focus to the trigger', async ({
		adminPage
	}) => {
		const { page, trigger, panel } = await openModelAnalysis(adminPage);

		await trigger.focus();
		await page.keyboard.press('ArrowDown');
		await expect(panel).toBeVisible();
		await expect(trigger).toHaveAttribute('aria-expanded', 'true');

		await page.keyboard.press('Escape');
		await expect(panel).toBeHidden();
		await expect(trigger).toBeFocused();
		await expect(trigger).toHaveAttribute('aria-expanded', 'false');
	});

	test('METRICS-MODEL-TC004: a model can be picked with the keyboard', async ({ adminPage }) => {
		const { page, trigger, panel } = await openModelAnalysis(adminPage);

		await trigger.focus();
		await page.keyboard.press('Enter');
		await expect(modelRow(panel, 'GPT 4o')).toBeFocused();

		// Open the Cohere group and pick its model
		await page.keyboard.press('ArrowDown');
		await expect(groupToggle(panel, 'Cohere')).toBeFocused();
		await page.keyboard.press('Enter');
		await expect(groupToggle(panel, 'Cohere')).toHaveAttribute('aria-expanded', 'true');

		await page.keyboard.press('ArrowDown');
		await expect(modelRow(panel, 'Cohere Command-4')).toBeFocused();
		await page.keyboard.press('Enter');

		await expect(panel).toBeHidden();
		await expect(trigger).toContainText('Cohere Command-4');
		await expect(trigger).toBeFocused();
	});

	test('METRICS-MODEL-TC005: an archived model is tagged and reopens on its group', async ({
		adminPage
	}) => {
		const { page, trigger, panel } = await openModelAnalysis(adminPage);
		const archived = adminPage.getTranslation('Archived');

		// Archived → GPT → gpt-4o-2024-11-20, all by keyboard
		await trigger.focus();
		await page.keyboard.press('Enter');
		await page.keyboard.press('End');
		await page.keyboard.press('Enter');
		await page.keyboard.press('ArrowDown');
		await expect(panel.locator('button[aria-expanded]').nth(4)).toBeFocused();
		await page.keyboard.press('Enter');
		await page.keyboard.press('ArrowDown');
		await expect(modelRow(panel, 'gpt-4o-2024-11-20')).toBeFocused();
		await page.keyboard.press('Enter');

		await expect(panel).toBeHidden();
		await expect(trigger.locator('[title="gpt-4o-2024-11-20"]')).toBeVisible();
		await expect(trigger).toContainText(archived);

		// Reopening expands Archived and its GPT group, and focuses the selection
		await page.keyboard.press('ArrowDown');
		await expect(modelRow(panel, 'gpt-4o-2024-11-20')).toBeFocused();
		await expect(groupToggle(panel, archived)).toHaveAttribute('aria-expanded', 'true');
	});

	test('METRICS-MODEL-TC006: the panel closes when focus or a click leaves it', async ({
		adminPage
	}) => {
		const { page, trigger, panel } = await openModelAnalysis(adminPage);

		// Tabbing past the last row
		await trigger.click();
		await expect(panel).toBeVisible();
		await page.keyboard.press('End');
		await page.keyboard.press('Tab');
		await expect(panel).toBeHidden();

		// Clicking elsewhere on the page (the "Select Model:" label)
		await trigger.click();
		await expect(panel).toBeVisible();
		await page.locator('#model-select-label').click();
		await expect(panel).toBeHidden();
	});
});
