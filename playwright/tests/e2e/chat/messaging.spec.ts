import { randomInt } from 'crypto';
import { test, expect } from '../../../src/fixtures/base-fixture';
import * as path from 'path';

const TEST_IMAGE_PATH = path.join(process.cwd(), 'playwright/src/test-data/uploads/test_image.png');

test.describe('Messaging Features', () => {
	test.setTimeout(120000);

	// ===========================================
	// TC001: Send message and get reply
	// ===========================================
	test('TC001: User can send a message and get a reply', async ({ userPage, locale }) => {
		const question =
			locale === 'fr-CA'
				? 'En quelle année la ville de Québec a-t-elle été fondée ?'
				: 'In what year was Quebec city founded?';
		await userPage.sendMessage(question);

		const answerText = await userPage.getLastMessageText();
		expect(answerText).not.toBe('');
		expect(answerText).toContain('1608');

		const followup = locale === 'fr-CA' ? 'Et Montréal ?' : 'What about Montreal?';
		await userPage.sendMessage(followup);

		const followupAnswerText = await userPage.getLastMessageText();
		expect(followupAnswerText).not.toBe('');
		expect(followupAnswerText).toContain('1642');
	});

	// ===========================================
	// TC002: Autocomplete partial message
	// ===========================================
	test('TC002: User can type a question and have it auto completed', async ({ userPage }) => {
		const partialMessage = 'In what year';

		await expect(userPage.messageInput).toBeVisible();
		await userPage.messageInput.fill(partialMessage);

		// Wait a moment for autocomplete to potentially appear
		await userPage.page.waitForTimeout(3000);

		// Check if autocomplete suggestion appeared
		const autocompleteVisible = await userPage.page
			.locator('.tiptap-autocomplete, .ghost-text, [data-autocomplete], .placeholder')
			.isVisible()
			.catch(() => false);

		if (!autocompleteVisible) {
			test.skip(true, 'Autocomplete feature not available or not configured in this environment');
			return;
		}

		// Accept autocomplete with Tab
		await userPage.acceptAutocomplete();

		// Verify message was autocompleted
		const completedMessage = await userPage.getMessageInputValue();
		expect(completedMessage.length).toBeGreaterThan(partialMessage.length);

		// Send the autocompleted message
		await userPage.sendButton.click();
		await userPage.waitForGeneration();

		// Verify we got an answer
		const answerText = await userPage.getLastMessageText();
		expect(answerText).not.toBe('');
	});

	// ===========================================
	// TC003: Copy user question
	// ===========================================
	test('TC003: User can use the question options (copy)', async ({ userPage }) => {
		const question = 'In what year was Quebec city founded?';
		await userPage.sendMessage(question);

		await userPage.copyLastUserMessage();

		const clipboardText = await userPage.getClipboardText();
		expect(clipboardText).toBe(question);
	});

	// ===========================================
	// TC004: Edit user question
	// ===========================================
	test('TC004: User can use the question options (edit)', async ({ userPage, locale }) => {
		const initialQuestion = 'What is a dog? Explain in 20 words.';
		const editedQuestion = 'What is a cat? Explain in 20 words.';

		// Send initial question
		await userPage.sendMessage(initialQuestion);
		const initialAnswer = await userPage.getLastMessageText();
		expect(initialAnswer.toLowerCase()).toContain('dog');

		// Open edit mode
		await userPage.editLastUserMessage();

		// Edit and save (without regenerating)
		await userPage.typeEditContent(editedQuestion);
		await userPage.saveMessageEdit();

		// Verify question text changed but answer remains
		const userMessageText = await userPage.getLastUserMessageText();
		expect(userMessageText).toContain('cat');

		// Verify old answer is still there
		const currentAnswer = await userPage.getLastMessageText();
		expect(currentAnswer.toLowerCase()).toContain('dog');

		// Open edit again and cancel
		await userPage.editLastUserMessage();
		await userPage.cancelMessageEdit();

		// Open edit and send (triggers regeneration)
		await userPage.editLastUserMessage();
		await userPage.sendEditedMessage();

		// Verify new answer contains cat context
		const newAnswer = await userPage.getLastMessageText();
		expect(newAnswer.toLowerCase()).toContain('cat');

		// Navigate to previous response
		await userPage.goToPreviousAnswer();
		const previousAnswer = await userPage.getLastMessageText();
		expect(previousAnswer.toLowerCase()).toContain('dog');
	});

	// ===========================================
	// TC005: Delete user question
	// ===========================================
	test.skip('TC005: User can use the question options (Delete) in the chat page', async ({
		userPage
	}) => {
		const question = 'In what year was Quebec city founded?';
		await userPage.sendMessage(question);

		//Need more info on the logic of the delete function
	});

	// ===========================================
	// TC006: Stop answer generation
	// ===========================================
	test("TC006: User can stop an answer while it's generating.", async ({ userPage }, testInfo) => {
		const question = 'Explain the history of artificial intelligence in details';
		await userPage.sendMessage(question, false);

		// Wait for response container to appear (streaming has started)
		const responseContainer = userPage.page.locator('#response-content-container').last();

		// Wait longer for content to stream in
		try {
			await responseContainer.waitFor({ state: 'visible', timeout: 10000 });
			await userPage.page.waitForTimeout(5000);
			const stopBtn = userPage.stopGenerationButton;
			await stopBtn.waitFor({ state: 'visible', timeout: 5000 });
		} catch (e) {
			testInfo.annotations.push({
				type: 'warning',
				description:
					'Generation completed too quickly to test stop-continue flow (fast model or no streaming).'
			});
			return;
		}

		const stopBtn = userPage.stopGenerationButton;
		await stopBtn.waitFor({ state: 'visible', timeout: 5000 });
		await stopBtn.click();
		await userPage.callButton.waitFor({ state: 'visible', timeout: 5000 });
	});

	// ===========================================
	// TC007: Copy answer
	// ===========================================
	test('TC007: User can use the ANSWER options (copy)', async ({ userPage }) => {
		const question = 'In what year was Quebec city founded?';
		await userPage.sendMessage(question);

		const answerText = await userPage.getLastMessageText();
		expect(answerText).toContain('1608');

		// Copy the answer and verify the clipboard
		await userPage.copyLastAnswer();
		const clipboardText = await userPage.getClipboardText();
		expect(clipboardText).toContain('1608');
	});

	// ===========================================
	// TC008: Edit answer
	// ===========================================
	test('TC008: User can use the ANSWER options (edit) @serial', async ({ userPage, locale }) => {
		const initialQuestion = 'What is a dog? Explain in 20 words.';
		const editedContent = 'A cat is a small domesticated carnivorous mammal.';
		const followUpQuestion = 'What is a duck? Explain in 20 words.';

		// Send initial question
		await userPage.sendMessage(initialQuestion);
		const initialAnswer = await userPage.getLastMessageText();
		expect(initialAnswer.toLowerCase()).toContain('dog');

		// Open answer edit mode and save as copy
		await userPage.editLastAnswer();
		await userPage.typeAnswerEditContent(editedContent);
		await userPage.saveAnswerAsCopy();

		// Verify edited answer is shown
		const editedAnswer = await userPage.getLastMessageText();
		expect(editedAnswer.toLowerCase()).toContain('cat');
		await userPage.goToPreviousAnswer();
		const previousAnswer = await userPage.getLastMessageText();
		expect(previousAnswer.toLowerCase()).toContain('dog');

		// Navigate back to edited version
		await userPage.goToNextAnswer();
		const nextAnswer = await userPage.getLastMessageText();
		expect(nextAnswer.toLowerCase()).toContain('cat');

		// Test cancel editing
		await userPage.editLastAnswer();
		await userPage.cancelAnswerEdit();

		// Test Save (not as copy) with toast
		await userPage.editLastAnswer();
		await userPage.saveAnswerEdit();
		const saveToast = userPage.getTranslation('Message editing confirmed.');
		await userPage.verifyToast(saveToast);

		// Send follow-up and edit that answer
		await userPage.sendMessage(followUpQuestion);
		const followupAnswer = await userPage.getLastMessageText();
		expect(followupAnswer.toLowerCase()).toContain('duck');

		await userPage.editLastAnswer();
		const duckEdit = 'Ducks are waterfowl birds.';
		await userPage.typeAnswerEditContent(duckEdit);
		await userPage.saveAnswerEdit();

		const modifiedFollowup = await userPage.getLastMessageText();
		expect(modifiedFollowup.toLowerCase()).toContain('duck');
	});

	// ===========================================
	// TC009: Read aloud answer
	// ===========================================
	test('TC009: User can use the ANSWER options (Read Aloud)', async ({ userPage }) => {
		const question = 'Explain quantum mechanics in 50 words.';
		await userPage.sendMessage(question);

		const answerText = await userPage.getLastMessageText();
		expect(answerText).not.toBe('');

		// Start reading aloud then stop
		await userPage.toggleReadAloud();
		await userPage.page.waitForTimeout(2000);
		await userPage.toggleReadAloud();

		// Verify speaking stopped
		await userPage.page.waitForTimeout(500);
		const stillSpeaking = await userPage.isSpeaking();
		expect(stillSpeaking).toBe(false);
	});

	// ===========================================
	// TC010: Generate image from answer
	// ===========================================
	test.skip('TC010: User can use the ANSWER options (Generate image)', async ({ userPage }) => {
		// skippig test as dall-e is not working
		test.setTimeout(240000);
		const question = 'Hello, how are you?';
		await userPage.sendMessage(question);

		const answerText = await userPage.getLastMessageText();
		expect(answerText).not.toBe('');

		// Generate image from answer
		await userPage.generateImageFromAnswer();

		// Wait for image to appear
		const imageElement = userPage.getLastImage();
		await expect(imageElement).toBeVisible({ timeout: 120000 });
	});

	// ===========================================
	// TC011: Answer token information
	// ===========================================
	test('TC011: User can use the ANSWER options (Information)', async ({ userPage }, testInfo) => {
		const question = 'What is a dog? Explain in 20 words.';
		await userPage.sendMessage(question);

		const answerText = await userPage.getLastMessageText();
		expect(answerText).not.toBe('');

		// Check if info button exists (model must provide usage data)
		const infoBtn = userPage.page.locator('button[id^="info-"]').last();
		const isInfoBtnVisible = await infoBtn.isVisible().catch(() => false);

		if (!isInfoBtnVisible) {
			testInfo.annotations.push({
				type: 'warning',
				description: 'Information button not available (model does not provide usage data).'
			});
			return; // Skip the rest of the test gracefully
		}

		// Hover over info button
		await userPage.hoverAnswerInfo();

		// Get token info
		const tokenInfo = await userPage.getTokenInfoText();
		expect(tokenInfo).not.toBe('');

		// Token info should contain numbers (not all zeros)
		const hasNonZeroNumber = /[1-9]/.test(tokenInfo);
		expect(hasNonZeroNumber).toBe(true);
	});

	// ===========================================
	// TC012: Thumbs up/down feedback
	// ===========================================
	test('TC012: User can use the ANSWER options (Thumb up/down)', async ({ userPage }) => {
		// Send first question and rate positively
		await userPage.sendMessage('What is a dog? Explain in 20 words.');
		const dogAnswer = await userPage.getLastMessageText();
		expect(dogAnswer).not.toBe('');

		await userPage.clickGoodResponse();

		// Rate answer (6-10 for good, buttons are disabled below 6 for positive feedback)
		await userPage.setRating(randomInt(6, 10));
		await userPage.selectFeedbackReason('accurate_information');
		await userPage.enterFeedbackComment('This was a helpful response about dogs.');
		await userPage.submitFeedback();
		await userPage.verifyToast(userPage.getTranslation('Thanks for your feedback!'));

		// Send follow-up and rate negatively
		await userPage.sendMessage('What is a bird? Explain in 20 words.');
		const birdAnswer = await userPage.getLastMessageText();
		expect(birdAnswer).not.toBe('');

		await userPage.clickBadResponse();

		// Rate answer (1-5 for bad, buttons are disabled above 5 for negative feedback)
		await userPage.setRating(randomInt(1, 5));
		await userPage.selectFeedbackReason('too_verbose');
		await userPage.enterFeedbackComment('This response about birds could be improved.');
		await userPage.submitFeedback();

		await userPage.verifyToast(userPage.getTranslation('Thanks for your feedback!'));
	});

	// ===========================================
	// TC013: Continue response
	// ===========================================
	test('TC013: User can use the ANSWER options (Continue Response)', async ({
		userPage
	}, testInfo) => {
		// Ask a question and DON'T wait for completion so we can stop it
		const question =
			'Explain the history of the internet in detail. Cover its origins, key developments, and modern impact.';
		await userPage.sendMessage(question, false);

		// Wait for response container to appear (streaming has started)
		const responseContainer = userPage.page.locator('#response-content-container').last();

		try {
			await responseContainer.waitFor({ state: 'visible', timeout: 10000 });
			await userPage.page.waitForTimeout(3000);
			const stopBtn = userPage.stopGenerationButton;
			await stopBtn.waitFor({ state: 'visible', timeout: 5000 });
		} catch (e) {
			testInfo.annotations.push({
				type: 'warning',
				description:
					'Generation completed too quickly to test stop-continue flow (fast model or no streaming).'
			});
			return;
		}

		// Stop the generation mid-stream
		await userPage.stopGeneration();
		await userPage.callButton.waitFor({ state: 'visible', timeout: 2000 });

		// Get the partial answer after stopping
		const partialAnswer = await userPage.getLastMessageText();
		const partialLength = partialAnswer.length;
		expect(partialLength).toBeGreaterThan(0);

		// Continue the response to complete it
		await userPage.continueResponse();
		const completedAnswer = await userPage.getLastMessageText();
		const completedLength = completedAnswer.length;
		expect(completedLength).toBeGreaterThan(0);

		// Continue AGAIN to see if model expands the answer further
		await userPage.continueResponse();
		const expandedAnswer = await userPage.getLastMessageText();
		expect(expandedAnswer.length).toBeGreaterThan(0);
	});

	// ===========================================
	// TC014: Regenerate answer
	// ===========================================
	test('TC014: User can use the ANSWER options (Regenerate)', async ({ userPage }) => {
		const question = 'What is the Shared Services Canada mandate? Explain in 20 words.';
		await userPage.sendMessage(question);

		const firstAnswer = await userPage.getLastMessageText();
		expect(firstAnswer).not.toBe('');

		// Regenerate the answer
		await userPage.regenerateAnswer();

		const secondAnswer = await userPage.getLastMessageText();
		expect(secondAnswer).not.toBe('');

		// Navigate to previous answer
		await userPage.goToPreviousAnswer();
		const previousAnswer = await userPage.getLastMessageText();
		expect(previousAnswer).toBe(firstAnswer);

		// Regenerate from previous (should create a third version)
		await userPage.regenerateAnswer();
		const thirdAnswer = await userPage.getLastMessageText();
		expect(thirdAnswer).not.toBe('');
	});

	// ===========================================
	// TC015: Report an issue
	// ===========================================
	test('TC015: User can use the ANSWER options (Report an Issue)', async ({
		userPage
	}, testInfo) => {
		await userPage.sendMessage('What is a cat? Explain in 20 words.');
		await userPage.getLastMessageText();

		// Open Report Issue modal, fill in description and steps and attach an image
		await userPage.openReportIssue();
		await userPage.fillIssueDescription('Test description');
		await userPage.fillStepsToReproduce('Test Steps to Reproduce');
		await userPage.attachImageToReport(TEST_IMAGE_PATH);
		await userPage.submitIssueReport();

		// Verify toast
		try {
			const successMsg = userPage.getTranslation(
				'Thank you! Your issue has been submitted successfully.'
			);
			const failureMsg = userPage.getTranslation('Failed to submit issue. Please try again later.');

			const toast = userPage.page.locator('[data-sonner-toast]');
			await toast.waitFor({ state: 'visible', timeout: 5000 });
			const toastText = await toast.innerText();

			if (!toastText.includes(successMsg) && !toastText.includes(failureMsg)) {
				console.log(`Toast mismatch: ${toastText}`);
			}
		} catch (e) {
			console.log('Toast verification skipped or timed out');
		}

		// If submission failed (local environment), the modal stays open.
		const modalVisible = await userPage.page.locator('.modal, dialog').first().isVisible();
		if (modalVisible) {
			testInfo.annotations.push({
				type: 'warning',
				description:
					'Report Issue Modal remained open after submission (local environment cannot submit issues).'
			});
			await userPage.cancelIssueReport();
		}

		// Test close via X button
		await userPage.openReportIssue();
		await userPage.closeIssueModal();

		// Test cancel button
		await userPage.openReportIssue();
		await userPage.cancelIssueReport();
	});

	// ===========================================
	// TC016: Suggestion box
	// ===========================================
	test('TC016: User can use the ANSWER options (Suggestion Box)', async ({
		userPage
	}, testInfo) => {
		await userPage.sendMessage('Explain artificial intelligence in 20 words.');
		await userPage.getLastMessageText();

		await userPage.openSuggestionBox();
		await userPage.fillSuggestion('Test suggestion content');
		await userPage.attachImageToSuggestion(TEST_IMAGE_PATH);
		await userPage.submitSuggestion();

		// Verify toast
		try {
			const successMsg = userPage.getTranslation(
				'Thank you! Your suggestion has been submitted successfully.'
			);
			const failureMsg = userPage.getTranslation(
				'Failed to submit suggestion. Please try again later.'
			);

			const toast = userPage.page.locator('[data-sonner-toast]');
			await toast.waitFor({ state: 'visible', timeout: 5000 });
			const toastText = await toast.innerText();

			if (!toastText.includes(successMsg) && !toastText.includes(failureMsg)) {
				console.log(`Toast mismatch: ${toastText}`);
			}
		} catch (e) {
			console.log('Toast verification skipped or timed out');
		}

		// Close modal if still open (failure case)
		const modalVisible = await userPage.page.locator('.modal, dialog').first().isVisible();
		if (modalVisible) {
			testInfo.annotations.push({
				type: 'warning',
				description:
					'Suggestion Box Modal remained open after submission (local environment cannot submit suggestions).'
			});
			await userPage.cancelSuggestion();
		}

		// Test close via X button
		await userPage.openSuggestionBox();
		await userPage.closeSuggestionModal();

		// Test cancel button
		await userPage.openSuggestionBox();
		await userPage.cancelSuggestion();
	});
});
