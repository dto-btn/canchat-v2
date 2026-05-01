import { BasePage, type Language } from './base.page';
import type { ChatPage } from './chat.page';
import { type Page, type Locator, expect } from '@playwright/test';

export class AuthPage extends BasePage {
	emailInput!: Locator;
	passwordInput!: Locator;
	signInButton!: Locator;
	createAccountButton!: Locator;
	nameInput!: Locator;
	onboardingButton!: Locator;
	signOutButtonPendingUser!: Locator;
	changeLanguageButtonPendingUser!: Locator;
	readonly isFirstRunButton: Locator;

	constructor(page: Page, lang: Language = 'en-GB') {
		super(page, lang);

		this.isFirstRunButton = page.getByRole('button').filter({ hasText: /^$/ });
		this.updateLanguage(lang);
	}

	/**
	 * Override to update Chat-specific locators
	 * @param lang The new language to switch to ('en-GB' or 'fr-CA')
	 */
	override updateLanguage(lang: Language) {
		super.updateLanguage(lang);

		this.emailInput = this.page.getByRole('textbox', {
			name: this.t['Enter Your Email'] || 'Enter Your Email'
		});
		this.passwordInput = this.page.getByRole('textbox', {
			name: this.t['Enter Your Password'] || 'Enter Your Password'
		});
		this.signInButton = this.page.getByRole('button', {
			name: this.t['Sign in'] || 'Sign in'
		});
		this.createAccountButton = this.page.getByRole('button', {
			name: this.t['Create Admin Account'] || 'Create Admin Account'
		});
		this.nameInput = this.page.getByRole('textbox', {
			name: this.t['Enter Your Full Name'] || 'Enter Your Full Name'
		});
		this.onboardingButton = this.page.getByRole('button', {
			name: this.t["Okay, Let's Go!"] || "Okay, Let's Go!"
		});
		this.signOutButtonPendingUser = this.page.getByRole('button', {
			name: this.t['Sign Out'] || 'Sign Out'
		});
		this.changeLanguageButtonPendingUser = this.page.getByRole('button', {
			name: 'Français',
			exact: true
		});
	}

	/**
	 * Perform the user login
	 * @param email The email address of the user.
	 * @param pass The password for the account.
	 */
	async login(email: string, pass: string) {
		await this.goto('/auth');
		await this.emailInput.fill(email);
		await this.passwordInput.fill(pass);
		await this.signInButton.click();

		// If the test is in French, click the change language button to switch to French UI
		if (this.lang === 'fr-CA' && email === 'pending@canchat.ca') {
			await this.changeLanguageButtonPendingUser.isVisible({ timeout: 2000 });
			await this.changeLanguageButtonPendingUser.click();
		}

		await Promise.race([
			this.page.waitForURL(/\/($|\?|c\/)/),
			this.page.locator('#chat-input').waitFor({ state: 'visible' }),
			this.signOutButtonPendingUser.waitFor({ state: 'visible' })
		]);
		await expect(this.page.locator('body')).toBeVisible();

		// Handle "Ok, Let's Go!" onboarding popup
		if (await this.onboardingButton.isVisible({ timeout: 2000 })) {
			await this.onboardingButton.click();
		}
	}

	/**
	 * Returns the localized pending-activation message.
	 */
	private getPendingActivationMessage(lang: Language) {
		return lang === 'fr-CA'
			? 'Veuillez patienter pendant que nous activons votre compte.'
			: 'Please wait while we activate your account.';
	}

	/**
	 * Verifies the pending-activation banner and sign-out button are visible.
	 */
	async verifyPendingActivation(lang: Language) {
		const pendingMessage = this.page.getByText(this.getPendingActivationMessage(lang));
		await expect(pendingMessage).toBeVisible({ timeout: 30000 });
		await expect(this.signOutButtonPendingUser).toBeVisible({ timeout: 30000 });
	}

	/**
	 * Logs in and waits for the chat input to be visible.
	 */
	async loginAndOpenChat(
		user: { email: string; password: string },
		chatPage: ChatPage,
		lang: Language
	) {
		await this.login(user.email, user.password);
		await chatPage.goto(`/?lang=${lang}`);
		await expect(this.page.locator('#chat-input')).toBeVisible({ timeout: 60000 });
	}

	/**
	 * Logs in and handles pending vs. standard users.
	 */
	async loginAndHandleUser(
		user: { email: string; password: string; role: string },
		chatPage: ChatPage,
		lang: Language
	) {
		await this.login(user.email, user.password);
		if (user.role === 'pending') {
			await this.verifyPendingActivation(lang);
			await this.signOutPendingUser();
			return;
		}

		await chatPage.goto(`/?lang=${lang}`);
		await expect(this.page.locator('#chat-input')).toBeVisible({ timeout: 60000 });
		await chatPage.signOut();
	}

	/**
	 * Clears auth state and confirms redirection to the login page.
	 */
	async clearAuthAndReturnToLogin() {
		await this.page.evaluate(() => localStorage.removeItem('token')).catch(() => {});
		await this.page
			.context()
			.clearCookies()
			.catch(() => {});
		await this.page.reload();
		await expect(this.page).toHaveURL(/\/auth/);
	}

	/**
	 * Registers a new Admin account (first run only)
	 * @param name The name of the admin.
	 * @param email The email address of the admin.
	 * @param pass The password for the account.
	 */
	async registerAdmin(name: string, email: string, pass: string) {
		await this.nameInput.fill(name);
		await this.emailInput.fill(email);
		await this.passwordInput.fill(pass);
		await this.createAccountButton.click();

		// Handle "Ok, Let's Go!" onboarding popup
		await this.onboardingButton.waitFor({ state: 'visible', timeout: 5000 });
		await this.onboardingButton.click();
	}

	/**
	 * Signs the user out and verifies redirection to the auth page.
	 */
	async signOutPendingUser() {
		await this.signOutButtonPendingUser.click();
		await expect(this.page).toHaveURL(/\/auth/);
	}
}
