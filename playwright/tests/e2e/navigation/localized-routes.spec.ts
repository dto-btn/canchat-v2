import { expect, test, type Page } from '@playwright/test';

async function mockBackend(page: Page, role: 'admin' | 'user' = 'admin') {
	await page.addInitScript(() => {
		if (!sessionStorage.getItem('test-locale-initialized')) {
			localStorage.setItem('locale', 'en-GB');
			sessionStorage.setItem('test-locale-initialized', 'true');
		}
	});
	await page.route('**/ws/**', (route) => route.abort());
	await page.route('**/api/**', async (route) => {
		const request = route.request();
		const path = new URL(request.url()).pathname;
		const origin = request.headers().origin;
		const headers = origin
			? {
					'access-control-allow-origin': origin,
					'access-control-allow-credentials': 'true',
					'access-control-allow-headers': '*',
					'access-control-allow-methods': 'GET, POST, PUT, DELETE, OPTIONS'
				}
			: {};
		if (request.method() === 'OPTIONS') return route.fulfill({ status: 204, headers });

		let json: unknown = [];
		if (path === '/api/config') {
			json = { name: 'Test', default_locale: 'en-GB', features: { enable_websocket: false } };
		} else if (path === '/api/v1/auths/' || path === '/api/v1/auths/refresh') {
			json = {
				id: 'test',
				name: 'Test',
				email: 'test@example.invalid',
				role,
				token: 'test',
				expires_at: 4102444800,
				permissions: { workspace: {} }
			};
		} else if (path.startsWith('/api/v1/terms/status')) {
			json = { accepted_at: '2026-01-01' };
		} else if (path === '/api/models') {
			json = { data: [] };
		} else if (path.includes('/users/user/settings')) {
			json = { ui: {} };
		} else if (path.includes('/config')) {
			json = {};
		}
		await route.fulfill({ status: 200, json, headers });
	});
}

test('switches workspace URLs without losing unsaved input or URL state', async ({ page }) => {
	await mockBackend(page);
	await page.goto('/fr/espace-travail/invites/creer?x=1#test');
	const title = page.locator('form input').first();
	await expect(title).toBeVisible();
	await expect(page.locator('html')).toHaveAttribute('lang', 'fr-CA');
	await title.fill('Unsaved text');

	await page.getByRole('button', { name: 'EN', exact: true }).click();
	await expect(page).toHaveURL(/\/en\/workspace\/prompts\/create\?x=1#test$/);
	await expect(page.locator('html')).toHaveAttribute('lang', 'en-GB');
	await expect(title).toHaveValue('Unsaved text');

	await page.getByRole('button', { name: 'FR', exact: true }).click();
	await expect(page).toHaveURL(/\/fr\/espace-travail\/invites\/creer\?x=1#test$/);
	await expect(title).toHaveValue('Unsaved text');
	await page.goBack();
	await expect(page).toHaveURL(/\/en\/workspace\/prompts\/create\?x=1#test$/);
	await expect(page.locator('html')).toHaveAttribute('lang', 'en-GB');
	await expect(title).toHaveValue('Unsaved text');
	await page.goForward();
	await expect(page).toHaveURL(/\/fr\/espace-travail\/invites\/creer\?x=1#test$/);
	await expect(page.locator('html')).toHaveAttribute('lang', 'fr-CA');
	await page.reload();
	await expect(title).toBeVisible();
	await expect(page.locator('html')).toHaveAttribute('lang', 'fr-CA');
});

test('localizes admin links and keeps legacy URLs working', async ({ page }) => {
	await mockBackend(page);
	await page.goto('/en/admin/functions');
	await expect(page.locator('a[href="/en/admin/functions/create"]')).toBeVisible();
	await page.getByRole('button', { name: 'FR', exact: true }).click();
	await expect(page).toHaveURL(/\/fr\/admin\/fonctions$/);
	await expect(page.locator('a[href="/fr/admin/fonctions/creer"]')).toBeVisible();
	await page.locator('a[href="/fr/admin/fonctions/creer"]').click();
	await expect(page).toHaveURL(/\/fr\/admin\/fonctions\/creer$/);

	await page.goto('/admin/functions');
	await expect(page.locator('a[href="/fr/admin/fonctions/creer"]')).toBeVisible();
});

test('enforces workspace permissions on translated paths', async ({ page }) => {
	await mockBackend(page, 'user');
	await page.goto('/fr/espace-travail/modeles');
	await expect(page).toHaveURL(/\/$/, { timeout: 30000 });
});
