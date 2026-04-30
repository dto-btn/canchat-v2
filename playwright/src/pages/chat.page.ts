import * as fs from 'fs';
import * as path from 'path';
import { BasePage, type Language } from './base.page';
import { type Page, type Locator, expect } from '@playwright/test';

export class ChatPage extends BasePage {
	readonly messageInput: Locator;
	sendButton!: Locator;
	callButton!: Locator;
	stopGenerationButton!: Locator;
	readonly regenerateButton: Locator;
	readonly responseMessages: Locator;
	readonly responseMessageGenerating: Locator;
	readonly responseSelector: string = '#response-content-container';
	chatStatusDescription!: Locator;
	readonly headerNewChatButton: Locator;
	readonly chatHistoryItems: Locator;
	readonly searchInput: Locator;
	readonly clearSearchButton: Locator;
	selectedCountLabel!: Locator;
	bulkDeleteButton!: Locator;
	newFolderButton!: Locator;
	foldersContainer!: Locator;
	folders!: Locator;
	readonly searchTagItems: Locator;
	readonly searchOptionItems: Locator;
	searchResultsLabel!: Locator;

	constructor(page: Page, lang: Language = 'en-GB') {
		super(page, lang);

		this.messageInput = page.locator('#chat-input');
		this.regenerateButton = page.locator('div:nth-child(8) > .visible');
		this.responseMessages = page.locator('#response-content-container p');
		this.responseMessageGenerating = page.locator('.space-y-2');
		this.responseSelector = '#response-content-container';
		this.chatStatusDescription = page.locator('.status-description');
		this.headerNewChatButton = page.locator('#new-chat-button');
		this.chatHistoryItems = page.locator('#sidebar a[href^="/c/"]');
		this.searchInput = page.locator('#chat-search input');
		this.clearSearchButton = page.locator('#clear-search-button');
		this.selectedCountLabel = page.locator('span:has-text("selected")');
		this.bulkDeleteButton = page.locator('button[title="Delete Selected"]');
		this.newFolderButton = page.locator('button[aria-label="New Folder"]');
		this.foldersContainer = page.locator('.group').filter({ hasText: 'Chats' }).first();
		this.folders = page.locator('button[id^="folder-"][id$="-button"]');
		this.searchTagItems = page.locator('button[id^="search-tag-"]');
		this.searchOptionItems = page.locator('button[id^="search-option-"]');
		this.searchResultsLabel = page.locator('[aria-live="polite"].sr-only');

		this.updateLanguage(lang);
	}

	// ===================================================
	// Chat Helpers
	// ===================================================

	/**
	 * Override to update Chat-specific locators
	 * @param lang The new language to switch to ('en-GB' or 'fr-CA')
	 */
	override updateLanguage(lang: Language) {
		super.updateLanguage(lang);

		this.sendButton = this.page.getByRole('button', {
			name: this.t['Send message'] || 'Send message'
		});
		this.callButton = this.page.getByRole('button', {
			name: 'Call'
		});
		this.stopGenerationButton = this.page.locator('button.bg-white.hover\\:bg-gray-100');
		this.bulkDeleteButton = this.page.locator(
			`button[title="${this.t['Delete Selected'] || 'Delete Selected'}"]`
		);
		this.selectedCountLabel = this.page.locator(
			`span:has-text("${this.t['selected'] || 'selected'}")`
		);
		this.newFolderButton = this.page.locator(
			`button[aria-label="${this.t['New Folder'] || 'New Folder'}"]`
		);
		this.foldersContainer = this.page
			.locator('.group')
			.filter({ hasText: this.t['Chats'] || 'Chats' })
			.first();
		this.searchResultsLabel = this.page.locator('[aria-live="polite"].sr-only');
	}

	/**
	 * Starts a new chat session
	 */
	async newChat() {
		await this.toggleSidebar(true);
		await this.sidebarNewChatButton.click();
		await expect(this.messageInput).toBeVisible();
	}

	/**
	 * Starts a new chat session using the header button
	 */
	async newChatFromHeader() {
		await this.toggleSidebar(false);
		await this.headerNewChatButton.waitFor({ state: 'visible' });
		await this.headerNewChatButton.click();
		await expect(this.messageInput).toBeVisible();
	}

	/**
	 * Sends a message and waits for the response generation
	 */
	async getSelectedModel(index: number = 0): Promise<string> {
		const button = this.page.locator(`#model-selector-${index}-button`);
		const text = await button.locator('span').first().innerText();
		return text.trim();
	}

	async getSuggestions(): Promise<string[]> {
		const suggestionButtons = this.page.locator('button:has(span.font-medium)');
		return await suggestionButtons.allInnerTexts();
	}

	async sendMessage(text: string, waitForReply: boolean = true, idleMessage: string = '') {
		await expect(this.messageInput).toBeVisible();

		await this.messageInput.fill(text);
		await expect(this.sendButton).toBeEnabled();
		await this.sendButton.click();

		if (waitForReply) {
			await this.waitForGeneration(idleMessage);
		}
	}

	/**
	 * Selects a chat in the history by hovering and clicking the checkbox
	 * @param index The index of the chat to select (0-indexed)
	 */
	async selectChat(index: number) {
		const item = this.chatHistoryItems.nth(index);
		await item.hover();
		await item.locator('.checkbox-area').click();
	}

