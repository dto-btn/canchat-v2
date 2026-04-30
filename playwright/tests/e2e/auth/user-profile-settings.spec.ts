import { test } from '../../../src/fixtures/base-fixture';

test.describe('Feature: User Settings', () => {
	test.setTimeout(180000);

	test('user can delete all chats', async ({ userPage, locale }, testInfo) => {
		console.log('Testing: user can delete all chats');
		await userPage.navigateToUserSettings(userPage.t['Chats'] || 'Chats');

		await userPage.page
			.getByRole('button', { name: userPage.t['Delete All Chats'] || 'Delete All Chats' })
			.click();
		await userPage.page.getByRole('button', { name: userPage.t['Confirm'] || 'Confirm' }).click();
	});
});
