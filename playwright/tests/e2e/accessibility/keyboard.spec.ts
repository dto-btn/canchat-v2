import { test, expect } from '@playwright/test';
import { createServer, type ViteDevServer } from 'vite';
import { svelte, vitePreprocess } from '@sveltejs/vite-plugin-svelte';
import { resolve } from 'node:path';

let server: ViteDevServer;
let baseURL: string;

test.beforeAll(async () => {
	server = await createServer({
		configFile: false,
		optimizeDeps: {
			include: [
				'lowlight',
				'highlight.js/lib/core',
				'turndown',
				'dayjs',
				'file-saver',
				'paneforge',
				'@xyflow/svelte'
			]
		},
		define: { APP_VERSION: '"test"', APP_BUILD_HASH: '"test"' },
		resolve: { alias: { $lib: resolve('src/lib') } },
		server: { host: '127.0.0.1', port: 0, watch: null, hmr: false },
		plugins: [
			svelte({ configFile: false, preprocess: vitePreprocess() }),
			{
				name: 'keyboard-accessibility-fixture',
				resolveId(id) {
					if (id === '$app/environment') return '\0keyboard-environment';
					if (id === '$app/navigation') return '\0keyboard-navigation';
				},
				load(id) {
					if (id === '\0keyboard-environment')
						return 'export const browser = true; export const dev = false;';
					if (id === '\0keyboard-navigation')
						return 'export const goto = async () => {}; export const afterNavigate = () => {}; export const beforeNavigate = () => {};';
				},
				configureServer(server) {
					server.middlewares.use('/__keyboard', async (_request, response, next) => {
						try {
							response.setHeader('Content-Type', 'text/html');
							response.end(
								await server.transformIndexHtml(
									'/__keyboard',
									`<!doctype html>
								<html lang="en"><body><main id="fixture"></main><script type="module">
									import Fixture from '/playwright/fixtures/KeyboardAccessibility.svelte';
									import '/src/tailwind.css?import';
									import '/src/app.css?import';
									new Fixture({ target: document.getElementById('fixture') });
								</script></body></html>`
								)
							);
						} catch (error) {
							next(error);
						}
					});
				}
			}
		]
	});
	await server.listen();
	const address = server.httpServer?.address();
	if (!address || typeof address === 'string') throw new Error('Expected a TCP fixture server');
	baseURL = `http://127.0.0.1:${address.port}`;
}, 60_000);

test.afterAll(async () => {
	server?.httpServer?.closeAllConnections();
	await server?.close();
});

test.beforeEach(async ({ page }) => {
	await page.route('**/api/v1/**', async (route) => {
		const path = new URL(route.request().url()).pathname;
		let data: unknown = {};
		if (path.endsWith('/pinned')) data = false;
		if (path.endsWith('/tags')) data = [];
		if (path === '/api/v1/folders/')
			data = [
				{ id: 'folder-a', name: 'Folder A', parent_id: null, is_expanded: false },
				{ id: 'folder-b', name: 'Folder B', parent_id: 'folder-a', is_expanded: false }
			];
		if (path === '/api/v1/folders/' && route.request().method() === 'POST') {
			data = { id: 'new-folder', name: 'New folder', parent_id: null, is_expanded: false };
		}
		await route.fulfill({ json: data });
	});
});

for (const locale of ['en-GB', 'fr-CA']) {
	test(`message navigation exposes old actions and timestamps without trapping focus in ${locale}`, async ({
		page
	}) => {
		await page.goto(`${baseURL}/__keyboard?locale=${locale}`);
		const messages = page.locator('[data-keyboard-message]');
		await expect(messages).toHaveCount(3, { timeout: 30_000 });
		await page.locator('#before').focus();
		await page.keyboard.press('Tab');
		await expect(messages.nth(0)).toBeFocused();
		await page.keyboard.press('ArrowDown');
		await expect(messages.nth(1)).toBeFocused();
		await expect(messages.nth(1).getByText('14/11/2023 at', { exact: false })).toBeVisible();
		await page.keyboard.press('Enter');
		const edit = messages
			.nth(1)
			.getByRole('button', { name: locale === 'fr-CA' ? 'Modifier' : 'Edit', exact: true });
		await expect(edit).toBeFocused();
		await page.keyboard.press('Enter');
		await expect(messages.nth(1).locator('textarea')).toBeFocused();
		await page.keyboard.press('Escape');
		await expect(messages.nth(1)).toBeFocused();
		await page.keyboard.press('Enter');
		await page.keyboard.press('ArrowRight');
		const copy = messages.nth(1).locator('.copy-response-button');
		await expect(copy).toBeFocused();
		await expect(copy).toBeVisible();
		await expect(copy).toHaveCSS('outline-style', 'solid');
		await page.keyboard.press('Escape');
		await expect(messages.nth(1)).toBeFocused();
		await page.keyboard.press('End');
		await expect(messages.nth(2)).toBeFocused();
		await page.keyboard.press('Tab');
		await expect(page.getByRole('textbox', { name: 'Composer' })).toBeFocused();
		await page.keyboard.press('Shift+Tab');
		await expect(messages.nth(2)).toBeFocused();
		await page.keyboard.press('Home');
		await page.keyboard.press('Shift+Tab');
		await expect(page.locator('#before')).toBeFocused();
	});
}