	/**
	 * Selects a chat in the history by its href (URL)
	 * @param href The href of the chat to select
	 */
	async selectChatByHref(href: string) {
		const item = this.page.locator(`#sidebar a[href="${href}"]`);
		await item.waitFor({ state: 'visible' });
		await item.hover();
		await item.locator('.checkbox-area').click();
	}

	/**
	 * Waits for the response to complete
	 */
	async waitForGeneration(idleMessage: string = '') {
		const selector = this.responseSelector;
		await this.page.waitForSelector(selector, { state: 'visible' });
		const stopBtn = this.stopGenerationButton;
		await stopBtn.waitFor({ state: 'hidden', timeout: 180000 });

		await this.page.waitForFunction(
			({ selector, idleMessage }) => {
				const el = document.querySelector(selector);
				if (!el) return false;
				const text = el.textContent?.trim() || '';
				return text.length > 0 && (idleMessage.length === 0 || !text.includes(idleMessage));
			},
			{ selector, idleMessage },
			{ timeout: 180000 }
		);

		await this.waitToSettle(1500);
	}

	/**
	 * Opens the model selector dropdown for a specific selector index
	 */
	private async openModelSelector(selectorIndex: number = 0): Promise<Locator> {
		const modelDropdown = this.page.locator(`#model-selector-${selectorIndex}-button`);
		await modelDropdown.waitFor({ state: 'visible' });
		await modelDropdown.click();
		const searchInput = this.page.locator('#model-search-input');
		await searchInput.waitFor({ state: 'visible' });
		return searchInput;
	}

	/**
	 * Returns the model list items inside the dropdown
	 */
	private getModelListItems(): Locator {
		return this.page
			.locator('div.max-h-64 button')
			.filter({ has: this.page.locator('img[alt="Model"]') });
	}

	/**
	 * Returns the number of available models in the selector
	 */
	async getAvailableModelCount(): Promise<number> {
		const searchInput = await this.openModelSelector(0);
		const count = await this.getModelListItems().count();
		await this.page.keyboard.press('Escape');
		await searchInput.waitFor({ state: 'hidden' }).catch(() => {});
		return count;
	}

	/**
	 * Returns the number of models matching a search term
	 */
	async getFilteredModelCount(selectorIndex: number, searchTerm: string): Promise<number> {
		const searchInput = await this.openModelSelector(selectorIndex);
		await searchInput.fill(searchTerm);
		const count = await this.getModelListItems().count();
		await this.page.keyboard.press('Escape');
		await searchInput.waitFor({ state: 'hidden' }).catch(() => {});
		return count;
	}

	/**
	 * Add a model by index using keyboard navigation
	 * @param selectorIndex The model selector index to target
	 * @param modelIndex The index of the model in the list (0-based)
	 */
	async addModelByIndex(selectorIndex: number, modelIndex: number): Promise<string> {
		const searchInput = await this.openModelSelector(selectorIndex);
		await searchInput.fill('');
		for (let i = 0; i < modelIndex; i += 1) {
			await this.page.keyboard.press('ArrowDown');
		}
		await this.page.keyboard.press('Enter');
		await searchInput.waitFor({ state: 'hidden' }).catch(() => {});
		return await this.getSelectedModelLabel(selectorIndex);
	}

	/**
	 * Selects a model using a search term
	 */
	async selectModelBySearch(selectorIndex: number, searchTerm: string): Promise<string> {
		const searchInput = await this.openModelSelector(selectorIndex);
		await searchInput.fill(searchTerm);
		const modelItems = this.getModelListItems();
		await modelItems.first().waitFor({ state: 'visible' });
		await this.page.keyboard.press('Enter');
		await searchInput.waitFor({ state: 'hidden' }).catch(() => {});
		return await this.getSelectedModelLabel(selectorIndex);
	}

	/**
	 * Returns the selected model label for a given selector index
	 */
	async getSelectedModelLabel(selectorIndex: number): Promise<string> {
		const label = this.page.locator(`#model-selector-${selectorIndex}-button span`).first();
		await label.waitFor({ state: 'visible' });
		return (await label.innerText()).trim();
	}

	/**
	 * Adds a new model selector (up to 3)
	 */
	async clickModelSelector(): Promise<void> {
		// The label is translated but not the name of the button
		//const addLabel = this.getTranslation('Add Model');
		await this.page.getByRole('button', { name: 'Add Model' }).click();
	}

	/**
	 * Removes a model selector by index (1 or 2)
	 */
	async removeModelSelector(selectorIndex: number): Promise<void> {
		// The button does not have a proper translation
		//const removeLabel = this.getTranslation('Remove Model');
		const removeButton = this.page
			.getByRole('button', { name: 'Remove Model' })
			.nth(selectorIndex - 1);
		await removeButton.click();
	}

	/**
	 * Returns the model names of the most recent assistant responses
	 */
	async getLastAssistantModelNames(count: number = 1): Promise<string[]> {
		const assistantMessages = this.page
			.locator('div[id^="message-"]')
			.filter({ has: this.page.locator('.chat-assistant') });
		const total = await assistantMessages.count();
		const start = Math.max(0, total - count);
		const names: string[] = [];
		for (let i = start; i < total; i += 1) {
			const container = assistantMessages.nth(i);
			const nameSpan = container.locator('span.line-clamp-1').first();
			await nameSpan.waitFor({ state: 'visible' });
			names.push((await nameSpan.innerText()).trim());
		}
		return names;
	}

