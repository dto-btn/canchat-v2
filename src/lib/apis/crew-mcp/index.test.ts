import { describe, it, expect, vi, beforeEach } from 'vitest';
import { writable } from 'svelte/store';

const { socketStore } = vi.hoisted(() => ({ socketStore: { value: null as unknown } }));

vi.mock('$lib/stores', () => {
	const store = writable<unknown>(null);
	socketStore.value = store;
	return { socket: store };
});

import { queryCrewMCPWebSocket } from './index';

type Handler = (data: unknown) => void;

class FakeSocket {
	id = 'socket-id-1234';
	connected = true;
	handlers = new Map<string, Set<Handler>>();
	emitted: Array<[string, { carrier: { operation_id: string; traceparent: string } }]> = [];

	on(event: string, handler: Handler) {
		if (!this.handlers.has(event)) this.handlers.set(event, new Set());
		this.handlers.get(event)!.add(handler);
	}

	off(event: string, handler?: Handler) {
		if (handler) this.handlers.get(event)?.delete(handler);
		else this.handlers.delete(event);
	}

	emit(event: string, data: { carrier: { operation_id: string; traceparent: string } }) {
		this.emitted.push([event, data]);
	}

	receive(event: string, data: unknown) {
		for (const handler of [...(this.handlers.get(event) ?? [])]) handler(data);
	}
}

describe('queryCrewMCPWebSocket carrier', () => {
	let fake: FakeSocket;

	beforeEach(() => {
		fake = new FakeSocket();
		(socketStore.value as ReturnType<typeof writable>).set(fake);
	});

	it('attaches the operation carrier to the crew-mcp-query payload', async () => {
		const pending = queryCrewMCPWebSocket('question', 'model-a', ['tool'], 'chat-1');

		const [[event, payload]] = fake.emitted;
		expect(event).toBe('crew-mcp-query');
		expect(payload).toMatchObject({
			query: 'question',
			model: 'model-a',
			selected_tools: ['tool'],
			chat_id: 'chat-1'
		});
		expect(payload.carrier.operation_id).toMatch(/^[A-Za-z0-9._-]{8,64}$/);
		expect(payload.carrier.operation_id).not.toBe(fake.id);
		expect(payload.carrier.traceparent).toMatch(/^00-[0-9a-f]{32}-[0-9a-f]{16}-01$/);

		fake.receive('crew-mcp-result', {
			result: 'ok',
			tools_used: [],
			success: true,
			operation_id: payload.carrier.operation_id
		});
		await expect(pending).resolves.toMatchObject({ result: 'ok' });
	});

	it('uses a fresh carrier per query and ignores replies for other operations', async () => {
		const first = queryCrewMCPWebSocket('one');
		const second = queryCrewMCPWebSocket('two');
		const [firstCarrier, secondCarrier] = fake.emitted.map(([, payload]) => payload.carrier);
		expect(firstCarrier.operation_id).not.toBe(secondCarrier.operation_id);

		fake.receive('crew-mcp-result', {
			result: 'second',
			tools_used: [],
			success: true,
			operation_id: secondCarrier.operation_id
		});
		fake.receive('crew-mcp-result', {
			result: 'first',
			tools_used: [],
			success: true,
			operation_id: firstCarrier.operation_id
		});

		await expect(first).resolves.toMatchObject({ result: 'first' });
		await expect(second).resolves.toMatchObject({ result: 'second' });
	});

	it('removes only its own listeners when a query times out', async () => {
		vi.useFakeTimers();
		try {
			const timedOut = queryCrewMCPWebSocket('slow');
			const timedOutResult = expect(timedOut).rejects.toThrow('timeout');
			vi.advanceTimersByTime(300000);
			const pending = queryCrewMCPWebSocket('still running');
			const pendingCarrier = fake.emitted[1][1].carrier;

			vi.advanceTimersByTime(300000);
			await timedOutResult;
			expect(fake.handlers.get('crew-mcp-result')?.size).toBe(1);

			fake.receive('crew-mcp-result', {
				result: 'done',
				tools_used: [],
				success: true,
				operation_id: pendingCarrier.operation_id
			});
			await expect(pending).resolves.toMatchObject({ result: 'done' });
		} finally {
			vi.useRealTimers();
		}
	});
});