test('model popover allows Tab to reach temporary chat and Escape restores focus', async ({
	page
}) => {
	await page.goto(`${baseURL}/__keyboard`);
	const trigger = page.locator('#model-selector-test-button');
	await trigger.focus();
	await page.keyboard.press('Enter');
	await expect(page.getByRole('textbox', { name: 'Search a model' })).toBeFocused();
	await page.keyboard.press('Tab');
	await expect(page.getByRole('button', { name: 'Test model', exact: true })).toBeFocused();
	await page.keyboard.press('Tab');
	await expect(page.getByRole('switch', { name: 'Temporary Chat' })).toBeFocused();
	await page.keyboard.press('Escape');
	await expect(trigger).toBeFocused();
	await page.keyboard.press('Enter');
	await expect(page.getByRole('textbox', { name: 'Search a model' })).toBeFocused();
	await page.keyboard.press('Tab');
	await expect(page.getByRole('button', { name: 'Test model', exact: true })).toBeFocused();
	await page.keyboard.press('Tab');
	await expect(page.getByRole('switch', { name: 'Temporary Chat' })).toBeFocused();
	await page.keyboard.press('Space');
	await expect(trigger).toBeFocused();
});

test('folder expansion and controls have visible keyboard focus and can be exited', async ({
	page
}) => {
	await page.goto(`${baseURL}/__keyboard`);
	const folder = page.locator('#folder-folder-a-button');
	await folder.focus();
	await page.keyboard.press('Space');
	await expect(folder).toHaveAttribute('aria-expanded', 'true');
	await page.keyboard.press('Enter');
	await expect(folder).toHaveAttribute('aria-expanded', 'false');
	await page.keyboard.press('ArrowRight');
	await expect(folder).toHaveAttribute('aria-expanded', 'true');
	await page.keyboard.press('ArrowLeft');
	await expect(folder).toHaveAttribute('aria-expanded', 'false');
	await page.keyboard.press('Tab');
	await expect(page.getByRole('button', { name: 'Folder Menu' })).toBeFocused();
	const advanced = page.getByRole('button', { name: 'Advanced Params' });
	await advanced.focus();
	await page.keyboard.press('Enter');
	await page.keyboard.press('Tab');
	await expect(page.getByRole('button', { name: 'Default', exact: true })).toBeFocused();
	await expect(page.getByRole('button', { name: 'Default', exact: true })).toHaveCSS(
		'outline-style',
		'solid'
	);
	await page.keyboard.press('Tab');
	await expect(page.locator('#after')).toBeFocused();
});

test('chat menu offers a non-drag move to nested folders and new folders', async ({ page }) => {
	await page.goto(`${baseURL}/__keyboard`);
	const trigger = page.locator('#test-chat-menu');
	for (const createNew of [false, true]) {
		await trigger.focus();
		await page.keyboard.press('Enter');
		await expect(page.getByRole('menuitem', { name: 'Move to folder', exact: true })).toBeFocused();
		await page.keyboard.press('Enter');
		const select = page.getByLabel('Destination folder');
		await expect(select).toBeFocused();
		await expect(select.locator('option', { hasText: 'Folder A / Folder B' })).toHaveCount(1);
		if (createNew) {
			await page.keyboard.press('Tab');
			await page.keyboard.press('Space');
			await page.keyboard.press('Tab');
			await page.keyboard.type('New folder');
		} else {
			await page.keyboard.press('ArrowDown');
			await page.keyboard.press('ArrowDown');
		}
		const request = page.waitForRequest('**/api/v1/chats/test-chat/folder');
		await page.keyboard.press('Tab');
		if (!createNew) await page.keyboard.press('Tab');
		await page.keyboard.press('Tab');
		await page.keyboard.press('Enter');
		expect((await request).postDataJSON()).toEqual({
			folder_id: createNew ? 'new-folder' : 'folder-b'
		});
		await expect(trigger).toBeFocused();
	}
	await expect(page.getByRole('status', { name: 'Changes' })).toHaveText('2');
});