	/**
	 * Retrieves the chat status description displayed above the answer
	 * Example is wiki grounding "Enhanced with 5 ressources"
	 */
	async getChatStatusDescription(status: string) {
		await expect(this.chatStatusDescription).toContainText(status, { timeout: 60000 });
	}

	/**
	 * Retrieves the full text content of the most recent chat assistant response bubble
	 */
	async getLastMessageText(): Promise<string> {
		const lastContainer = this.page.locator('#response-content-container').last();
		await expect(lastContainer).toBeVisible();
		await this.waitForGeneration();
		return await lastContainer.innerText();
	}

	/**
	 * Triggers regeneration of the last AI response
	 */
	async regenerateLastResponse() {
		await this.regenerateButton.click();
		await this.waitForGeneration();
	}

	/**
	 * Checks if the "Network Problem" error is visible
	 */
	async isNetworkErrorPresent(): Promise<boolean> {
		return await this.responseMessages
			.getByText(this.getTranslation('Network Problem'))
			.isVisible();
	}

	/**
	 * Opens the 'More' menu and toggles a specific tool/capability
	 * @param toolName The name of the button in the menu
	 * @param enable Desired state (true/false)
	 */
	async toggleChatTool(toolName: string, enable: boolean) {
		await this.page.getByLabel('More').click();
		const toolButton = this.page.getByRole('menuitem', { name: toolName });
		const toolSwitch = toolButton.getByRole('switch');

		await expect(toolButton).toBeVisible();

		const isChecked = await toolSwitch.isChecked();
		if (isChecked !== enable) {
			await toolSwitch.click();
			await expect(toolSwitch).toBeChecked({ checked: enable });
		}

		await this.page.keyboard.press('Escape');
	}

	/**
	 * Uploads a file via the 'More' menu
	 * @param filePath Path to the file
	 */
	async uploadFile(filePath: string) {
		const fileChooserPromise = this.page.waitForEvent('filechooser');

		await this.page.getByLabel('More').click();
		await this.page.getByText(this.getTranslation('Upload Files')).click();

		const fileChooser = await fileChooserPromise;
		await fileChooser.setFiles(filePath);
	}

	/**
	 * Gets toast notification text (even if hidden or already dismissed)
	 * @param expectedText The translation key or text to search for in the toast
	 * @param timeout Maximum time to wait for toast (default: 30s)
	 * @returns The full text content of the toast
	 */
	async getToastText(expectedText: string, timeout: number = 30000): Promise<string> {
		const translatedText = this.getTranslation(expectedText);
		const toast = this.page.locator('li').filter({ hasText: translatedText }).first();
		await toast.waitFor({ state: 'attached', timeout });
		return (await toast.textContent()) || '';
	}

	/**
	 * Checks if a toast notification appeared (even if already dismissed)
	 * @param expectedText The translation key or text to search for in the toast
	 * @param timeout Maximum time to wait for toast (default: 30s)
	 * @returns true if toast appeared, false otherwise
	 */
	async checkToastAppeared(expectedText: string, timeout: number = 30000): Promise<boolean> {
		try {
			const translatedText = this.getTranslation(expectedText);
			const toast = this.page.locator('li').filter({ hasText: translatedText }).first();
			await toast.waitFor({ state: 'attached', timeout });
			return true;
		} catch {
			return false;
		}
	}

	/**
	 * Returns the locator for the last generated image in the chat
	 */
	getLastImage() {
		return this.page.locator('[data-cy="image"]').last();
	}

	/**
	 * Copies the last user message and verifies toast
	 */
	async copyLastUserMessage() {
		const lastUserMessage = this.page.locator('.user-message').last();
		await lastUserMessage.scrollIntoViewIfNeeded();
		await lastUserMessage.hover();
		await lastUserMessage.locator('.chat-user').hover();
		const copyLabel = this.getTranslation('Copy');
		const copyBtn = lastUserMessage.locator(`button[aria-label="${copyLabel}"]`).first();
		await copyBtn.waitFor({ state: 'attached' });
		await copyBtn.click({ force: true });
	}

	/**
	 * Pastes content from the clipboard into the message input field.
	 */
	async pasteToMessageInput() {
		await this.messageInput.focus();
		const modifier = process.platform === 'darwin' ? 'Meta' : 'Control';
		await this.page.keyboard.press(`${modifier}+V`);
		await this.page.waitForTimeout(200);
	}

	/**
	 * Returns the current value of the message input field.
	 */
	async getMessageInputValue(): Promise<string> {
		return (await this.messageInput.innerText()).trim();
	}

	/**
	 * Waits for autocomplete suggestion to appear
	 */
	async waitForAutocomplete(): Promise<void> {
		await this.page.locator('.autocomplete-suggestion, [data-testid="autocomplete"]').waitFor({
			state: 'visible',
			timeout: 10000
		});
	}

	/**
	 * Accepts autocomplete by pressing Tab
	 */
	async acceptAutocomplete(): Promise<void> {
		await this.page.keyboard.press('Tab');
		await this.waitToSettle(500);
	}

