// Operation carrier for Socket.IO business events (OBS-27).
//
// Each logical operation gets its own random ID and W3C `traceparent`. The
// server validates both with the `X-Request-Id` rules and ignores invalid
// values. Never use the socket ID or an analytics (Faro) session ID here:
// those identify a connection or a browsing session, not an operation.

export const OPERATION_ID_PATTERN = /^[A-Za-z0-9._-]{8,64}$/;
const TRACEPARENT_PATTERN = /^00-(?!0{32})[0-9a-f]{32}-(?!0{16})[0-9a-f]{16}-[0-9a-f]{2}$/;

export type OperationCarrier = {
	operation_id: string;
	traceparent: string;
};

const randomHex = (bytes: number): string => {
	const values = new Uint8Array(bytes);
	crypto.getRandomValues(values);
	return Array.from(values, (value) => value.toString(16).padStart(2, '0')).join('');
};

const nonZeroHex = (bytes: number): string => {
	let value = randomHex(bytes);
	while (/^0+$/.test(value)) {
		value = randomHex(bytes);
	}
	return value;
};

export const createOperationId = (): string => randomHex(16);

export const createTraceparent = (): string => `00-${nonZeroHex(16)}-${nonZeroHex(8)}-01`;

export const isValidOperationId = (value: unknown, socketId?: string | null): value is string =>
	typeof value === 'string' && OPERATION_ID_PATTERN.test(value) && value !== socketId;

export const isValidTraceparent = (value: unknown): value is string =>
	typeof value === 'string' && TRACEPARENT_PATTERN.test(value);

export const createOperationCarrier = (socketId?: string | null): OperationCarrier => {
	let operationId = createOperationId();
	while (!isValidOperationId(operationId, socketId)) {
		operationId = createOperationId();
	}
	return { operation_id: operationId, traceparent: createTraceparent() };
};

export const withOperationCarrier = <T extends Record<string, unknown>>(
	payload: T,
	carrier: OperationCarrier
): T & { carrier: OperationCarrier } => ({ ...payload, carrier });

// Replies without an operation ID come from a server that predates OBS-27.
export const isReplyForOperation = (reply: unknown, carrier: OperationCarrier): boolean => {
	const operationId = (reply as { operation_id?: unknown } | null)?.operation_id;
	return operationId === undefined || operationId === carrier.operation_id;
};
