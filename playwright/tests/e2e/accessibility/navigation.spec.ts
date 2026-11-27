import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { createInstance } from 'i18next';
import { writable } from 'svelte/store';
import { createServer } from 'vite';

const navigation = new Map<string, string>();
const controls = new Map<string, string>();
const controlFixtures = [
	{
		path: 'common/FileItem',
		props: { name: 'Report', type: 'file', size: 1024, dismissible: true }
	},
	{ path: 'common/SensitiveInput', props: {} },
	{ path: 'common/Image', props: {} },
	{ path: 'common/Checkbox', props: { ariaLabel: 'Vision', state: 'checked' } },
	{
		path: 'layout/Navbar',
		props: {
			showModelSelector: false,
			selectedModels: [],
			shareEnabled: true,
			chat: { id: 'test' }
		}
	},
	{ path: 'workspace/Knowledge/ItemMenu', props: {} },
	{ path: 'workspace/Knowledge/KnowledgeBase/AddContentMenu', props: {} },
	{ path: 'workspace/Models/Knowledge', props: {} },
	{ path: 'chat/MessageInput/InputMenu', props: {} }
];

test.beforeAll(async () => {
	const server = await createServer({
		server: { middlewareMode: true, watch: null, hmr: false }
	});
	try {
		const { default: en } = await server.ssrLoadModule(
			'/src/lib/i18n/locales/en-GB/translation.json'
		);
		const { default: fr } = await server.ssrLoadModule(
			'/src/lib/i18n/locales/fr-CA/translation.json'
		);
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
		user.set({ id: 'alex', name: 'Alex', role: 'admin', profile_image_url: '/user.png' });
		for (const locale of ['en-GB', 'fr-CA']) {
			await i18n.init({
				lng: locale,
				fallbackLng: 'en-GB',
				returnEmptyString: false,
				resources: { 'en-GB': { translation: en }, 'fr-CA': { translation: fr } }
			});
			for (const { path, props } of controlFixtures) {
				const { default: component } = await server.ssrLoadModule(
					`/src/lib/components/${path}.svelte`
				);
				controls.set(
					`${locale}/${path}`,
					component.render(props, { context: new Map([['i18n', writable(i18n)]]) }).html
				);
			}
		}
	} finally {
		await server.close();
	}
}, 60_000);

for (const path of ['layout/Navbar', 'channel/Navbar', 'app/AppSidebar']) {
	test(`${path} has no nested interactive controls or missing image alternatives`, async ({
		page
	}) => {
		const html = navigation.get(path);
		if (html === undefined) {
			throw new Error(`Missing rendered navigation: ${path}`);
		}
		await page.setContent(html);
		await expect(page.locator('a button, a [role="button"], button button')).toHaveCount(0);
		if (path !== 'app/AppSidebar') {
			const menu = page.getByRole('button', { name: 'User Menu', exact: true });
			await expect(menu).toHaveCount(1);
			await expect(menu.locator('button')).toHaveCount(0);
			await expect(menu.locator('img')).toHaveAttribute('alt', '');
		}
		const results = await new AxeBuilder({ page })
			.withRules(['nested-interactive', 'image-alt', 'button-name', 'aria-prohibited-attr'])
			.analyze();
		expect(results.violations).toEqual([]);
	});
}

for (const locale of ['en-GB', 'fr-CA']) {
	for (const { path } of controlFixtures) {
		test(`${path} has named, permitted and non-nested controls in ${locale}`, async ({ page }) => {
			const html = controls.get(`${locale}/${path}`);
			if (html === undefined) {
				throw new Error(`Missing rendered control: ${locale}/${path}`);
			}
			// Check before the browser's HTML parser can repair nested native buttons.
			expect(html).not.toMatch(
				/<button\b[^>]*>(?:(?!<\/button>)[\s\S])*<(?:button|input|select|textarea)\b/
			);
			await page.setContent(html);
			if (path === 'common/FileItem') {
				await expect(page.getByRole('button', { name: 'Report' })).toHaveCount(1);
				await expect(
					page.getByRole('button', {
						name: locale === 'fr-CA' ? 'Retirer le fichier' : 'Remove File'
					})
				).toHaveCount(1);
			}
			if (path === 'common/Checkbox') {
				await expect(page.getByRole('checkbox', { name: 'Vision' })).toBeChecked();
			}
			const results = await new AxeBuilder({ page })
				.withRules(['nested-interactive', 'button-name', 'aria-prohibited-attr'])
				.analyze();
			expect(results.violations).toEqual([]);
		});
	}
}