	/**
	 * Opens edit mode for the last user message
	 */
	async editLastUserMessage(): Promise<void> {
		const lastUserMessage = this.page.locator('.user-message').last();
		await lastUserMessage.scrollIntoViewIfNeeded();
		await lastUserMessage.hover();

		const editLabel = this.getTranslation('Edit');
		const editBtn = lastUserMessage.locator(`button[aria-label="${editLabel}"]`).first();
		await editBtn.waitFor({ state: 'attached' });
		await editBtn.click({ force: true });
		await this.page
			.locator('textarea, [contenteditable="true"]')
			.last()
			.waitFor({ state: 'visible' });
	}

	/**
	 * Types new content in the edit textarea
	 */
	async typeEditContent(text: string): Promise<void> {
		const editTextarea = this.page
			.locator('.user-message textarea, .user-message [contenteditable="true"]')
			.last();
		await editTextarea.fill(text);
	}

	/**
	 * Saves the edited message without regenerating
	 */
	async saveMessageEdit(): Promise<void> {
		const saveLabel = this.getTranslation('Save');
		await this.page.getByRole('button', { name: saveLabel, exact: true }).click();
		await this.waitToSettle(500);
	}

	/**
	 * Cancels message editing
	 */
	async cancelMessageEdit(): Promise<void> {
		const cancelLabel = this.getTranslation('Cancel');
		await this.page.getByRole('button', { name: cancelLabel }).click();
		await this.waitToSettle(300);
	}

	/**
	 * Sends edited message (triggers regeneration)
	 */
	async sendEditedMessage(): Promise<void> {
		const sendLabel = this.getTranslation('Send');
		await this.page.getByRole('button', { name: sendLabel, exact: true }).click();
		await this.waitForGeneration();
	}

	/**
	 * Gets the text of the last user message
	 */
	async getLastUserMessageText(): Promise<string> {
		const lastUserMessage = this.page.locator('.user-message .chat-user').last();
		return (await lastUserMessage.innerText()).trim();
	}

	/**
	 * Copies the last AI answer and verifies toast
	 */
	async copyLastAnswer(): Promise<void> {
		const lastResponse = this.page.locator('#response-content-container').last();
		await lastResponse.scrollIntoViewIfNeeded();
		await lastResponse.hover();
		const copyBtn = this.page.locator('.copy-response-button').last();
		await copyBtn.waitFor({ state: 'visible', timeout: 10000 });
		await copyBtn.click({ force: true });
	}

	// ===================================================
	// Chat (Answer) Helpers
	// ===================================================

	/**
	 * Prepares to interact with answer buttons by scrolling and hovering
	 */
	private async prepareAnswerButtonInteraction(): Promise<void> {
		const lastResponse = this.page.locator('#response-content-container').last();
		await lastResponse.scrollIntoViewIfNeeded();
		await lastResponse.hover();
	}

	/**
	 * Clicks an answer action button by aria-label
	 */
	private async clickAnswerButton(ariaLabel: string): Promise<void> {
		await this.prepareAnswerButtonInteraction();
		const btn = this.page.locator(`button[aria-label="${ariaLabel}"]`).last();
		await btn.waitFor({ state: 'visible', timeout: 10000 });
		await btn.click({ force: true });
	}

	/**
	 * Opens edit mode for the last AI answer
	 */
	async editLastAnswer(): Promise<void> {
		const editLabel = this.getTranslation('Edit');
		await this.clickAnswerButton(editLabel);
		await this.page
			.locator('textarea, [contenteditable="true"]')
			.last()
			.waitFor({ state: 'visible' });
	}

	/**
	 * Types new content in the answer edit textarea
	 */
	async typeAnswerEditContent(text: string): Promise<void> {
		const editTextarea = this.page.locator('textarea[id^="message-edit-"]').last();
		await editTextarea.fill(text);
	}

	/**
	 * Saves the edited answer as a new copy
	 */
	async saveAnswerAsCopy(): Promise<void> {
		const label = this.getTranslation('Save As Copy');
		await this.page.getByRole('button', { name: label }).click();
		await this.waitToSettle(500);
	}

	/**
	 * Saves the edited answer (replaces current)
	 */
	async saveAnswerEdit(): Promise<void> {
		const saveLabel = this.getTranslation('Save');
		await this.page.getByRole('button', { name: saveLabel, exact: true }).click();
		await this.waitToSettle(500);
	}

	/**
	 * Cancels answer editing
	 */
	async cancelAnswerEdit(): Promise<void> {
		const cancelLabel = this.getTranslation('Cancel');
		await this.page.getByRole('button', { name: cancelLabel }).click();
		await this.waitToSettle(300);
	}

	/**
	 * Navigates to previous answer version
	 */
	async goToPreviousAnswer(): Promise<void> {
		const label = this.getTranslation('Previous Response');
		const btn = this.page.locator(`button[aria-label="${label}"]`).last();
		if (await btn.isVisible()) {
			await btn.click();
		} else {
			await this.page.locator('div.flex.self-center.min-w-fit button').first().click();
		}
		await this.waitToSettle(500);
	}

	/**
	 * Navigates to next answer version
	 */
	async goToNextAnswer(): Promise<void> {
		const label = this.getTranslation('Next Response');
		const btn = this.page.locator(`button[aria-label="${label}"]`).last();
		if (await btn.isVisible()) {
			await btn.click();
		} else {
			await this.page.locator('div.flex.self-center.min-w-fit button').last().click();
		}
		await this.waitToSettle(500);
	}