test('tags can be added from the menu using only the keyboard and dialogs are escapable', async ({
	page
}) => {
	await page.goto(`${baseURL}/__keyboard`);
	const trigger = page.locator('#test-chat-menu');
	await trigger.focus();
	await page.keyboard.press('Enter');
	await expect(page.getByRole('menuitem', { name: 'Move to folder', exact: true })).toBeFocused();
	await page.keyboard.press('ArrowDown');
	await expect(page.getByRole('menuitem', { name: 'Tags', exact: true })).toBeFocused();
	await page.keyboard.press('Enter');
	const add = page.getByRole('button', { name: 'Add Tag', exact: true });
	await expect(add).toBeFocused();
	await page.keyboard.press('Enter');
	await expect(page.getByRole('combobox', { name: 'Add a tag', exact: true })).toBeFocused();
	await page.keyboard.type('keyboard');
	const request = page.waitForRequest('**/api/v1/chats/test-chat/tags');
	await page.keyboard.press('Enter');
	await request;
	await expect(add).toBeFocused();
	await page.keyboard.press('Escape');
	await expect(trigger).toBeFocused();
});

test('focusing a sidebar chat reveals its menu and rename retains keyboard focus', async ({
	page
}) => {
	await page.goto(`${baseURL}/__keyboard`);
	await page.getByRole('link', { name: 'Keyboard chat', exact: true }).focus();
	await page.keyboard.press('Tab');
	const trigger = page.locator('#chat-menu-keyboard-chat');
	await expect(trigger).toBeFocused();
	await expect(trigger).toBeVisible();
	await page.keyboard.press('Enter');
	await expect(page.getByRole('menuitem', { name: 'Move to folder', exact: true })).toBeFocused();
	await page.keyboard.press('Escape');
	await expect(trigger).toBeFocused();
	await page.keyboard.press('Enter');
	await expect(page.getByRole('menuitem', { name: 'Move to folder', exact: true })).toBeFocused();
	for (const label of ['Tags', 'Pin', 'Rename']) {
		await page.keyboard.press('ArrowDown');
		await expect(page.getByRole('menuitem', { name: label, exact: true })).toBeFocused();
	}
	await expect(page.getByRole('menuitem', { name: 'Rename', exact: true })).toBeFocused();
	await page.keyboard.press('Enter');
	await expect(page.locator('#chat-title-input-keyboard-chat')).toBeFocused();
});

test('the non-modal Chat Controls panel does not trap focus and returns it on close', async ({
	page
}) => {
	await page.goto(`${baseURL}/__keyboard?controls`);
	const opener = page.locator('#open-controls');
	await opener.focus();
	await page.keyboard.press('Enter');
	const close = page
		.locator('#chat-controls-container-wrapper')
		.getByRole('button', { name: 'Close', exact: true });
	await expect(close).toBeFocused();
	const outside = page.locator('#outside-controls');
	for (let index = 0; index < 3; index++) {
		await page.keyboard.press('Shift+Tab');
		if (await outside.evaluate((element) => element === document.activeElement)) break;
	}
	await expect(outside).toBeFocused();
	for (let index = 0; index < 3; index++) {
		await page.keyboard.press('Tab');
		if (await close.evaluate((element) => element === document.activeElement)) break;
	}
	await expect(close).toBeFocused();
	await page.keyboard.press('Tab');
	await page.keyboard.press('Tab');
	await page.keyboard.press('Tab');
	await page.keyboard.press('Tab');
	const panel = page.locator('#chat-controls-container-wrapper');
	await expect(panel.getByRole('button', { name: 'Advanced Params', exact: true })).toBeFocused();
	await page.keyboard.press('Enter');
	await page.keyboard.press('Tab');
	await page.keyboard.press('Tab');
	const seed = panel.locator('button[aria-describedby="tooltip-seed"]');
	await expect(seed).toBeFocused();
	await expect(seed).toHaveCSS('outline-style', 'solid');
	await expect(seed).toHaveCSS('outline-color', 'rgb(0, 94, 168)');
	await page.keyboard.press('Space');
	await page.keyboard.press('Tab');
	await expect(panel.getByRole('spinbutton').first()).toBeFocused();
	for (let index = 0; index < 7; index++) await page.keyboard.press('Shift+Tab');
	await expect(close).toBeFocused();
	await page.keyboard.press('Enter');
	await expect(opener).toBeFocused();
});
