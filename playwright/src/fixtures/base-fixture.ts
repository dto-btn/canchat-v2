import {
	mergeTests,
	type BrowserContext,
	type TestInfo,
	test as baseTest,
	expect
} from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';
import * as crypto from 'crypto';
import { authFixture } from './auth-fixture';
import { ChatPage } from '../pages/chat.page';
import { AdminPage } from '../pages/admin.page';
import { Language } from '../pages/base.page';

// Define coverage output directory
const istanbulCLIOutput = path.join(process.cwd(), '.nyc_output');

/**
 * Inject CSS to hide toast notifications during test runs.
 * This prevents notification pollution from concurrent workers.
 */
async function hideToastNotifications(context: BrowserContext) {
	await context.addInitScript(() => {
		const css = '[data-sonner-toaster] { display: none !important; }';
		const injectStyle = () => {
			if (document.head) {
				const style = document.createElement('style');
				style.textContent = css;
				document.head.appendChild(style);
			} else {
				// If head doesn't exist yet, wait for DOMContentLoaded
				document.addEventListener(
					'DOMContentLoaded',
					() => {
						const style = document.createElement('style');
						style.textContent = css;
						document.head.appendChild(style);
					},
					{ once: true }
				);
			}
		};
		injectStyle();
	});
}

/**
 * Provide a browser context to collect Istanbul coverage
 * @param context The Playwright browser context
 */
async function setupCoverage(context: BrowserContext) {
	// Ensure output directory exists
	await fs.promises.mkdir(istanbulCLIOutput, { recursive: true });

	await context.exposeFunction('collectIstanbulCoverage', (coverageJSON: string) => {
		if (coverageJSON) {
			fs.writeFileSync(
				path.join(istanbulCLIOutput, `playwright_coverage_${crypto.randomUUID()}.json`),
				coverageJSON
			);
		}
	});
}

/**
 * Triggers coverage collection for all open pages in the context
 * @param context The Playwright browser context
 */
async function saveCoverage(context: BrowserContext) {
	for (const page of context.pages()) {
		await page.evaluate(() =>
			(window as any).collectIstanbulCoverage(JSON.stringify((window as any).__coverage__))
		);
	}
}

type PageFixtures = {
	adminPage: AdminPage;
	userPage: ChatPage;
	analystPage: ChatPage;
	globalAnalystPage: ChatPage;
	guestPage: ChatPage;
};

export const test = mergeTests(authFixture, baseTest).extend<PageFixtures>({
	// --- Admin Fixture ---
	adminPage: async ({ browser, authFiles, locale }, use, testInfo: TestInfo) => {
		const context = await browser.newContext({
			storageState: authFiles.admin,
			locale: locale,
			permissions: testInfo.project.use.permissions
		});
		await setupCoverage(context);
		await hideToastNotifications(context);

		const page = await context.newPage();
		const adminPage = new AdminPage(page, locale as Language);
		await adminPage.goto(`/?lang=${locale}`);

		await use(adminPage);

		await saveCoverage(context);
		await context.close();
	},

	// --- Standard User Fixture ---
	userPage: async ({ browser, authFiles, locale }, use, testInfo: TestInfo) => {
		const context = await browser.newContext({
			storageState: authFiles.user,
			locale: locale,
			permissions: testInfo.project.use.permissions
		});
		await setupCoverage(context);
		await hideToastNotifications(context);

		const page = await context.newPage();
		const chatPage = new ChatPage(page, locale as Language);
		await chatPage.goto(`/?lang=${locale}`);

		await use(chatPage);

		await saveCoverage(context);
		await context.close();
	},

	// --- Analyst Fixture ---
	analystPage: async ({ browser, authFiles, locale }, use, testInfo: TestInfo) => {
		const context = await browser.newContext({
			storageState: authFiles.analyst,
			locale: locale,
			permissions: testInfo.project.use.permissions
		});
		await setupCoverage(context);
		await hideToastNotifications(context);

		const page = await context.newPage();
		const chatPage = new ChatPage(page, locale as Language);
		await chatPage.goto(`/?lang=${locale}`);

		await use(chatPage);

		await saveCoverage(context);
		await context.close();
	},

	// --- Global Analyst Fixture ---
	globalAnalystPage: async ({ browser, authFiles, locale }, use, testInfo: TestInfo) => {
		const context = await browser.newContext({
			storageState: authFiles.globalAnalyst,
			locale: locale,
			permissions: testInfo.project.use.permissions
		});
		await setupCoverage(context);
		await hideToastNotifications(context);

		const page = await context.newPage();
		const chatPage = new ChatPage(page, locale as Language);
		await chatPage.goto(`/?lang=${locale}`);

		await use(chatPage);

		await saveCoverage(context);
		await context.close();
	},

	// --- Guest/No-Auth Fixture ---
	guestPage: async ({ browser, locale }, use, testInfo: TestInfo) => {
		const context = await browser.newContext({
			permissions: testInfo.project.use.permissions
		});
		await setupCoverage(context);
		await hideToastNotifications(context);

		const page = await context.newPage();
		const chatPage = new ChatPage(page, locale as Language);
		await chatPage.goto('/auth');

		await use(chatPage);

		await saveCoverage(context);
		await context.close();
	}
});

export { expect };
