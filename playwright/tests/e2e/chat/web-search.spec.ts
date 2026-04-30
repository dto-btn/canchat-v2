import { test, expect } from '../../../src/fixtures/base-fixture';
import type { ChatPage } from '../../../src/pages/chat.page';

/**
 * Navigates to the app, enables web search, sends a locale-appropriate question,
 * waits for the full response, and returns the answer text and the question used.
 */
async function setupWebSearchResponse(
	userPage: ChatPage,
	locale: string | undefined
): Promise<{ answerText: string; question: string }> {
	await userPage.goto('/');
	await userPage.enableWebSearch();

	const question =
		locale === 'fr-CA'
			? 'Quel pays a actuellement la plus grande population au monde?'
			: 'What country currently has the largest population in the world?';

	await userPage.sendMessage(question);
	const answerText = await userPage.getLastMessageText();

	return { answerText, question };
}

test.describe('Feature: Web Search', () => {
	test.setTimeout(120000);

	// ===========================================
	// CHAT-WEB-TC001: Turn on web search and ask a question
	// ===========================================
	test('CHAT-WEB-TC001: User is able to turn on web search and ask a question', async ({
		userPage,
		locale
	}, testInfo) => {
		const { answerText } = await setupWebSearchResponse(userPage, locale);

		expect(answerText).toMatch(/inde|chine|india|china/i);

		await testInfo.attach('Results', {
			body: `Chat response: ${answerText}`,
			contentType: 'text/plain'
		});
	});

	// ===========================================
	// CHAT-WEB-TC002: Expand query accordion and see websites
	// ===========================================
	test('CHAT-WEB-TC002: User can expand the query and see a list of websites', async ({
		userPage,
		locale
	}, testInfo) => {
		const { answerText } = await setupWebSearchResponse(userPage, locale);

		expect(answerText).not.toBe('');

		// 1. Verify the query summary is displayed above the message
		const queryLocator = userPage.getWebSearchQuery();
		await expect(queryLocator.last()).toBeVisible();

		// 2. Expand the accordion and verify source URLs are listed
		await userPage.expandWebSearchAccordion();
		const links = userPage.getWebSearchLinks();
		await expect(links.first()).toBeVisible({ timeout: 5000 });
		expect(await links.count()).toBeGreaterThan(0);

		await testInfo.attach('Results', {
			body: `Chat response: ${answerText}`,
			contentType: 'text/plain'
		});
	});

	// ===========================================
	// CHAT-WEB-TC003: Click URL in accordion opens new tab
	// ===========================================
	test('CHAT-WEB-TC003: User can click the url in the accordion returned in a new tab', async ({
		userPage,
		locale
	}, testInfo) => {
		await setupWebSearchResponse(userPage, locale);

		// Expand the accordion and verify source URLs are listed
		await userPage.expandWebSearchAccordion();
		const links = userPage.getWebSearchLinks();
		await expect(links.first()).toBeVisible({ timeout: 5000 });
		expect(await links.count()).toBeGreaterThan(0);

		// Click the first link and verify it opens a new tab
		const [newPage] = await Promise.all([
			userPage.page.context().waitForEvent('page'),
			links.first().click()
		]);
		expect(newPage.url()).toMatch(/^https?:\/\//);
		await newPage.close();

		await testInfo.attach('Results', {
			body: `Opened URL: ${newPage.url()}`,
			contentType: 'text/plain'
		});
	});

	// ===========================================
	// CHAT-WEB-TC004: Click inline citation opens modal or new tab
	// ===========================================
	test('CHAT-WEB-TC004: User can click the url citation within the text and open in a new tab', async ({
		userPage,
		locale
	}, testInfo) => {
		await setupWebSearchResponse(userPage, locale);

		const citations = userPage.getInlineCitations();
		const count = await citations.count();

		if (count === 0) {
			// No inline citations in the response
			testInfo.annotations.push({
				type: 'skip-reason',
				description: 'LLM did not include inline citations in this response'
			});
			return;
		}

		const firstCitation = citations.first();
		const tagName = await firstCitation.evaluate((el) => el.tagName.toLowerCase());

		if (tagName === 'button') {
			// Badge button opens modal
			await firstCitation.click();
			const modal = userPage.page.locator('div.modal').last();
			await expect(modal).toBeVisible({ timeout: 5000 });

			await testInfo.attach('Results', {
				body: 'Inline citation (button) opened CitationsModal',
				contentType: 'text/plain'
			});
		} else {
			// Markdown hyperlink opens new browser tab
			const [newPage] = await Promise.all([
				userPage.page.context().waitForEvent('page'),
				firstCitation.click()
			]);
			expect(newPage.url()).toMatch(/^https?:\/\//);
			await newPage.close();

			await testInfo.attach('Results', {
				body: `Inline citation (link) opened URL: ${newPage.url()}`,
				contentType: 'text/plain'
			});
		}
	});

	// ===========================================
	// CHAT-WEB-TC005: Click bottom citation shows modal
	// ===========================================
	test('CHAT-WEB-TC005: User can click the url citation at the bottom and get more information', async ({
		userPage,
		locale
	}, testInfo) => {
		await setupWebSearchResponse(userPage, locale);

		const citations = userPage.getBottomCitations();
		const count = await citations.count();

		if (count === 0) {
			// No bottom citation for this response (llm response failed).
			testInfo.annotations.push({
				type: 'skip-reason',
				description: 'No bottom citation present in this web search response (llm response failed)'
			});
			return;
		}

		// Click the first citation and verify the CitationsModal opens
		const modal = await userPage.clickBottomCitation(0);
		await expect(modal).toBeVisible({ timeout: 5000 });

		await expect(modal.getByText('Citation')).toBeVisible();

		await testInfo.attach('Results', {
			body: `Citation modal opened; ${count} citation(s)`,
			contentType: 'text/plain'
		});
	});

	// ===========================================
	// CHAT-WEB-TC006: User can see the query being searched
	// ===========================================
	test('CHAT-WEB-TC006: User can see the query being searched', async ({
		userPage,
		locale
	}, testInfo) => {
		await userPage.goto('/');
		await userPage.enableWebSearch();

		const question =
			locale === 'fr-CA'
				? 'Quel pays a actuellement la plus grande population au monde?'
				: 'What country currently has the largest population in the world?';

		// Send without waiting so we can observe the in-progress state
		await userPage.sendMessage(question, false);

		// The status-description element shows "Searching…" or "Generating search query".
		const queryLocator = userPage.getWebSearchQuery();
		await expect(queryLocator.last()).toBeVisible({ timeout: 30000 });
		await userPage.waitForGeneration();

		const answerText = await userPage.getLastMessageText();
		expect(answerText).not.toBe('');

		await testInfo.attach('Results', {
			body: `Chat response: ${answerText}`,
			contentType: 'text/plain'
		});
	});

	// ===========================================
	// CHAT-WEB-TC007: Search returning 0 results doesn't crash
	// ===========================================
	test.skip('CHAT-WEB-TC007: A search returning 0 results does not crash the search', async ({
		userPage
	}) => {
		// Skipped: No reliable way to craft a query that always returns 0 results for now.
	});

	// ===========================================
	// CHAT-WEB-TC008: Web search timeout skipping
	// ===========================================
	test.skip('CHAT-WEB-TC008: Web search that timeout are being skipped', async ({ userPage }) => {
		// Skipped: No reliable way to test or simulate a timeout for now && design of the feature is not final.
	});

	// ===========================================
	// CHAT-WEB-TC009: User can stop the generation of the search request
	// ===========================================
	test('CHAT-WEB-TC009: User can stop the generation of the search request', async ({
		userPage,
		locale
	}) => {
		await userPage.goto('/');
		await userPage.enableWebSearch();

		const question =
			locale === 'fr-CA'
				? 'Quel pays a actuellement la plus grande population au monde?'
				: 'What country currently has the largest population in the world?';

		// Send without waiting so we can interact with the stop button mid-generation
		await userPage.sendMessage(question, false);

		// Wait for the web search to start before attempting to stop
		const queryLocator = userPage.getWebSearchQuery();
		await expect(queryLocator.last()).toBeVisible({ timeout: 15000 });

		await userPage.stopGenerationButton.click();

		// Known bug: the stop button does not currently interrupt an in-progress web search.
		await expect(userPage.stopGenerationButton).toBeHidden({ timeout: 2000 });
	});

	// ===========================================
	// CHAT-WEB-TC010: Refresh page, search continues
	// ===========================================
	test('CHAT-WEB-TC010: User can refresh the page and the search continues', async ({
		userPage,
		locale
	}, testInfo) => {
		await userPage.goto('/');
		await userPage.enableWebSearch();

		const question =
			locale === 'fr-CA'
				? 'Quel pays a actuellement la plus grande population au monde?'
				: 'What country currently has the largest population in the world?';

		// Send without waiting so the page can be refreshed mid-generation
		await userPage.sendMessage(question, false);

		// Allow the web search to start before refreshing
		await userPage.page.waitForTimeout(1500);
		await userPage.page.reload();
		await userPage.page.waitForLoadState('networkidle');

		await userPage.waitForGeneration();
		const answerText = await userPage.getLastMessageText();
		expect(answerText).not.toBe('');

		await testInfo.attach('Results', {
			body: `Post-refresh response: ${answerText}`,
			contentType: 'text/plain'
		});
	});

	// ===========================================
	// CHAT-WEB-TC011: Search results in French
	// ===========================================
	test('CHAT-WEB-TC011: User can perform a search and get result in french', async ({
		userPage,
		locale
	}, testInfo) => {
		await userPage.goto('/');

		await userPage.enableWebSearch();

		const question = 'Quel pays a actuellement la plus grande population au monde?';
		await userPage.sendMessage(question);

		const answerText = await userPage.getLastMessageText();
		expect(answerText).not.toBe('');

		expect(answerText).toMatch(/inde|chine|monde|population/i);

		await testInfo.attach('Results', {
			body: `French search response: ${answerText}`,
			contentType: 'text/plain'
		});
	});
});
