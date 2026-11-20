import { test, expect } from '@playwright/test';
import { createInstance } from 'i18next';
import { marked } from 'marked';
import { writable } from 'svelte/store';
import { createServer } from 'vite';

const rendered = new Map<string, string>();

test.beforeAll(async () => {
	const server = await createServer({
		server: { middlewareMode: true, watch: null, hmr: false }
	});
	try {
		const { user, settings, models } = await server.ssrLoadModule('/src/lib/stores/index.ts');
		const { default: translations } = await server.ssrLoadModule(
			'/src/lib/i18n/locales/fr-CA/translation.json'
		);
		user.set({ id: 'alex', name: 'Alex', role: 'user', profile_image_url: '/user.png' });
		models.set([{ id: 'model', name: 'Test model' }]);
		const { default: UserMessage } = await server.ssrLoadModule(
			'/src/lib/components/chat/Messages/UserMessage.svelte'
		);
		const { default: ResponseMessage } = await server.ssrLoadModule(
			'/src/lib/components/chat/Messages/ResponseMessage.svelte'
		);
		const { default: MarkdownTokens } = await server.ssrLoadModule(
			'/src/lib/components/chat/Messages/Markdown/MarkdownTokens.svelte'
		);
		const { default: ChannelMessage } = await server.ssrLoadModule(
			'/src/lib/components/channel/Messages/Message.svelte'
		);

		for (const locale of ['en-GB', 'fr-CA']) {
			const i18n = createInstance();
			await i18n.init({
				lng: locale,
				fallbackLng: 'en-GB',
				resources: { 'fr-CA': { translation: translations } }
			});
			const context = new Map([['i18n', writable(i18n)]]);
			const userMessage = { id: 'question', role: 'user', content: 'A question', timestamp: 0 };
			const response = {
				id: 'answer',
				role: 'assistant',
				model: 'model',
				content: 'An answer',
				timestamp: 0,
				done: false
			};
			const history = { messages: { question: userMessage, answer: response } };
			for (const chatBubble of [true, false]) {
				settings.set({ chatBubble });
				rendered.set(
					`${locale}-${chatBubble}`,
					UserMessage.render(
						{
							history,
							messageId: 'question',
							user: { name: 'Alex' },
							siblings: [],
							readOnly: true
						},
						{ context }
					).html
				);
			}
			rendered.set(
				`${locale}-response`,
				ResponseMessage.render(
					{ history, messageId: 'answer', siblings: [], readOnly: true },
					{ context }
				).html
			);
			rendered.set(
				`${locale}-markdown`,
				MarkdownTokens.render(
					{ id: 'markdown', tokens: marked.lexer('# First\n\n## Second') },
					{ context }
				).html
			);
			rendered.set(
				`${locale}-channel`,
				ChannelMessage.render(
					{
						message: {
							id: 'channel-message',
							user_id: 'alex',
							user: { name: 'Alex' },
							content: 'Channel message'
						},
						showUserProfile: false
					},
					{ context }
				).html
			);
		}
	} finally {
		await server.close();
	}
});

for (const locale of ['en-GB', 'fr-CA']) {
	for (const chatBubble of [true, false]) {
		test(`user messages have a navigable heading in ${locale}, bubbles ${chatBubble}`, async ({
			page
		}) => {
			await page.setContent(rendered.get(`${locale}-${chatBubble}`)!);
			await expect(page.getByRole('heading', { level: 2 })).toHaveText(
				`${locale === 'fr-CA' ? 'Vous' : 'You'}: A question`
			);
		});
	}

	test(`assistant messages and Markdown headings are semantic in ${locale}`, async ({ page }) => {
		await page.setContent(
			`${rendered.get(`${locale}-response`)}${rendered.get(`${locale}-markdown`)}`
		);
		await expect(
			page.getByRole('heading', { level: 2, name: 'Test model: An answer' })
		).toHaveCount(1);
		await expect(page.getByRole('heading', { level: 1, name: 'First' })).toHaveCount(1);
		await expect(page.getByRole('heading', { level: 2, name: 'Second' })).toHaveCount(1);
	});

	test(`channel messages identify the author when the profile is hidden in ${locale}`, async ({
		page
	}) => {
		await page.setContent(rendered.get(`${locale}-channel`)!);
		await expect(
			page.getByRole('heading', { level: 2, name: 'Alex: Channel message' })
		).toHaveCount(1);
	});
}
