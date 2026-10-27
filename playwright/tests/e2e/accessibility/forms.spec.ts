import { test, expect } from '@playwright/test';
import { createServer, type ViteDevServer } from 'vite';
import { svelte, vitePreprocess } from '@sveltejs/vite-plugin-svelte';
import { resolve } from 'node:path';

let server: ViteDevServer;
let baseURL: string;

test.describe.configure({ mode: 'default' });

test.beforeAll(async () => {
	server = await createServer({
		configFile: false,
		optimizeDeps: { noDiscovery: true },
		define: { APP_VERSION: '"test"', APP_BUILD_HASH: '"test"' },
		resolve: { alias: { $lib: resolve('src/lib') } },
		server: { host: '127.0.0.1', port: 0, watch: null, hmr: false },
		plugins: [
			svelte({ configFile: false, preprocess: vitePreprocess() }),
			{
				name: 'accessibility-form-fixture',
				resolveId(id) {
					if (id === '$app/environment') return '\0test-environment';
				},
				load(id) {
					if (id === '\0test-environment')
						return 'export const browser = true; export const dev = true;';
				},
				configureServer(server) {
					server.middlewares.use('/__accessibility-forms', async (_request, response, next) => {
						try {
							response.setHeader('Content-Type', 'text/html');
							response.end(
								await server.transformIndexHtml(
									'/__accessibility-forms',
									`<!doctype html><html lang="en"><body><main id="fixture"></main>
							<script type="module">
								import { createInstance } from '/node_modules/i18next/dist/esm/i18next.js';
								import { writable } from '/node_modules/svelte/src/runtime/store/index.js';
								import IssueModal from '/src/lib/components/common/IssueModal.svelte';
								import SuggestionModal from '/src/lib/components/common/SuggestionModal.svelte';
								import RateComment from '/src/lib/components/chat/Messages/RateComment.svelte';
								import fr from '/src/lib/i18n/locales/fr-CA/translation.json?import';
								import '/src/tailwind.css?import';
								import '/src/app.css?import';
								const params = new URLSearchParams(location.search);
								const locale = params.get('locale') || 'en-GB';
								const i18n = createInstance();
								await i18n.init({
									lng: locale, fallbackLng: 'en-GB', returnEmptyString: false,
									resources: { 'fr-CA': { translation: fr } }
								});
								const components = { issue: IssueModal, suggestion: SuggestionModal, rating: RateComment };
								const Component = components[params.get('form')];
								new Component({
									target: document.getElementById('fixture'),
									context: new Map([['i18n', writable(i18n)]]),
									props: { show: true, message: { id: 'test', annotation: { rating: 1 } } }
								});
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
	if (!address || typeof address === 'string') {
		throw new Error('Expected the accessibility fixture server to listen on a TCP port');
	}
	baseURL = `http://127.0.0.1:${address.port}`;
}, 60_000);

test.afterAll(async () => {
	server?.httpServer?.closeAllConnections();
	await server?.close();
});

for (const locale of ['en-GB', 'fr-CA']) {
	for (const form of ['issue', 'suggestion']) {
		test(`${form} upload errors are linked, announced and cleared in ${locale}`, async ({
			page
		}) => {
			await page.goto(`${baseURL}/__accessibility-forms?form=${form}&locale=${locale}`);
			const input = page.locator(`#${form}-files`);
			await expect(input).toBeAttached({ timeout: 30_000 });
			await expect(input).toHaveAccessibleName(
				locale === 'fr-CA' ? 'Choisir les fichiers à téléverser' : 'Choose files to upload'
			);
			await input.setInputFiles({
				name: 'invalid.txt',
				mimeType: 'text/plain',
				buffer: Buffer.from('not an image')
			});
			const error = page.locator(`#${form}-files-error`);
			const typeError =
				locale === 'fr-CA'
					? 'Seuls les fichiers image sont autorisés.'
					: 'Only image files are allowed.';
			await expect(error).toHaveAttribute('role', 'alert');
			await expect(error).toHaveText(typeError);
			await expect(input).toHaveAttribute('aria-invalid', 'true');
			await expect(input).toHaveAccessibleDescription(typeError);
			await input.setInputFiles(
				Array.from({ length: 4 }, (_, index) => ({
					name: `${index}.png`,
					mimeType: 'image/png',
					buffer: Buffer.from('image')
				}))
			);
			await expect(error).toHaveText(
				locale === 'fr-CA'
					? 'Vous pouvez téléverser un maximum de 3 fichiers.'
					: 'You can only upload up to 3 files'
			);
			await input.setInputFiles({
				name: 'valid.png',
				mimeType: 'image/png',
				buffer: Buffer.from('image')
			});
			await expect(error).toHaveCount(0);
			await expect(input).not.toHaveAttribute('aria-invalid');
			await expect(input).not.toHaveAttribute('aria-describedby');
		});
	}
}

test('rating reasons are labelled and pressed, and the comment is required', async ({ page }) => {
	await page.goto(`${baseURL}/__accessibility-forms?form=rating`);
	const group = page.getByRole('group', { name: 'Why?' });
	await expect(group).toBeVisible({ timeout: 30_000 });
	await expect(group).toHaveAccessibleDescription(
		'Fields marked with an asterisk (*) are required.'
	);
	const reason = group.getByRole('button', { name: 'Accurate information', exact: true });
	await expect(reason).toHaveAttribute('aria-pressed', 'false');
	await reason.click();
	await expect(reason).toHaveAttribute('aria-pressed', 'true');
	const comment = page.getByRole('textbox', { name: 'Comment' });
	await expect(comment).toHaveAttribute('required', '');
	const save = page.getByRole('button', { name: 'Save', exact: true });
	await expect(save).toBeDisabled();
	await page.getByRole('button', { name: '8', exact: true }).click();
	await comment.fill('Helpful answer');
	await expect(save).toBeEnabled();
	await comment.fill('   ');
	await expect(save).toBeDisabled();
});