	/**
	 * Toggles read aloud for the last answer
	 * @returns true if reading started, false if stopped
	 */
	async toggleReadAloud(): Promise<boolean> {
		const readLabel = this.getTranslation('Read Aloud');
		const stopLabel = this.getTranslation('Stop');

		await this.prepareAnswerButtonInteraction();

		// Try to click either read or stop button
		const readBtn = this.page.locator(`button[aria-label="${readLabel}"]`).last();
		const stopBtn = this.page.locator(`button[aria-label="${stopLabel}"]`).last();

		if (await stopBtn.isVisible()) {
			await stopBtn.click({ force: true });
			return false;
		} else {
			await readBtn.waitFor({ state: 'visible', timeout: 10000 });
			await readBtn.click({ force: true });
			await this.page.waitForTimeout(1000);
			return await this.isSpeaking();
		}
	}

	/**
	 * Checks if speech synthesis is active
	 */
	async isSpeaking(): Promise<boolean> {
		return await this.page.evaluate(() => window.speechSynthesis?.speaking ?? false);
	}

	/**
	 * Generates an image from the last answer context
	 */
	async generateImageFromAnswer(): Promise<void> {
		const label = this.getTranslation('Generate Image');
		await this.clickAnswerButton(label);
	}

	/**
	 * Hovers over info button to reveal token usage tooltip
	 */
	async hoverAnswerInfo(): Promise<void> {
		// Info button has id="info-{message.id}" but no aria-label
		await this.prepareAnswerButtonInteraction();
		const infoBtn = this.page.locator('button[id^="info-"]').last();
		await infoBtn.waitFor({ state: 'visible', timeout: 10000 });
		await infoBtn.hover();
		await this.page.waitForTimeout(500);
	}

	/**
	 * Returns the token info tooltip content
	 */
	async getTokenInfoText(): Promise<string> {
		const tooltip = this.page
			.locator('[role="tooltip"], .tooltip, .popover')
			.filter({ hasText: new RegExp(this.getTranslation('token'), 'i') });
		await tooltip.waitFor({ state: 'visible', timeout: 5000 });
		return (await tooltip.innerText()).trim();
	}

	/**
	 * Stops generation by clicking the Stop button
	 */
	async stopGeneration(): Promise<void> {
		await this.stopGenerationButton.click();
		await this.waitToSettle(500);
	}

	/**
	 * Clicks the continue response button
	 */
	async continueResponse(): Promise<void> {
		const label = this.getTranslation('Continue Response');
		await this.clickAnswerButton(label);
		await this.page.waitForTimeout(3000);
		await this.waitForGeneration();
	}

	/**
	 * Clicks regenerate button for the last answer
	 */
	async regenerateAnswer(): Promise<void> {
		const label = this.getTranslation('Regenerate');
		await this.clickAnswerButton(label);
		await this.waitForGeneration();
	}

	// ===================================================
	// Feedback Modal Helpers
	// ===================================================

	/**
	 * Clicks thumbs up (good response) button
	 */
	async clickGoodResponse(): Promise<void> {
		const label = this.getTranslation('Good Response');
		await this.clickAnswerButton(label);
		await this.waitToSettle(500);
	}

	/**
	 * Clicks thumbs down (bad response) button
	 */
	async clickBadResponse(): Promise<void> {
		const label = this.getTranslation('Bad Response');
		await this.clickAnswerButton(label);
		await this.waitToSettle(500);
	}

	/**
	 * Sets the rating by clicking the numbered button (1-10)
	 */
	async setRating(value: number): Promise<void> {
		const feedbackContainer = this.page.locator('[id^="message-feedback-"]').last();
		await feedbackContainer.waitFor({ state: 'visible' });
		const ratingBtn = feedbackContainer.locator(`button:has-text("${value}")`).first();
		await ratingBtn.click();
	}

	/**
	 * Selects a "Why" reason by clicking the corresponding button
	 */
	async selectFeedbackReason(reasonKey: string): Promise<void> {
		const feedbackContainer = this.page.locator('[id^="message-feedback-"]').last();
		await feedbackContainer.waitFor({ state: 'visible' });

		// Map reason keys to translated text
		const reasonTextMap: Record<string, string> = {
			accurate_information: this.getTranslation('Accurate information'),
			followed_instructions_perfectly: this.getTranslation('Followed instructions perfectly'),
			showcased_creativity: this.getTranslation('Showcased creativity'),
			positive_attitude: this.getTranslation('Positive attitude'),
			attention_to_detail: this.getTranslation('Attention to detail'),
			thorough_explanation: this.getTranslation('Thorough explanation'),
			dont_like_the_style: this.getTranslation("Don't like the style"),
			too_verbose: this.getTranslation('Too verbose'),
			not_helpful: this.getTranslation('Not helpful'),
			not_factually_correct: this.getTranslation('Not factually correct'),
			didnt_fully_follow_instructions: this.getTranslation("Didn't fully follow instructions"),
			refused_when_it_shouldnt_have: this.getTranslation("Refused when it shouldn't have"),
			being_lazy: this.getTranslation('Being lazy'),
			other: this.getTranslation('Other')
		};

		const reasonText = this.getTranslation(reasonTextMap[reasonKey] || reasonKey);
		const reasonBtn = feedbackContainer.getByRole('button', { name: reasonText });
		await reasonBtn.click();
	}

