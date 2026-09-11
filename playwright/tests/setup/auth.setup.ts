import { test as setup, type Page } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';
import { fileURLToPath } from 'url';
import { AuthPage } from '../../src/pages/auth.page';
import { AdminPage } from '../../src/pages/admin.page';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// --- Paths & Data ---
const usersFile = path.join(__dirname, '../../src/test-data/users.json');
const usersData = JSON.parse(fs.readFileSync(usersFile, 'utf-8'));

const adminUser = usersData.users.find((u: any) => u.username === 'admin');
const standardUsers = usersData.users.filter((u: any) => u.username !== 'admin');

setup('global setup: seed data & initialize', async ({ page }) => {
	const authPage = new AuthPage(page);
	const adminPage = new AdminPage(page);

	await authPage.goto('/auth');
	const isFirstRun = await authPage.isFirstRunButton.isVisible();

	if (!isFirstRun && authFilesMissing) {
		console.log('CanChat already initialized. Missing auth files detected.');
		await authPage.login(adminUser.email, adminUser.password);
		await saveAuthState(page, 'admin.json');
		await seedUserAccounts(page, adminPage);
		await generateUserAuthFiles(page, authPage, adminPage);
		console.log('Auth files regenerated.');
		return;
	}

	if (!isFirstRun) {
		console.log('CanChat already initialized. Skipping Global Setup');
		return;
	}

	console.log('First run detected. Starting initialization...');
	await performAdminRegistration(page, authPage);
	await seedUserAccounts(page, adminPage);
	await enableAvailableModels(adminPage);
	await adminPage.signOut();

	await acceptInitialUserTerms(page);

	console.log('Global Setup Complete!');
});

async function performAdminRegistration(page: Page, authPage: AuthPage) {
	console.log('Registering Admin...');
	// Click the empty button to start setup
	await authPage.isFirstRunButton.click();
	await authPage.registerAdmin(adminUser.name, adminUser.email, adminUser.password);
}

async function seedUserAccounts(page: Page, adminPage: AdminPage) {
	console.log('Checking User Accounts...');
	await adminPage.navigateToAdminSettings(
		adminPage.getTranslation('Users & Access'),
		adminPage.getTranslation('Overview')
	);

	for (const user of standardUsers) {
		const userExists = await page.getByText(user.email, { exact: true }).isVisible();

		if (!userExists) {
			console.log(`Creating user: ${user.username}`);
			await adminPage.createUser(user.name, user.role, user.email, user.password);
		}
	}
}

async function acceptInitialUserTerms(page: Page) {
	const browser = page.context().browser();

	for (const user of standardUsers) {
		if (user.username === 'pending') {
			continue;
		}

		console.log(`Accepting initial terms for ${user.username}...`);

		// Use a fresh context for each user
		const context = await browser!.newContext();
		const userPage = await context.newPage();
		const userAuthPage = new AuthPage(userPage);

		await userAuthPage.goto('/auth');
		await userAuthPage.login(user.email, user.password);

		// Accept Term of Use popup
		if (user.username != 'pending') {
			await userAuthPage.acceptTermsButton.click();
		}

		await context.close();
	}
}

async function enableAvailableModels(adminPage: AdminPage) {
	console.log('Checking Available Models...');
	// Make multiple models visible
	await adminPage.navigateToAdminSettings('Settings', 'Connections');

	const modelsToEnable = [
		'gpt-4o-mini',
		'gpt-4.1-mini',
		'gpt-5-mini',
		'gpt-5.4-mini',
		'o3-mini',
		'o4-mini'
	];
	let defaultModelSet = false;

	for (const model of modelsToEnable) {
		try {
			await adminPage.openModelSettings(model);
			const { isDeprecated, shutdownDate } = await adminPage.isModelDeprecated();

			if (isDeprecated) {
				console.log(
					`Skipping model ${model} - model is deprecated (Shutdown Date: ${shutdownDate}).`
				);
				continue;
			}

			const deprecationInfo = shutdownDate ? ` (Retires on: ${shutdownDate})` : '';

			await adminPage.updateModelDescription({
				en: `${model} English Description`,
				fr: `${model} French Description`
			});
			await adminPage.updateModelVisibility(adminPage.getTranslation('public'));
			await adminPage.saveModelSettings();

			if (!defaultModelSet) {
				await adminPage.updateChatModel(model);
				await adminPage.setDefaultChatModel();
				defaultModelSet = true;
			}
			console.log(`Successfully enabled model: ${model}${deprecationInfo}`);
		} catch (error) {
			console.log(`Skipping model ${model} - not available or failed to enable.`);
		}
	}
}
