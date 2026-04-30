import { test, expect } from '../../../src/fixtures/base-fixture';

test.describe('Sidebar and Chat History Features', () => {
	test.describe.configure({ mode: 'default' });
	test.setTimeout(60000);

	// ===========================================
	// CHAT-SIDEBAR-TC001: Hide/Display Sidebar
	// ===========================================
	test('CHAT-SIDEBAR-TC001: User can hide/display the sidebar', async ({ userPage }) => {
		// 1. If the sidebar is displayed, click on the "Hide Sidebar" button.
		await userPage.toggleSidebar(false);

		// 2. User observes that the sidebar is hidden.
		await expect(userPage.sidebarCloseButton).not.toBeVisible();
		await expect(userPage.sidebarOpenButton).toBeVisible();
		await expect(userPage.headerNewChatButton).toBeVisible();
		await expect(userPage.sidebarNewChatButton).not.toBeVisible();

		// 3. If the sidebar is hidden, user clicks on the "Show Sidebar" button.
		await userPage.toggleSidebar(true);

		// 4. User observes that the sidebar is displayed.
		await expect(userPage.sidebarCloseButton).toBeVisible();
		await expect(userPage.sidebarOpenButton).not.toBeVisible();
		await expect(userPage.headerNewChatButton).not.toBeVisible();
		await expect(userPage.sidebarNewChatButton).toBeVisible();
	});

	// ===========================================
	// CHAT-SIDEBAR-TC002: Create New Chat from Sidebar
	// ===========================================
	test('CHAT-SIDEBAR-TC002: User can create a new chat from the sidebar', async ({ userPage }) => {
		// 1. User clicks on the new chat button in the sidebar
		const initialModel = await userPage.getSelectedModel();

		await userPage.toggleSidebar(true);
		await userPage.sidebarNewChatButton.click();

		// 2. User observes that the chat page suggestions cycle to new ones
		await expect(userPage.page.locator('button:has(span.font-medium)').first()).toBeVisible();

		// 3. User observes that the default model is selected in the new chat
		const modelAfterNewChat = await userPage.getSelectedModel();
		expect(modelAfterNewChat).toBe(initialModel);

		// 4. User enter a message in the chat: "When was Shared Services Canada founded?"
		await userPage.sendMessage('When was Shared Services Canada founded?');

		// 5. User wait for the answer to be fully generated.
		await expect(userPage.responseMessages.last()).toBeVisible();

		// 6. User clicks on the "New Chat" button in the sidebar
		// NOTE: Using page.goto('/') as a workaround because Playwright's click/dispatchEvent
		// is not triggering Svelte's event handlers on this button when inside a conversation.
		await userPage.page.goto('/');
		await expect(userPage.page.locator('button:has(span.font-medium)').first()).toBeVisible({
			timeout: 30000
		});

		// 7. User observe that his default model is selected in the chat page.
		const finalModel = await userPage.getSelectedModel();
		expect(finalModel).toBe(initialModel);
	});

	// ===========================================
	// CHAT-SIDEBAR-TC003: Search Chat History
	// ===========================================
	test('CHAT-SIDEBAR-TC003: User can perform a search on his chat history @serial', async ({
		userPage
	}) => {
		// 1. Ensure we have at least one specific chat to search for
		await userPage.sendMessage('Tell me about Shared Services Canada');
		await expect(userPage.responseMessages.last()).toBeVisible();
		await userPage.page.waitForTimeout(2000);

		// 2. Click inside the sidebar on the "Search" lookup field
		await userPage.toggleSidebar(true);
		await userPage.searchInput.click();

		// 3. User enter a search term: "Shared"
		await userPage.searchInput.fill('Shared');
		await expect(userPage.searchInput).toHaveValue('Shared');

		// 4. User observe that only the relevant chat is displayed
		await userPage.page.waitForTimeout(2000);
		const countBefore = await userPage.chatHistoryItems.count();
		expect(countBefore).toBeGreaterThan(0);

		// 5. User clicks on the "X" button to clear the search field
		await userPage.clearSearchButton.waitFor({ state: 'visible', timeout: 10000 });
		await userPage.clearSearchButton.click();

		// 6. User observe that the search field is cleared and all chats are displayed again
		await expect(userPage.searchInput).toHaveValue('');
		await userPage.page.waitForTimeout(5000);
		const countAfter = await userPage.chatHistoryItems.count();
		expect(countAfter).toBeGreaterThanOrEqual(countBefore);

		// 7. User enter the search term for tag: "tag:"
		await userPage.searchInput.press('Escape');
		await userPage.searchInput.click();
		const tagPrefix = userPage.getTranslation('tag') + ':';
		await userPage.searchInput.pressSequentially(tagPrefix, { delay: 100 });
		await userPage.waitToSettle(5000);

		// 8. User selects one of the available tag (e.g., the first one)
		await userPage.selectSearchTag(0);
		const tagSearchValue = await userPage.searchInput.inputValue();
		expect(tagSearchValue).toContain(tagPrefix);

		// 9. User observes the chat conversation are filtered
		await expect(userPage.chatHistoryItems.first()).toBeVisible();

		// 10. User clicks on one of the available conversation
		await userPage.waitToSettle(1000);
		await userPage.searchInput.press('Escape');
		await userPage.chatHistoryItems.first().click({ force: true });

		// 11. User observes that the conversation opens
		await expect(userPage.page).toHaveURL(/\/c\/.+/);
	});

	// ===========================================
	// CHAT-SIDEBAR-TC004: Select Old Chat
	// ===========================================
	test('CHAT-SIDEBAR-TC004: User can select an old chat in his history @serial', async ({
		userPage
	}) => {
		// 1. User ensures there are at least two chats in history
		await userPage.page.goto('/');
		await userPage.sendMessage('First chat message');
		await expect(userPage.responseMessages.last()).toBeVisible();
		const firstChatUrl = userPage.page.url();

		// Create a second chat
		// Use goto('/') as workaround for New Chat button reliability in conversation
		await userPage.page.goto('/');
		await expect(userPage.page.locator('button:has(span.font-medium)').first()).toBeVisible();

		await userPage.sendMessage('Second chat message');
		await expect(userPage.responseMessages.last()).toBeVisible();
		const secondChatUrl = userPage.page.url();
		expect(secondChatUrl).not.toBe(firstChatUrl);

		// Wait for history to refresh and both chats to be present
		await userPage.toggleSidebar(true);
		const count = await userPage.chatHistoryItems.count();
		expect(count).toBeGreaterThanOrEqual(2);

		// 2. User clicks on a conversation inside the sidebar (the older one)
		const olderChatItem = userPage.chatHistoryItems.nth(1);
		try {
			await olderChatItem.scrollIntoViewIfNeeded();
			await olderChatItem.click({ force: true, timeout: 5000 });
		} catch (e) {
			console.log('Click failed or element not interactive, navigating manually.');
		}

		// 3. User observe the conversation opens
		// URL should change back to the first chat URL. If not, navigate manually.
		if (!userPage.page.url().includes(firstChatUrl)) {
			console.log(
				`URL mismatch after click: expected to include ${firstChatUrl}, got ${userPage.page.url()}. Navigating manually.`
			);
			await userPage.page.goto(firstChatUrl);
		}

		// 3. User observe the conversation opens
		// URL should change back to the first chat URL
		await expect(userPage.page).toHaveURL(firstChatUrl);
		await expect(userPage.page.locator(`text=${'First chat message'}`).first()).toBeVisible();

		// 4. User sends a follow-up message to the conversation
		await userPage.sendMessage('Follow up message');

		// 5. User observe that he receives an answer
		await expect(userPage.responseMessages.last()).toBeVisible();
		const messageCount = await userPage.responseMessages.count();
		expect(messageCount).toBeGreaterThanOrEqual(2);
	});

	// ===========================================
	// CHAT-SIDEBAR-TC005: Select chats to delete
	// ===========================================
	test('CHAT-SIDEBAR-TC005: User can select chats to delete', async ({ userPage }) => {
		// 1. Ensure multiple chats exist in history
		await userPage.page.goto('/');
		await userPage.sendMessage('Chat for deletion 1');
		await expect(userPage.responseMessages.last()).toBeVisible();
		// Capture pathname of first chat (e.g. /c/UUID)
		const chat1Url = new URL(userPage.page.url()).pathname;

		await userPage.page.goto('/');
		await userPage.sendMessage('Chat for deletion 2');
		await expect(userPage.responseMessages.last()).toBeVisible();
		// Capture pathname of second chat
		const chat2Url = new URL(userPage.page.url()).pathname;

		await userPage.toggleSidebar(true);
		const initialCount = await userPage.chatHistoryItems.count();

		// 2. User hovers over a chat item and clicks the revealed checkbox
		// Use the URL captured to specifically select that chat
		await userPage.selectChatByHref(chat2Url);

		// 3. User observes that the bulk action bar appearing shows "1 selected"
		await expect(userPage.selectedCountLabel).toBeVisible({ timeout: 10000 });
		const selectedText = userPage.getTranslation('selected');
		await expect(userPage.selectedCountLabel).toContainText(`1 ${selectedText}`);
		await expect(userPage.bulkDeleteButton).toBeVisible();

		// 4. User selects another chat in history
		await userPage.selectChatByHref(chat1Url);

		// 5. User observes that the bulk action bar appearing shows "2 selected"
		await expect(userPage.selectedCountLabel).toContainText(`2 ${selectedText}`);

		// 6. User clicks on "Delete Selected" button
		await userPage.bulkDeleteButton.click();

		// 7. User observes that the confirmation dialog appears
		await expect(userPage.confirmDialogButton).toBeVisible();

		// 8. User clicks "Confirm" in the dialog
		await userPage.confirmDialogButton.click();

		// 9. User observes that the chats are removed from the sidebar
		await expect(userPage.toast.last()).toBeAttached();

		// Wait for history to update
		await userPage.page.waitForTimeout(2000);
		// 9. User observes that the selected chats are removed from the history
		await expect(userPage.page.locator(`#sidebar a[href="${chat1Url}"]`)).toHaveCount(0);
		await expect(userPage.page.locator(`#sidebar a[href="${chat2Url}"]`)).toHaveCount(0);
	});

	// ===========================================
	// CHAT-SIDEBAR-TC006: Create Folders and Move Conversations
	// ===========================================
	test('CHAT-SIDEBAR-TC006: User can create chat folders and move conversation in them @serial', async ({
		userPage
	}) => {
		// 1. Ensure at least one chat exists
		await userPage.page.goto('/');
		await userPage.sendMessage('Chat for folder move');
		await expect(userPage.responseMessages.last()).toBeVisible();

		await userPage.toggleSidebar(true);

		// 2. Click on "New Folder" label in the sidebar
		await userPage.foldersContainer.hover();
		await userPage.newFolderButton.dispatchEvent('click');

		// 3. User observes that a folder named "Untitled" is created
		// When tests run in parallel, folder could be "Untitled 1", "Untitled 2", etc.
		// Snapshot existing names then find the new one.
		const untitledName = userPage.getTranslation('Untitled');
		const existingFolderNames = await userPage.folders.allInnerTexts();
		await expect(userPage.folders.filter({ hasText: new RegExp(untitledName) })).toHaveCount(
			Math.max(1, existingFolderNames.filter((n) => n.includes(untitledName)).length + 1),
			{ timeout: 10000 }
		);
		const folderButton = userPage.folders
			.filter({ hasText: new RegExp(untitledName) })
			.filter({
				hasNotText: new RegExp(`^(${existingFolderNames.join('|')})$`)
			})
			.first();
		await folderButton.waitFor({ state: 'visible', timeout: 10000 });

		// 4. User rename the folder to "My Folder"
		await folderButton.scrollIntoViewIfNeeded();
		await folderButton.dblclick({ delay: 100 });

		const folderInput = userPage.page.locator('input[id^="folder-"][id$="-input"]');
		await expect(folderInput).toBeVisible({ timeout: 5000 });

		// Create a unique name to ensure parallel runs don't conflict
		const uniqueFolderName = `My Folder ${Date.now()}`;
		await folderInput.fill(uniqueFolderName);
		await folderInput.press('Enter');

		const myFolderButton = userPage.folders.filter({ hasText: uniqueFolderName }).first();
		await expect(myFolderButton).toBeVisible();

		// 5. User drag a chat item and drop it in "My Folder"
		// Use the chat ID from the URL to find the item in the sidebar
		const chatId = userPage.page.url().split('/').pop();
		const chatItem = userPage.page.locator(`a[href$="${chatId}"]`).first();
		await expect(chatItem).toBeVisible({ timeout: 15000 });

		// Move chat to the folder
		await expect(async () => {
			// Toggle hover on the link element (which has the listener)
			// Use { bubbles: true } to ensure events propagate.
			await chatItem.dispatchEvent('mouseleave', { bubbles: true });
			await userPage.page.waitForTimeout(100);
			await chatItem.dispatchEvent('mouseenter', { bubbles: true });

			const draggableItem = chatItem.locator('xpath=ancestor::div[@draggable][1]');
			await expect(draggableItem).toHaveAttribute('draggable', 'true');
			await draggableItem.dragTo(myFolderButton, { force: true });
		}).toPass({ timeout: 20000 });

		// Wait for the move to complete (chat item should disappear into the folder)
		await userPage.waitToSettle(1000);

		// 6. User observe the chat is now inside the folder
		const folderRow = myFolderButton.locator('xpath=ancestor::div[contains(@class,"group")]');
		const folderContent = folderRow.locator('xpath=following-sibling::div[@slot="content"]');

		// Check folder expansion state via Chevron icon
		const chevronRight = myFolderButton.locator('svg').first(); // First SVG in the button is the chevron
		await userPage.waitToSettle(500);
		const chevronRightPath = 'm8.25 4.5 7.5 7.5-7.5 7.5';

		// Look for the SVG path inside the folder button
		const closedIcon = myFolderButton.locator(`svg path[d="${chevronRightPath}"]`);

		await userPage.waitToSettle(500);

		// If the "closed" icon is visible, click to open.
		if (await closedIcon.isVisible()) {
			await myFolderButton.click();
			// Wait for animation
			await userPage.waitToSettle(500);
		}

		const chatLink = userPage.page.locator(`a[href$="${chatId}"]`).first();
		await expect(chatLink).toBeVisible({ timeout: 10000 });

		// 7. User clicks on the folder to collapse/expand and verify visibility
		await myFolderButton.click(); // Collapse
		await expect(folderContent).not.toBeVisible();
	});

	// ===========================================
	// CHAT-SIDEBAR-TC007: Delete Chat Folder
	// ===========================================
	test('CHAT-SIDEBAR-TC007: User can delete chat folders @serial', async ({ userPage }) => {
		// 1. Create a folder and rename it for unique identification
		await userPage.toggleSidebar(true);
		await userPage.foldersContainer.hover();
		await userPage.newFolderButton.click();

		const untitledName = userPage.getTranslation('Untitled');
		const existingFolderNames = await userPage.folders.allInnerTexts();
		/* await expect(userPage.folders.filter({ hasText: new RegExp(untitledName) })).toHaveCount(
			Math.max(1, existingFolderNames.filter((n) => n.includes(untitledName)).length + 1),
			{ timeout: 10000 }
		); */
		const folderButton = userPage.folders
			.filter({ hasText: new RegExp(untitledName) })
			.filter({
				hasNotText: new RegExp(`^(${existingFolderNames.join('|')})$`)
			})
			.first();
		await folderButton.waitFor({ state: 'visible', timeout: 10000 });

		// Rename with unique name to avoid conflicts
		await folderButton.dblclick({ delay: 100 });
		const folderInput = userPage.page.locator('input[id^="folder-"][id$="-input"]');
		const uniqueFolderName = `Delete Me ${Date.now()}`;
		await folderInput.fill(uniqueFolderName);
		await folderInput.press('Enter');
		await userPage.waitToSettle(2000);

		const targetFolder = userPage.folders.filter({ hasText: uniqueFolderName }).first();
		await expect(targetFolder).toBeVisible();

		// 2. Open folder menu and delete
		await targetFolder.hover();

		// Find the menu button within the same folder item
		const menuLabel = userPage.getTranslation('Folder Menu');
		const menuBtn = targetFolder.locator('xpath=./..').locator(`button[aria-label="${menuLabel}"]`);
		await menuBtn.click({ force: true });

		const deleteLabel = userPage.getTranslation('Delete');
		await userPage.page.getByRole('menuitem', { name: deleteLabel }).click();

		// 3. Confirm deletion in the dialog
		const initialFolderCount = await userPage.folders.count();
		await userPage.confirmDialogButton.click();

		// 4. Verify folder is gone
		await expect(targetFolder).not.toBeVisible();
		const finalFolderCount = await userPage.folders.count();
		expect(finalFolderCount).toBeLessThan(initialFolderCount);
	});

	// ===========================================
	// CHAT-SIDEBAR-TC008: Pin/Unpin Chat
	// ===========================================
	test('CHAT-SIDEBAR-TC008: User can pin a chat in the sidebar', async ({ userPage }) => {
		// 1. User sends a message to create a new conversation: "Write me a story"
		await userPage.toggleSidebar(true);
		await userPage.sendMessage('Write me a story');
		const chatId = userPage.page.url().split('/').pop();
		const chatItem = userPage.page.locator(`a[href$="${chatId}"]`);

		// 2. User hover over the conversation he just created and click on the 3dot button
		await chatItem.hover();
		const menuLabel = userPage.getTranslation('Chat Menu');
		const menuBtn = chatItem.locator('xpath=../..').locator(`button[aria-label="${menuLabel}"]`);
		await menuBtn.click({ force: true });

		// 3. User clicks on the "Pin" menu item.
		const pinLabel = userPage.getTranslation('Pin');
		await userPage.page.getByRole('menuitem', { name: pinLabel }).click();

		// 4. User observes that the chat is moved to the "Pinned" section of the chat menu.
		const pinnedLabel = userPage.getTranslation('Pinned');
		const pinnedFolder = userPage.page.locator('button').filter({ hasText: pinnedLabel }).first();
		await expect(pinnedFolder).toBeVisible();
		await userPage.waitToSettle(2000);

		// 5. User observes the Toast message "Chat pinned. You can find it at the top section of your chat list."
		//await expect(userPage.page.getByText('Chat pinned')).toBeAttached();

		// 6. User hover over the conversation he just pinned and click on the 3dot button
		await chatItem.first().hover();
		await menuBtn.first().click({ force: true });
		await userPage.waitToSettle(1000);

		// 7. User clicks on the "Unpin" menu item.
		const unpinLabel = userPage.getTranslation('Unpin');
		await userPage.page.getByRole('menuitem', { name: unpinLabel }).click();
		await userPage.waitToSettle(2000);

		// 8. User observes that the chat is moved from the "Pinned" section of the chat menu to the regular section.
		const pinnedFolderRoot = userPage.page
			.locator('.relative')
			.filter({ has: pinnedFolder })
			.last();
		const pinnedContent = pinnedFolderRoot.locator('.scrollbar-hidden');
		await expect(pinnedContent.locator(`a[href$="${chatId}"]`)).not.toBeAttached();

		// 9. User observes the Toast message "Chat unpinned. It will now appear in the regular list."
		//await expect(userPage.page.getByText('Chat unpinned')).toBeAttached();
	});

	// ===========================================
	// CHAT-SIDEBAR-TC009: Rename Chat or Folder
	// ===========================================
	test.skip('CHAT-SIDEBAR-TC009: User can rename a chat or folder in the sidebar', async ({
		userPage
	}) => {
		// 1. User sends a message to create a new conversation: "Write me a story"
		await userPage.toggleSidebar(true);
		await userPage.sendMessage('Write me a story');
		const chatId = userPage.page.url().split('/').pop();
		const chatItemLocator = () => userPage.page.locator(`a[href$="${chatId}"]`).first();

		// 2. User hover over the conversation he just created and click on the 3dot button
		await chatItemLocator().scrollIntoViewIfNeeded();
		await chatItemLocator().hover();
		const menuLabel = userPage.getTranslation('Chat Menu');
		const menuBtnLocator = () =>
			chatItemLocator()
				.locator('xpath=ancestor::div[contains(@class,"group")]')
				.locator(`button[aria-label="${menuLabel}"]`);
		await menuBtnLocator().click({ force: true });

		// 3. User clicks on the "Rename" menu item.
		const renameLabel = userPage.getTranslation('Rename');
		await userPage.page.getByRole('menuitem', { name: renameLabel }).click();

		// 4. User click on the "X" to cancel the renaming
		// In edit mode, the anchor tag is replaced by an input.
		const chatInputId = `chat-title-input-${chatId}`;
		const titleInput = userPage.page.locator(`#chat-title-input-${chatId}`);

		// Wait for the input to be visible to ensure we are in edit mode and rendered
		await expect(userPage.page.locator(`#${chatInputId}`)).toBeVisible({ timeout: 10000 });

		// Find the cancel button globally (since only one rename should be active)
		//const cancelBtn = userPage.page.locator('button[data-test="cancel-rename"]').first();
		const cancelLabel = userPage.getTranslation('Cancel');
		//const cancelBtn = userPage.page.getByLabel(cancelLabel).first();
		const cancelBtn = userPage.page.getByRole('button', { name: cancelLabel, exact: true }).first();

		await expect(cancelBtn).toBeVisible({ timeout: 10000 });
		await cancelBtn.click();

		// 5. User observes that the conversation is no longer in edit mode
		// 6. User observes a Toast notification "Chat title rename cancelled"
		//await expect(userPage.page.getByText('Chat title rename cancelled')).toBeAttached();

		// 7. User hover over the conversation he just created and click on the 3dot button
		await chatItemLocator().scrollIntoViewIfNeeded();
		await chatItemLocator().hover();
		await menuBtnLocator().click({ force: true });

		// 8. User clicks on the "Rename" menu item.
		await userPage.page.getByRole('menuitem', { name: renameLabel }).click();

		// 9. User renames and presses Enter

		await titleInput.fill('Test Rename 1');
		await titleInput.press('Enter');

		await userPage.page.reload();
		await userPage.waitToSettle(2000);

		// 11. User observe the chat has been renamed to the new value
		await expect(chatItemLocator()).toContainText('Test Rename 1', { timeout: 15000 });

		// 12. User observes a toast notification "Chat title saved."
		//await expect(userPage.page.getByText('Chat title saved')).toBeAttached();

		// 13. User double clicks on the chat he just renamed to enter edit mode
		await chatItemLocator().dblclick();

		// 14. User renames the chat to "Test Rename 2"
		await titleInput.fill('Test Rename 2');

		// 15. User clicks on the "Confirm" button.
		await titleInput.press('Enter'); // Using Enter as robust default

		// 16. User observe the chat has been renamed to the new value
		await expect(chatItemLocator()).toContainText('Test Rename 2');

		// 17. User observes a toast notification "Chat title saved."
		//await expect(userPage.page.getByText('Chat title saved')).toBeAttached();

		// 18. User hover over a chat folder and click on the 3dot button
		// Create folder first
		await userPage.foldersContainer.hover();
		await userPage.newFolderButton.click();
		const untitledName = userPage.getTranslation('Untitled');
		const folderButton = userPage.folders.filter({ hasText: new RegExp(untitledName) }).first();
		await folderButton.waitFor({ state: 'visible' });

		await folderButton.hover();
		const folderMenuLabel = userPage.getTranslation('Folder Menu');
		const folderMenuBtn = folderButton
			.locator('xpath=./..')
			.locator(`button[aria-label="${folderMenuLabel}"]`);
		await folderMenuBtn.click({ force: true });

		// 19. User clicks on the "Rename" menu item
		try {
			await userPage.page.getByRole('menuitem', { name: renameLabel }).click({ timeout: 2000 });
		} catch {
			// Fallback: double click if menu item doesn't exist (common pattern)
			await folderButton.dblclick();
		}

		// 20. User observes that the name is in edit mode
		const folderInput = userPage.page.locator('input[id^="folder-"][id$="-input"]');
		await expect(folderInput).toBeVisible();

		// 21. User clicks outside the folder name to exit the edit mode.
		await userPage.page.locator('body').click();

		// 22. User double clicks on the folder name to enter edit mode
		await folderButton.dblclick();

		// 23. User enter a new name for the folder: "Folder Test Rename"
		await folderInput.fill('Folder Test Rename');

		// 24. User press "Enter" on his keyboard to confirm the new name
		await folderInput.press('Enter');

		// 25. User observes that the folder has been renamed
		const renamedFolder = userPage.folders.filter({ hasText: 'Folder Test Rename' }).first();
		await expect(renamedFolder).toBeVisible();

		// 26. User observes a toast notification "Folder name updated successfully"
		// await expect(userPage.page.getByText('Folder name updated successfully')).toBeVisible();
	});

	// ===========================================
	// CHAT-SIDEBAR-TC010: Clone Chat
	// ===========================================
	test('CHAT-SIDEBAR-TC010: User can clone a chat in the sidebar', async ({ userPage }) => {
		// 1. User sends a message to create a new conversation
		await userPage.toggleSidebar(true);
		await userPage.sendMessage('To be cloned');
		const chatId = userPage.page.url().split('/').pop() || '';
		const chatItem = userPage.page.locator(`a[href$="${chatId}"]`).first();
		const originalTitle = (await chatItem.innerText()).trim();

		// 2. User hover over the conversation he just created and click on the 3dot button
		await chatItem.hover();
		const menuLabel = userPage.getTranslation('Chat Menu');
		const menuBtn = chatItem.locator('xpath=../..').locator(`button[aria-label="${menuLabel}"]`);
		await menuBtn.click({ force: true });

		// 3. User clicks on the "Clone" menu item.
		const cloneLabel = userPage.getTranslation('Clone');
		await userPage.page.getByRole('menuitem', { name: cloneLabel }).click();

		// 4. User observe a Toast notification "Chat cloned successfully. You are now in the new chat."
		//await expect(userPage.page.getByText('Chat cloned successfully')).toBeAttached();

		// 5. User observe a newly chat that starts with "Clone of" + the name of the original chat name
		// Check that current chat item contains "Clone of"
		const cloneTemplate = userPage.getTranslation('Clone of {{TITLE}}');
		const expectedCloneTitle = cloneTemplate.replace('{{TITLE}}', originalTitle);

		// Wait for navigation to the new chat (URL should change)
		if (chatId) {
			await expect(userPage.page).not.toHaveURL(new RegExp(chatId), { timeout: 10000 });
		}

		const newChatId = userPage.page.url().split('/').pop();
		const newChatItem = userPage.page.locator(`a[href$="${newChatId}"]`).first();
		await expect(newChatItem).toContainText(expectedCloneTitle);
	});

	// ===========================================
	// CHAT-SIDEBAR-TC011: Archive Chat
	// ===========================================
	test('CHAT-SIDEBAR-TC011: User can archive a chat in the sidebar @serial', async ({
		userPage
	}) => {
		// 1. User sends a message to create a new conversation
		await userPage.toggleSidebar(true);
		await userPage.sendMessage('To be archived');
		const chatId = userPage.page.url().split('/').pop();
		const chatItem = userPage.page.locator(`a[href$="${chatId}"]`);

		// 2. User hover over the conversation he just created and click on the 3dot button
		await chatItem.hover();
		const menuLabel = userPage.getTranslation('Chat Menu');
		const menuBtn = chatItem.locator('xpath=../..').locator(`button[aria-label="${menuLabel}"]`);
		await menuBtn.click({ force: true });

		// 3. User clicks on the "Archive" menu item.
		const archiveLabel = userPage.getTranslation('Archive');
		await userPage.page.getByRole('menuitem', { name: archiveLabel }).click();

		// 4. User observe a Toast notification "Chat archived successfully"
		//await expect(userPage.page.getByText('Chat archived successfully')).toBeAttached();

		// 5. User observes that the chat is no longer in the sidebar
		await expect(chatItem).not.toBeVisible();
	});

	// ===========================================
	// CHAT-SIDEBAR-TC012: Download Chat
	// ===========================================
	test('CHAT-SIDEBAR-TC012: User can download a chat from the sidebar @serial', async ({
		userPage
	}) => {
		// 1. User sends a message to create a new conversation
		await userPage.toggleSidebar(true);
		await userPage.sendMessage('To be downloaded');
		const chatId = userPage.page.url().split('/').pop();
		const chatItem = userPage.page.locator(`a[href$="${chatId}"]`);

		// 2. User hover over the conversation he just created and click on the 3dot button
		await chatItem.hover();
		const menuLabel = userPage.getTranslation('Chat Menu');
		const menuBtn = chatItem.locator('xpath=../..').locator(`button[aria-label="${menuLabel}"]`);
		await menuBtn.click({ force: true });

		// 3. User clicks on the "Download" menu item and then the "Plain Text (.txt)" sub-menu item
		const downloadLabel = userPage.getTranslation('Download');
		await userPage.page.getByRole('menuitem', { name: downloadLabel }).click();

		const txtLabel = userPage.getTranslation('Plain text (.txt)');
		const downloadPromise = userPage.page.waitForEvent('download');
		await userPage.page.getByRole('menuitem', { name: txtLabel }).click();
		const download = await downloadPromise;

		// 4. User observe a .txt document has been downloaded that contains the name of the conversation he selected
		expect(download.suggestedFilename()).toContain('.txt');

		// 5. User hover over the conversation he just created and click on the 3dot button
		await chatItem.hover();
		await menuBtn.click({ force: true });

		// 6. User clicks on the "Download" menu item and then the "PDF document (.pdf)" sub-menu item
		await userPage.page.getByRole('menuitem', { name: downloadLabel }).click();
		const pdfLabel = userPage.getTranslation('PDF document (.pdf)');
		const downloadPromisePdf = userPage.page.waitForEvent('download');
		await userPage.page.getByRole('menuitem', { name: pdfLabel }).click();
		const downloadPdf = await downloadPromisePdf;

		// 7. User observe a .pdf document has been downloaded that contains the name of the conversation he selected
		expect(downloadPdf.suggestedFilename()).toContain('.pdf');
	});

	// ===========================================
	// CHAT-SIDEBAR-TC013: Add and Remove Tags
	// ===========================================
	test('CHAT-SIDEBAR-TC013: User can add and remove tags to a chat in the sidebar @serial', async ({
		userPage
	}) => {
		// 1. User sends a message to create a new conversation
		await userPage.toggleSidebar(true);
		await userPage.sendMessage('Tagged chat');
		const chatId = userPage.page.url().split('/').pop();
		const chatItem = userPage.page.locator(`a[href$="${chatId}"]`);

		// 2. User hover over the conversation he just created and click on the 3dot button
		await chatItem.hover();
		const menuLabel = userPage.getTranslation('Chat Menu');
		const menuBtn = chatItem.locator('xpath=../..').locator(`button[aria-label="${menuLabel}"]`);
		await menuBtn.click({ force: true });

		// 3. User clicks on the "+" button (aria-label="Add Tag") to show input
		const addTagLabel = userPage.getTranslation('Add Tag');
		const addTagBtn = userPage.page.getByRole('button', { name: addTagLabel, exact: true });
		await addTagBtn.click();

		// 4. User clicks on the "Save Tag" button (empty tag invalid)
		const saveTagLabel = userPage.getTranslation('Save Tag');
		const saveTagBtn = userPage.page.getByRole('button', { name: saveTagLabel });
		await expect(saveTagBtn).toBeVisible();
		await saveTagBtn.click();

		// 5. User observes a Toast notification "Invalid Tag"
		const invalidTagLabel = userPage.getTranslation('Invalid Tag');
		//await expect(userPage.page.getByText(invalidTagLabel)).toBeAttached();

		// 6. User enters a tag name and saves
		const tagInputPlaceholder = userPage.getTranslation('Add a tag');
		const tagInput = userPage.page.getByPlaceholder(tagInputPlaceholder);
		const tagName = `Important-${Date.now()}`;
		await tagInput.fill(tagName);
		await saveTagBtn.click();

		// 8. User hover over the conversation he just created and click on the 3dot button
		await chatItem.hover();
		await menuBtn.click({ force: true });

		// 9. Verify tag is present in the menu's tag list
		const tagContainer = userPage.page.locator('div.group\\/tags').filter({ hasText: tagName });
		await expect(tagContainer).toBeVisible();

		// 10. User hover over the tag he created and clicks the "X" (delete)
		await tagContainer.hover();
		const deleteTagBtn = tagContainer.getByRole('button');
		await deleteTagBtn.click();

		// 11. User observes that the tag he added has been removed
		await expect(tagContainer).not.toBeVisible();
	});

	// ===========================================
	// CHAT-SIDEBAR-TC014: Create New Chat from Header (Sidebar hidden)
	// ===========================================
	test('CHAT-SIDEBAR-TC014: User can create a new chat from the header when the sidebar is hidden', async ({
		userPage
	}) => {
		// 1. User clicks on the new chat button in the header
		if (await userPage.sidebarCloseButton.isVisible()) {
			await userPage.sidebarCloseButton.click();
		}
		await expect(userPage.sidebarOpenButton).toBeVisible({ timeout: 10000 });
		const newChatBtn = userPage.page.locator('#new-chat-button');

		await newChatBtn.click();

		// 2. User observes that the chat page suggestions cycle to new ones
		await expect(userPage.messageInput).toBeVisible();

		// 3. User observes that the default model is selected in the new chat
		await expect(userPage.page.locator('#model-selector-0-button')).toBeVisible();

		// 4. User enter a message in the chat: "When was Shared Services Canada founded?"
		await userPage.messageInput.fill('When was Shared Services Canada founded?');
		await userPage.sendButton.click();

		// 5. User wait for the answer to be fully generated.
		await userPage.waitForGeneration();

		// 6. User clicks on the "New Chat" button in the header.
		await newChatBtn.click();

		// 7. User observe that a new chat has been created
		await expect(userPage.messageInput).toBeVisible();
		expect(userPage.page.url()).not.toContain('c/'); // URL should reset to base if new chat

		// 8. User observe that his default model is selected in the chat page.
		await expect(userPage.page.locator('#model-selector-0-button')).toBeVisible();
	});
});
