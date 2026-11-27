import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { createInstance } from 'i18next';
import { writable } from 'svelte/store';
import { createServer } from 'vite';

const navigation = new Map<string, string>();

test.beforeAll(async () => {
	const server = await createServer({
		server: { middlewareMode: true, watch: null, hmr: false }
	});
	try {
		const { user } = await server.ssrLoadModule('/src/lib/stores/index.ts');
		user.set({ id: 'alex', name: 'Alex', role: 'user', profile_image_url: '/user.png' });
		const i18n = createInstance();
		await i18n.init({ lng: 'en-GB', fallbackLng: 'en-GB', resources: {} });
		for (const path of ['layout/Navbar', 'channel/Navbar', 'app/AppSidebar']) {
			const { default: component } = await server.ssrLoadModule(
				`/src/lib/components/${path}.svelte`
			);
			navigation.set(
				path,
				component.render(
					{ showModelSelector: false, selectedModels: [] },
					{ context: new Map([['i18n', writable(i18n)]]) }
				).html
			);
		}
	} finally {
		await server.close();
	}
});

for (const path of ['layout/Navbar', 'channel/Navbar', 'app/AppSidebar']) {
	test(`${path} has no nested interactive controls or missing image alternatives`, async ({
		page
	}) => {
		const html = navigation.get(path);
		if (html === undefined) {
			throw new Error(`Missing rendered navigation: ${path}`);
		}
		await page.setContent(html);
		if (path !== 'app/AppSidebar') {
			const menu = page.getByRole('button', { name: 'User Menu', exact: true });
			await expect(menu).toHaveCount(1);
			await expect(menu.locator('button')).toHaveCount(0);
			await expect(menu.locator('img')).toHaveAttribute('alt', '');
		}
		const results = await new AxeBuilder({ page })
			.withRules(['nested-interactive', 'image-alt'])
			.analyze();
		expect(results.violations).toEqual([]);
	});
}