	/**
	 * Enters feedback comment
	 */
	async enterFeedbackComment(comment: string): Promise<void> {
		const feedbackContainer = this.page.locator('[id^="message-feedback-"]').last();
		const textarea = feedbackContainer.locator('textarea').first();
		await textarea.fill(comment);
	}

	/**
	 * Submits the feedback form
	 */
	async submitFeedback(): Promise<void> {
		const saveLabel = this.getTranslation('Save');
		await this.page.getByRole('button', { name: saveLabel }).click();
		await this.waitToSettle(500);
	}

	// ===================================================
	// Report an Issue Modal Helpers
	// ===================================================

	/**
	 * Opens the Report Issue modal
	 */
	async openReportIssue(): Promise<void> {
		const label = this.getTranslation('Report an Issue');
		await this.clickAnswerButton(label);
		await this.waitToSettle(500);
	}

	/**
	 * Fills the issue description
	 */
	async fillIssueDescription(text: string): Promise<void> {
		const textarea = this.page.locator('#description');
		await textarea.fill(text);
	}

	/**
	 * Fills the steps to reproduce
	 */
	async fillStepsToReproduce(text: string): Promise<void> {
		const textarea = this.page.locator('#stepsToReproduce');
		await textarea.fill(text);
	}

	/**
	 * Attaches an image to the report (inside the Issue modal)
	 */
	async attachImageToReport(filePath: string): Promise<void> {
		const fileInput = this.page.locator('#files');
		await fileInput.setInputFiles(filePath);
		await this.waitToSettle(500);
	}

	/**
	 * Submits the issue report
	 */
	async submitIssueReport(): Promise<void> {
		const submitLabel = this.getTranslation('Submit');
		await this.page.getByRole('button', { name: submitLabel }).click();
		await this.waitToSettle(500);
	}

	/**
	 * Closes the issue modal via X button
	 */
	async closeIssueModal(): Promise<void> {
		const closeLabel = this.getTranslation('Close');
		await this.page.getByRole('button', { name: closeLabel }).click({ force: true });
		await this.waitToSettle(500);
	}

	/**
	 * Cancels the issue report
	 */
	async cancelIssueReport(): Promise<void> {
		const cancelLabel = this.getTranslation('Cancel');
		await this.page.getByRole('button', { name: cancelLabel }).click();
		await this.waitToSettle(300);
	}

	// ===================================================
	// Suggestion Modal Helpers
	// ===================================================

	/**
	 * Opens the Suggestion Box modal
	 */
	async openSuggestionBox(): Promise<void> {
		const label = this.getTranslation('Suggestion Box');
		await this.clickAnswerButton(label);
		await this.waitToSettle(500);
	}

	/**
	 * Fills the suggestion textarea
	 */
	async fillSuggestion(text: string): Promise<void> {
		const textarea = this.page.locator('#description');
		await textarea.fill(text);
	}

	/**
	 * Attaches an image to the suggestion
	 */
	async attachImageToSuggestion(filePath: string): Promise<void> {
		const fileInput = this.page.locator('#files');
		await fileInput.setInputFiles(filePath);
		await this.waitToSettle(500);
	}

	/**
	 * Submits the suggestion
	 */
	async submitSuggestion(): Promise<void> {
		const submitLabel = this.getTranslation('Submit');
		await this.page.getByRole('button', { name: submitLabel }).click();
		await this.waitToSettle(500);
	}

	/**
	 * Closes the suggestion modal via X button
	 */
	async closeSuggestionModal(): Promise<void> {
		const closeLabel = this.getTranslation('Close');
		await this.page.getByRole('button', { name: closeLabel }).click({ force: true });
		await this.waitToSettle(500);
	}

	/**
	 * Cancels the suggestion
	 */
	async cancelSuggestion(): Promise<void> {
		const cancelLabel = this.getTranslation('Cancel');
		await this.page.getByRole('button', { name: cancelLabel }).click();
		await this.waitToSettle(300);
	}

	// ===================================================
	// Sidebar Helpers
	// ===================================================

	/**
	 * Clicks on a chat history item by title or content
	 */
	async selectChatHistoryItem(title: string) {
		const item = this.chatHistoryItems.filter({ hasText: title }).first();
		await item.click();
		await this.waitToSettle(500);
	}

	/**
	 * Opens the context menu for a chat item
	 */
	async openChatItemMenu(title: string) {
		const item = this.page.locator('div.relative.group').filter({ hasText: title }).first();
		await item.hover();
		const menuButton = item.locator('button[aria-label="Chat Menu"]');
		await menuButton.click();
	}

	/**
	 * Selects a chat for bulk action via checkbox
	 */
	async toggleChatCheckbox(title: string) {
		const item = this.page.locator('div.relative.group').filter({ hasText: title }).first();
		await item.hover();
		const checkbox = item.locator('.checkbox-area');
		await checkbox.click();
	}

	/**
	 * Searches in the chat history
	 */
	async searchHistory(term: string) {
		await this.searchInput.fill(term);
		await this.waitToSettle(500);
	}

