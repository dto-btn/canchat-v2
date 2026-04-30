import { type Page, type Locator, expect } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const enPath = path.join(__dirname, '../../../src/lib/i18n/locales/en-GB/translation.json');
const frPath = path.join(__dirname, '../../../src/lib/i18n/locales/fr-CA/translation.json');

const en = JSON.parse(fs.readFileSync(enPath, 'utf-8'));
const fr = JSON.parse(fs.readFileSync(frPath, 'utf-8'));

export type TranslationKeys = keyof typeof en;
export type Language = 'en-GB' | 'fr-CA';

export class BasePage {
	readonly page: Page;
	readonly lang: Language;
	t: typeof en;

	// --- Locators: Header ---
	userProfileButton!: Locator;
	menuSettings!: Locator;
	menuAdminPanel!: Locator;
	menuSignOut!: Locator;
	setDefaultModel!: Locator;
	headerLanguageButtonEN!: Locator;
	headerLanguageButtonFR!: Locator;

	// --- Locators: Sidebar ---
	sidebarOpenButton!: Locator;
	readonly sidebarCloseButton: Locator;
	readonly sidebarNewChatButton: Locator;
	readonly headerNewChatButton: Locator;

	// --- Locators: System ---
	readonly splashLogo: Locator;
	confirmDialogButton!: Locator;

	// --- Locators: Toast ---
	readonly toast: Locator;

	constructor(page: Page, lang: 'en-GB' | 'fr-CA' = 'en-GB') {
		this.page = page;
		this.lang = lang;
		this.t = lang === 'fr-CA' ? fr : en;

		this.sidebarOpenButton = page.locator('#sidebar-toggle-button');
		this.sidebarCloseButton = page.locator('#hide-sidebar-button');
		this.sidebarNewChatButton = page.locator('#sidebar-new-chat-button');
		this.headerNewChatButton = page.locator('#new-chat-button');
		this.splashLogo = page.locator('img#logo[alt="CANChat Logo"]');
		this.toast = page.locator('li[role="status"]');

		this.updateLanguage(lang);
	}

	/**
	 * Updates the page language context
	 * @param lang The new language to switch to ('en-GB' or 'fr-CA')
	 */
	updateLanguage(lang: Language) {
		this.t = lang === 'fr-CA' ? fr : en;

		this.userProfileButton = this.page.getByRole('button', {
			name: this.t['User profile'] || 'User profile'
		});
		this.headerLanguageButtonEN = this.page.getByRole('button', { name: 'EN', exact: true });
		this.headerLanguageButtonFR = this.page.getByRole('button', { name: 'FR', exact: true });

		this.menuSettings = this.page.getByRole('menuitem', { name: this.t['Settings'] || 'Settings' });
		this.menuAdminPanel = this.page.getByRole('menuitem', {
			name: this.t['Admin Panel'] || 'Admin Panel'
		});
		this.menuSignOut = this.page.getByRole('menuitem', { name: this.t['Sign Out'] || 'Sign Out' });
		this.setDefaultModel = this.page.getByRole('button', {
			name: this.t['Set as default'] || 'Set as default'
		});
		this.confirmDialogButton = this.page.getByRole('button', {
			name: this.t['Confirm'] || 'Confirm'
		});
	}

	/**
	 * Switch the page language if required
	 * @param lang The new language to switch to ('en-GB' or 'fr-CA')
	 */
	async verifyPageLanguage(lang: Language) {
		if (lang == 'fr-CA') {
			if (await this.headerLanguageButtonFR.isVisible()) {
				await this.headerLanguageButtonFR.click();
				// Wait for the button to change (meaning the language switch logic triggered)
				await expect(this.headerLanguageButtonFR).not.toBeVisible();
			}
		} else {
			if (await this.headerLanguageButtonEN.isVisible()) {
				await this.headerLanguageButtonEN.click();
				// Wait for the button to change (meaning the language switch logic triggered)
				await expect(this.headerLanguageButtonEN).not.toBeVisible();
			}
		}
	}

	/**
	 * Navigates to the application root and waits for the splash screen to vanish
	 */
	async goto(path: string = '/') {
		await this.page.goto(path);
		await this.splashLogo.waitFor({ state: 'detached', timeout: 20000 }).catch(() => {});
		await this.page.waitForLoadState('domcontentloaded');
		await expect(this.page.locator('body')).toBeAttached();
	}

	/**
	 * Ensures the sidebar is in the desired state (Open or Closed)
	 * @param visible the state of the sidebar
	 */
	async toggleSidebar(visible: boolean) {
		// Wait for the UI to be ready enough to determine state
		// We use a short wait for either button to appear to establish baseline
		try {
			await Promise.race([
				this.sidebarOpenButton.waitFor({ state: 'visible', timeout: 5000 }),
				this.sidebarCloseButton.waitFor({ state: 'visible', timeout: 5000 })
			]);
		} catch (e) {
			// Continue, let checks decide
		}

		if (visible) {
			// We want to OPEN it.
			// If Close button is already visible, it's open. Done.
			// Using isVisible() here is safer because we waited above.
			if (await this.sidebarCloseButton.isVisible()) {
				return;
			}
			// It's not Open, so it must be Closed (or loading).
			// Click Open button. This will WAIT for it to be visible/enabled.
			await this.sidebarOpenButton.click();
			await expect(this.sidebarCloseButton).toBeVisible();
		} else {
			// We want to CLOSE it.
			// If Open button is already visible, it's closed. Done.
			if (await this.sidebarOpenButton.isVisible()) {
				return;
			}
			// It's not Closed, so it must be Open.
			// Click Close button. This will WAIT for it.
			await this.sidebarCloseButton.click();
			await expect(this.sidebarOpenButton).toBeVisible();
		}
	}

