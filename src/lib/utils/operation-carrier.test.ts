import { describe, it, expect } from 'vitest';
import {
	createOperationCarrier,
	isReplyForOperation,
	isValidOperationId,
	isValidTraceparent,
	withOperationCarrier
} from './operation-carrier';

describe('operation carrier', () => {
	it('creates an operation ID that passes the server X-Request-Id rules', () => {
		const { operation_id } = createOperationCarrier();
		expect(operation_id).toMatch(/^[A-Za-z0-9._-]{8,64}$/);
	});

	it('creates a valid W3C traceparent', () => {
		const { traceparent } = createOperationCarrier();
		expect(traceparent).toMatch(/^00-[0-9a-f]{32}-[0-9a-f]{16}-01$/);
		expect(isValidTraceparent(traceparent)).toBe(true);
	});

	it('creates a new carrier for every operation', () => {
		const first = createOperationCarrier();
		const second = createOperationCarrier();
		expect(first.operation_id).not.toBe(second.operation_id);
		expect(first.traceparent.split('-')[1]).not.toBe(second.traceparent.split('-')[1]);
	});

	it('never uses the socket ID as an operation ID', () => {
		expect(isValidOperationId('abcdefgh1234', 'abcdefgh1234')).toBe(false);
		const carrier = createOperationCarrier('abcdefgh1234');
		expect(carrier.operation_id).not.toBe('abcdefgh1234');
	});

	it.each(['short', 'x'.repeat(65), 'has space 1234', 'semi;colon-1234', 42, null])(
		'rejects invalid operation ID %s',
		(value) => {
			expect(isValidOperationId(value)).toBe(false);
		}
	);

	it.each([
		'garbage',
		`00-${'0'.repeat(32)}-00f067aa0ba902b7-01`,
		`00-4bf92f3577b34da6a3ce929d0e0e4736-${'0'.repeat(16)}-01`,
		'00-4BF92F3577B34DA6A3CE929D0E0E4736-00f067aa0ba902b7-01'
	])('rejects invalid traceparent %s', (value) => {
		expect(isValidTraceparent(value)).toBe(false);
	});

	it('attaches the carrier without changing the payload', () => {
		const carrier = createOperationCarrier();
		const payload = withOperationCarrier({ query: 'q', chat_id: 'c' }, carrier);
		expect(payload).toEqual({ query: 'q', chat_id: 'c', carrier });
	});

	it('matches replies by operation ID', () => {
		const carrier = createOperationCarrier();
		expect(isReplyForOperation({ operation_id: carrier.operation_id }, carrier)).toBe(true);
		expect(isReplyForOperation({ operation_id: 'other-operation-id' }, carrier)).toBe(false);
		expect(isReplyForOperation({ result: 'legacy reply' }, carrier)).toBe(true);
	});
});