	/**
	 * Selects a tag from the search results dropdown
	 */
	async selectSearchTag(index: number = 0) {
		const tag = this.searchTagItems.nth(index);
		await tag.waitFor({ state: 'visible' });
		await tag.click();
	}

	/**
	 * Selects an option from the search results dropdown
	 */
	async selectSearchOption(index: number = 0) {
		const option = this.searchOptionItems.nth(index);
		await option.waitFor({ state: 'visible' });
		await option.click();
	}

	/**
	 * Creates a new folder in the sidebar
	 */
	async createNewFolder() {
		const chatsFolder = this.page
			.locator('div:has-text("Chats")')
			.filter({ has: this.newFolderButton })
			.first();
		await chatsFolder.hover();
		await this.newFolderButton.click();
		await this.waitToSettle(500);
	}

	/**
	 * Moves a chat into a folder (using simple drag and drop)
	 */
	async moveChatToFolder(chatTitle: string, folderName: string) {
		const source = this.chatHistoryItems.filter({ hasText: chatTitle }).first();
		const target = this.page.locator('div').filter({ hasText: folderName }).first();
		await source.dragTo(target);
		await this.waitToSettle(500);
	}

	// ===================================================
	// Web Search Helpers
	// ===================================================

	/**
	 * Enables the Web Search tool via the More menu
	 */
	async enableWebSearch() {
		await this.toggleChatTool(this.getTranslation('Web Search'), true);
	}

	/**
	 * Returns the web search status container rendered above the response message.
	 */
	getWebSearchQuery(): Locator {
		return this.page.locator('div.status-description');
	}

	/**
	 * Expands the web search accordion (Collapsible component inside WebSearchResults.svelte)
	 * to reveal the list of searched URLs.
	 */
	async expandWebSearchAccordion() {
		const trigger = this.page.locator('div.status-description .cursor-pointer').last();
		await trigger.waitFor({ state: 'visible' });
		await trigger.click();
		// Wait for the slide-in transition to finish
		await this.waitToSettle(500);
	}

	/**
	 * Returns all anchor links rendered inside the expanded web search accordion.
	 */
	getWebSearchLinks(): Locator {
		return this.page.locator('div.status-description a[href][target="_blank"]');
	}

	/**
	 * Returns locators for inline citations embedded in the response text.
	 */
	getInlineCitations(): Locator {
		const lastResponse = this.page.locator('#response-content-container').last();
		return lastResponse.locator('button[class*="translate-y-[2px]"], a[href][target="_blank"]');
	}

	/**
	 * Returns locators for source citation chips rendered below a response by Citations.svelte.
	 */
	getBottomCitations(): Locator {
		return this.page.locator('button[id^="source-"]');
	}

	/**
	 * Clicks a source citation chip and returns the CitationsModal locator (div.modal).
	 * @param index The index of the citation chip to click (0-indexed)
	 */
	async clickBottomCitation(index: number = 0): Promise<Locator> {
		const citations = this.page.locator('button[id^="source-"]');
		await citations.nth(index).click();
		await this.waitToSettle(500);
		return this.page.locator('div.modal').last();
	}

	// ===================================================
	// File Item Modal Helpers
	// ===================================================

	/**
	 * Clicks an uploaded file to open the FileItemModal.
	 * @param index The index of the file chip to click (0-indexed)
	 */
	async clickUploadedFileChip(index: number = 0): Promise<void> {
		const removeLabel = this.getTranslation('Remove File');
		const fileChip = this.page
			.locator(`button:has(button[aria-label="${removeLabel}"])`)
			.nth(index);
		await fileChip.waitFor({ state: 'visible' });
		await fileChip.click({ position: { x: 40, y: 25 } });
		await this.waitToSettle(500);
	}

	/**
	 * In the open FileItemModal, enables the "Using Entire Document" toggle so the full
	 * file content is injected as context instead of using segmented RAG retrieval.
	 */
	async enableFileFullContent(): Promise<void> {
		const usingEntireDoc = this.getTranslation('Using Entire Document');
		const switchEl = this.page.locator('[role="switch"]');
		await switchEl.waitFor({ state: 'visible' });
		const isChecked = (await switchEl.getAttribute('data-state')) === 'checked';
		if (!isChecked) {
			await switchEl.click();
		}
		await expect(this.page.getByText(usingEntireDoc)).toBeVisible();
		await this.waitToSettle(300);
	}

	/**
	 * Closes the FileItemModal by clicking the X button in the modal header.
	 */
	async closeFileItemModal(): Promise<void> {
		const closeBtn = this.page.locator('div.flex.items-start.justify-between button').first();
		await closeBtn.waitFor({ state: 'visible' });
		await closeBtn.click();
		await this.waitToSettle(300);
	}

	// ===================================================
	// Document Upload Helpers
	// ===================================================

	/**
	 * Returns a locator for the upload spinner (spinning circle on file items)
	 */
	get uploadSpinner(): Locator {
		return this.page.locator('.spinner_ajPY');
	}

	/**
	 * Returns a locator for image thumbnails attached to the current prompt.
	 */
	get imageThumbnails(): Locator {
		return this.page.locator('img[alt="input"]');
	}