	/**
	 * Returns true if the sidebar is currently open.
	 */
	async isSidebarOpen(): Promise<boolean> {
		return await this.sidebarCloseButton.isVisible();
	}

	/**
	 * Waits until the page stabilize
	 */
	async waitToSettle(stableMs: number = 500, timeoutMs: number = 30000) {
		await this.page.waitForLoadState('networkidle');

		await this.page.waitForFunction(
			({ stableMs }) => {
				const body = document.querySelector('body');
				if (!body) return false;
				const html = body.innerHTML;

				// Initialize snapshot storage if missing
				if (!(window as any).__lastBodySnapshot) {
					(window as any).__lastBodySnapshot = { html, time: Date.now() };
					return false;
				}

				const snap = (window as any).__lastBodySnapshot;
				if (snap.html !== html) {
					snap.html = html;
					snap.time = Date.now();
				}

				return Date.now() - snap.time >= stableMs;
			},
			{ stableMs },
			{ timeout: timeoutMs }
		);
	}

	/**
	 * Opens the User Menu from the header if not already visible
	 */
	async openHeaderUserMenu() {
		await this.dismissBlockingModal();
		if (!(await this.menuSignOut.isVisible())) {
			await this.userProfileButton.click({ force: true });
			if (!(await this.menuSignOut.isVisible().catch(() => false))) {
				await this.page.waitForTimeout(300);
				await this.userProfileButton.click({ force: true });
			}
			await expect(this.menuSignOut).toBeVisible({ timeout: 8000 });
		}
	}

	/**
	 * Opens the global settings via the header user menu
	 */
	async openGlobalSettings() {
		await this.openHeaderUserMenu();
		await this.menuSettings.click();
		this.waitToSettle(500);
	}

	/**
	 * Navigates to a specific section within the user settings
	 * @param menu Sidebar link name
	 * @param section Tab button name
	 */
	async navigateToUserSettings(section?: string) {
		await this.goto('/');
		const sectionName = section || this.t['General'] || 'General';

		await this.openGlobalSettings();

		await this.page.getByRole('button', { name: sectionName }).click();
		await this.waitToSettle(500);
	}

	/**
	 * Signs the user out and verifies redirection to the auth page.
	 */
	async signOut() {
		await this.goto('/');
		await this.dismissBlockingModal();
		await this.openHeaderUserMenu();
		await this.menuSignOut.click({ force: true });
		await expect(this.page).toHaveURL(/\/auth/);
	}

	/**
	 * Dismisses blocking modal overlays if present
	 */
	async dismissBlockingModal() {
		const modal = this.page.locator('div.modal');
		for (let i = 0; i < 3; i += 1) {
			if (!(await modal.isVisible().catch(() => false))) return;
			await this.page.keyboard.press('Escape').catch(() => {});
			const closeButton = this.page.getByRole('button', {
				name: /Close|Cancel|OK|Ok|Okay|Okay, Let's Go!|Fermer|Annuler|D’accord/i
			});
			if (await closeButton.isVisible().catch(() => false)) {
				await closeButton
					.first()
					.click({ force: true })
					.catch(() => {});
			}
			await modal.waitFor({ state: 'hidden', timeout: 2000 }).catch(async () => {
				await modal
					.first()
					.click({ force: true })
					.catch(() => {});
			});
		}
	}

	/**
	 * Updates the desired chat model
	 * @param modelName The exact name of the model option to select (e.g. 'gpt-4o')
	 * @param modelNumber The index of the model in the list (only if more than 1)
	 */
	async updateChatModel(modelName: string, modelNumber: number = 1) {
		await this.goto('/');
		const anyModelButton = this.page.locator('button[id^="model-selector-"]').first();
		await anyModelButton.waitFor();

		const selectorIndex = modelNumber - 1;
		const modelDropdown = this.page.locator(`#model-selector-${selectorIndex}-button`);

		//If the user request to change a model at an index that does not exist, throw an error.
		if (!(await modelDropdown.isVisible())) {
			const actualCount = await this.page
				.locator('button[id^="model-selector-"][id$="-button"]')
				.count();

			throw new Error(
				`Error: You requested to update Model #${modelNumber}, but there are only ${actualCount} models available.`
			);
		}

		await modelDropdown.click();
		await this.page.getByRole('button', { name: `Model ${modelName}` }).click();
	}

	/**
	 * Click the Set as Default button
	 */
	async setDefaultChatModel() {
		await this.setDefaultModel.click();
	}

	/**
	 * Retrieves the translation for a given key.
	 * Returns the key itself if the translation is missing.
	 * @param key The text/key to translate
	 */
	getTranslation(key: string): string {
		return (this.t as any)[key] || key;
	}

	/**
	 * Verifies that a success toast appeared with specific text
	 * @param messageKey The translation key for the expected message
	 */
	async verifyToast(messageKey: string) {
		const expectedText = this.getTranslation(messageKey);
		const specificToast = this.toast.filter({ hasText: expectedText }).first();
		await expect(specificToast).toBeAttached();
	}

	/**
	 * Reads text content directly from the browser clipboard.
	 * Requires clipboard-read permission.
	 */
	async getClipboardText(): Promise<string> {
		return await this.page.evaluate(() => navigator.clipboard.readText());
	}
}
