import { randomInt } from 'crypto';
import { test, expect } from '../../../src/fixtures/base-fixture';

const QUESTION_LIST = {
	en: 'List 5 facts about Canada.',
	fr: 'Liste 5 faits sur le Canada.'
};

const DOG_QUESTION = {
	en: 'What is a dog?',
	fr: "Qu'est-ce qu'un chien ?"
};

const DOG_FOLLOWUP = {
	en: 'List 10 breeds.',
	fr: 'Liste 10 races.'
};

const MULTI_MODEL_QUESTION = {
	en: 'List 20 breeds of dogs.',
	fr: 'Liste 20 races de chiens.'
};

const MULTI_MODEL_FOLLOWUP_1 = {
	en: 'What about cats?',
	fr: 'Et les chats ?'
};

const MULTI_MODEL_FOLLOWUP_2 = {
	en: 'What about birds?',
	fr: 'Et les oiseaux ?'
};

test.describe('Model Selection', () => {
	test.setTimeout(240000);

	test('CHAT-MODEL-TC001: User can modify model before starting a new conversation', async ({
		userPage,
		locale
	}) => {
		const availableModels = await userPage.getAvailableModelCount();
		if (availableModels === 0) {
			test.skip(true, 'No models available for selection.');
			return;
		}

		const targetIndex = availableModels > 1 ? 1 : 0;
		const selectedModel = await userPage.addModelByIndex(0, targetIndex);
		await userPage.sendMessage(locale === 'fr-CA' ? QUESTION_LIST.fr : QUESTION_LIST.en);

		const [responseModel] = await userPage.getLastAssistantModelNames(1);
		expect(responseModel).toBe(selectedModel);
	});

	test('CHAT-MODEL-TC002: User can add multiple models before starting a new conversation', async ({
		userPage,
		locale
	}) => {
		const availableModels = await userPage.getAvailableModelCount();
		if (availableModels < 2) {
			test.skip(true, 'Need at least 2 models to validate multi-model conversations.');
			return;
		}

		const modelCount = availableModels >= 3 ? randomInt(2, 4) : 2;
		const selectedModels: string[] = [];

		for (let selectorIndex = 0; selectorIndex < modelCount; selectorIndex += 1) {
			if (selectorIndex > 0) {
				await userPage.clickModelSelector();
			}
			const selectedModel = await userPage.addModelByIndex(selectorIndex, selectorIndex);
			selectedModels.push(selectedModel);
		}

		await userPage.sendMessage(locale === 'fr-CA' ? QUESTION_LIST.fr : QUESTION_LIST.en);

		const responseModels = await userPage.getLastAssistantModelNames(modelCount);
		for (const modelName of selectedModels) {
			expect(responseModels).toContain(modelName);
		}
	});

	test('CHAT-MODEL-TC003: User can set a model as default', async ({ userPage }) => {
		const availableModels = await userPage.getAvailableModelCount();
		if (availableModels === 0) {
			test.skip(true, 'No models available for selection.');
			return;
		}

		const targetIndex = availableModels > 1 ? 1 : 0;
		const selectedModel = await userPage.addModelByIndex(0, targetIndex);
		await userPage.setDefaultChatModel();

		await userPage.newChat();
		expect(await userPage.getSelectedModelLabel(0)).toBe(selectedModel);

		await userPage.sendMessage('Hello');
		await userPage.toggleSidebar(true);
		await userPage.page.locator('a[href^="/c/"]').first().click();

		await userPage.newChat();
		expect(await userPage.getSelectedModelLabel(0)).toBe(selectedModel);

		await userPage.newChatFromHeader();
		expect(await userPage.getSelectedModelLabel(0)).toBe(selectedModel);

		await userPage.toggleSidebar(true);
		await userPage.page.locator('a[href^="/c/"]').first().click();
		await userPage.newChatFromHeader();
		expect(await userPage.getSelectedModelLabel(0)).toBe(selectedModel);
	});

	test('CHAT-MODEL-TC004: User can search a model from the list', async ({ userPage }) => {
		const availableModels = await userPage.getAvailableModelCount();
		if (availableModels === 0) {
			test.skip(true, 'No models available for selection.');
			return;
		}
		const filteredCount = await userPage.getFilteredModelCount(0, 'gpt');
		if (filteredCount === 0) {
			test.skip(true, 'No models matched the search term "gpt".');
			return;
		}

		const selectedModel = await userPage.selectModelBySearch(0, 'gpt');
		expect(selectedModel).not.toBe('');
	});

	test('CHAT-MODEL-TC005: User can modify the model during a conversation', async ({
		userPage,
		locale
	}) => {
		const availableModels = await userPage.getAvailableModelCount();
		if (availableModels < 2) {
			test.skip(true, 'Need at least 2 models to validate model switching.');
			return;
		}

		const initialModel = await userPage.addModelByIndex(0, 0);
		await userPage.sendMessage(locale === 'fr-CA' ? DOG_QUESTION.fr : DOG_QUESTION.en);
		const [initialResponseModel] = await userPage.getLastAssistantModelNames(1);
		expect(initialResponseModel).toBe(initialModel);

		const nextModel = await userPage.addModelByIndex(0, 1);
		await userPage.sendMessage(locale === 'fr-CA' ? DOG_FOLLOWUP.fr : DOG_FOLLOWUP.en);
		const [followupResponseModel] = await userPage.getLastAssistantModelNames(1);
		expect(followupResponseModel).toBe(nextModel);
	});

	test('CHAT-MODEL-TC006: User can add/remove multiple models during a conversation', async ({
		userPage,
		locale
	}) => {
		const availableModels = await userPage.getAvailableModelCount();
		if (availableModels < 2) {
			test.skip(true, 'Need at least 2 models to validate add/remove during conversation.');
			return;
		}

		const assistantMessages = userPage.page
			.locator('div[id^="message-"]')
			.filter({ has: userPage.page.locator('.chat-assistant') });
		const getAssistantCount = async () => assistantMessages.count();

		await userPage.addModelByIndex(0, 0);
		const beforeFirst = await getAssistantCount();
		await userPage.sendMessage(
			locale === 'fr-CA' ? MULTI_MODEL_QUESTION.fr : MULTI_MODEL_QUESTION.en,
			false
		);
		await userPage.addModelByIndex(0, 1);
		await userPage.waitForGeneration();
		const afterFirst = await getAssistantCount();
		expect(afterFirst - beforeFirst).toBe(1);

		const beforeSecond = await getAssistantCount();
		await userPage.sendMessage(
			locale === 'fr-CA' ? MULTI_MODEL_FOLLOWUP_1.fr : MULTI_MODEL_FOLLOWUP_1.en,
			false
		);
		await userPage.clickModelSelector();
		await userPage.addModelByIndex(1, 2);
		await userPage.waitForGeneration();
		const afterSecond = await getAssistantCount();
		expect(afterSecond - beforeSecond).toBe(1);

		const beforeThird = await getAssistantCount();
		await userPage.sendMessage(
			locale === 'fr-CA' ? MULTI_MODEL_FOLLOWUP_2.fr : MULTI_MODEL_FOLLOWUP_2.en,
			false
		);
		await userPage.removeModelSelector(1);
		await userPage.waitForGeneration();
		const afterThird = await getAssistantCount();
		expect(afterThird - beforeThird).toBe(1);
	});

	test('CHAT-MODEL-TC007: User can use all available models', async ({ userPage }) => {
		test.setTimeout(360000);
		const availableModels = await userPage.getAvailableModelCount();
		if (availableModels === 0) {
			test.skip(true, 'No models available for selection.');
			return;
		}

		for (let modelIndex = 0; modelIndex < availableModels; modelIndex += 1) {
			await userPage.newChat();
			const selectedModel = await userPage.addModelByIndex(0, modelIndex);
			await userPage.sendMessage('When was Canada founded?');
			const [responseModel] = await userPage.getLastAssistantModelNames(1);
			expect(responseModel).toBe(selectedModel);
		}
	});
});
