import { beforeEach, describe, expect, expectTypeOf, it, vi } from 'vitest';

import { apiJson } from '$lib/apis/client';
import { chatCompleted } from './index';

vi.mock('$lib/constants', () => ({ WEBUI_BASE_URL: '' }));
vi.mock('$lib/apis/client', () => ({ apiJson: vi.fn() }));

describe('chatCompleted', () => {
	beforeEach(() => {
		vi.resetAllMocks();
	});

	it('requires the completed response ID in the request type', () => {
		expectTypeOf<Parameters<typeof chatCompleted>[1]>().toExtend<{
			id: string;
		}>();
	});

	it('sends the response ID at the top level alongside message objects', async () => {
		const body = {
			model: 'arena-model',
			messages: [
				{ id: 'user-message', role: 'user', content: 'Hello', timestamp: 1 },
				{ id: 'assistant-message', role: 'assistant', content: 'Hi', timestamp: 2 }
			],
			chat_id: 'chat-id',
			id: 'assistant-message',
			session_id: 'socket-id'
		} satisfies Parameters<typeof chatCompleted>[1];
		const response = { messages: body.messages };
		vi.mocked(apiJson).mockResolvedValue(response);

		await expect(chatCompleted('test-token', body)).resolves.toEqual(response);

		expect(apiJson).toHaveBeenCalledExactlyOnceWith('/api/chat/completed', {
			method: 'POST',
			token: 'test-token',
			headers: { Accept: 'application/json' },
			body: JSON.stringify(body)
		});
	});
});