	/**
	 * Checks if the upload progress spinner is currently visible on any file item
	 * @returns true if a spinning circle is visible, false otherwise
	 */
	async isUploadSpinnerVisible(): Promise<boolean> {
		try {
			await expect(async () => {
				let anyVisible = false;
				for (const spinner of await this.uploadSpinner.all()) {
					if (await spinner.isVisible()) {
						anyVisible = true;
						break;
					}
				}
				expect(anyVisible).toBe(true);
			}).toPass({ timeout: 10000 });
			return true;
		} catch {
			return false;
		}
	}

	/**
	 * Waits for all upload spinners to disappear
	 * @param timeout Maximum time to wait (default: 60s)
	 */
	async waitForUploadSpinnerToDisappear(timeout: number = 60000): Promise<void> {
		await expect(async () => {
			for (const spinner of await this.uploadSpinner.all()) {
				await expect(spinner).toBeHidden();
			}
		}).toPass({ timeout });
	}

	/**
	 * Waits for at least one image thumbnail to appear in the prompt area.
	 * @param timeout Maximum time to wait (default: 10s)
	 */
	async waitForImageThumbnailToAppear(timeout: number = 10000): Promise<void> {
		await this.imageThumbnails.first().waitFor({ state: 'visible', timeout });
	}

	/**
	 * Unified wait for upload completion that handles both images and documents.
	 * - For images, wait for a thumbnail to appear
	 * - For documents, wait for the upload spinner to disappear
	 * @param type 'image' to wait for thumbnail, 'document' to wait for spinner, 'auto' to detect
	 * @param timeout Maximum time to wait (default: 60s)
	 */
	async waitForUploadComplete(
		type: 'image' | 'document' | 'auto' = 'auto',
		timeout: number = 60000
	): Promise<void> {
		if (type === 'image') {
			await this.waitForImageThumbnailToAppear(timeout);
		} else if (type === 'document') {
			await this.waitForUploadSpinnerToDisappear(timeout);
		} else {
			// Auto-detect wait for either a new image or spinners to clear
			const hasSpinners = (await this.uploadSpinner.count()) > 0;
			if (hasSpinners) {
				await this.waitForUploadSpinnerToDisappear(timeout);
			} else {
				// No spinners means either an image was added or upload hasn't started yet
				await this.waitForImageThumbnailToAppear(Math.min(timeout, 10000));
			}
		}
	}

	/**
	 * Returns the number of files attached to the current prompt.
	 * Counts both document dismiss buttons and image thumbnails
	 */
	async getUploadedFileCount(): Promise<number> {
		const removeLabel = this.getTranslation('Remove File');
		const documentCount = await this.page.locator(`button[aria-label="${removeLabel}"]`).count();
		const imageCount = await this.imageThumbnails.count();
		return documentCount + imageCount;
	}

	/**
	 * Removes a specific uploaded file from the prompt area.
	 * Handles both document items and image thumbnails
	 * @param index The index of the file to remove
	 */
	async removeUploadedFile(index: number = 0) {
		const removeLabel = this.getTranslation('Remove File');
		const documentDismissButtons = this.page.locator(`button[aria-label="${removeLabel}"]`);
		const documentCount = await documentDismissButtons.count();

		if (index < documentCount) {
			// Remove a document file item
			await documentDismissButtons.nth(index).click();
		} else {
			// Remove an image thumbnail (index adjusted for documents)
			const imageIndex = index - documentCount;
			const imageContainer = this.imageThumbnails
				.nth(imageIndex)
				.locator('xpath=ancestor::div[contains(@class,"relative")][contains(@class,"group")]');
			await imageContainer.hover();
			await imageContainer.locator('button').click();
		}
		await this.waitToSettle(500);
	}

	/**
	 * Uploads multiple files via the 'More' menu
	 * @param filePaths Array of file paths
	 */
	async uploadMultipleFiles(filePaths: string[]) {
		const fileChooserPromise = this.page.waitForEvent('filechooser');

		await this.page.getByLabel('More').click();
		await this.page.getByText(this.getTranslation('Upload Files')).click();

		const fileChooser = await fileChooserPromise;
		await fileChooser.setFiles(filePaths);
	}

	/**
	 * Simulates file upload via drag and drop
	 * @param filePath Path to the file to upload
	 */
	async uploadFileViaDragDrop(filePath: string) {
		const buffer = fs.readFileSync(filePath);
		const fileName = path.basename(filePath);
		const mimeType = fileName.endsWith('.txt') ? 'text/plain' : 'application/octet-stream';
		const base64Str = buffer.toString('base64');

		// Wait for the drop target to be visible before dispatching the event
		const dropTarget = this.page.locator('#chat-container');
		await dropTarget.waitFor({ state: 'visible', timeout: 5000 });

		await this.page.evaluate(
			async ({ base64, name, mime }) => {
				const dt = new DataTransfer();
				const res = await fetch(`data:${mime};base64,${base64}`);
				const blob = await res.blob();
				const file = new File([blob], name, { type: mime });
				dt.items.add(file);

				const container = document.getElementById('chat-container');
				if (!container) {
					throw new Error('Drop target #chat-container not found');
				}
				const dropEvent = new DragEvent('drop', {
					bubbles: true,
					cancelable: true,
					dataTransfer: dt
				});
				container.dispatchEvent(dropEvent);
			},
			{ base64: base64Str, name: fileName, mime: mimeType }
		);

		await this.waitToSettle(500);
	}
}
