/**
 * Shared browser request helpers.
 * They attach bearer auth, mark requests that should skip silent retry, and
 * rely on a single fetch interceptor to refresh and replay internal API calls.
 */

import { browser } from '$app/environment';

import { WEBUI_BASE_URL } from '$lib/constants';
import {
	clearAuthState,
	getAccessTokenValue,
	isAuthFailure,
	refreshAuthSession
} from '$lib/services/auth';

export type ApiRequestOptions = RequestInit & {
	token?: string | null;
	includeAuth?: boolean;
	retryOnUnauthorized?: boolean;
};

const UNAUTHORIZED_ERROR = 'Unauthorized';
// Private marker used to opt a request out of the interceptor's 401 refresh retry.
const SKIP_AUTH_RETRY_HEADER = 'x-owui-skip-401-retry';
const INTERNAL_API_PATH_PREFIXES = ['/api', '/ollama', '/openai', '/mcp'];

let nativeFetch: typeof fetch | null = null;
let fetchInterceptorInstalled = false;

/** Picks the token to send, treating blank defaults as an omitted token. */
const resolveRequestToken = (explicitToken?: string | null) => {
	return explicitToken === '' || !explicitToken ? getAccessTokenValue() : explicitToken;
};

/** Detects whether the request body should default to JSON content headers. */
const shouldSetJsonContentType = (body: BodyInit | null | undefined) => {
	return (
		body !== undefined &&
		body !== null &&
		!(body instanceof FormData) &&
		!(body instanceof URLSearchParams) &&
		!(body instanceof Blob) &&
		!(body instanceof ArrayBuffer) &&
		!ArrayBuffer.isView(body)
	);
};

/** Returns the original fetch implementation even after the interceptor is installed. */
const getNativeFetch = () => {
	if (nativeFetch) {
		return nativeFetch;
	}

	return globalThis.fetch.bind(globalThis);
};

/** Resolves the same-origin base used to decide whether a request is interceptor-managed. */
const getWebUiOrigin = () => {
	if (!browser) {
		return null;
	}

	return WEBUI_BASE_URL ? new URL(WEBUI_BASE_URL, location.origin).origin : location.origin;
};

/** Limits refresh retries to known internal API prefixes on the current app origin. */
const isInternalApiRequest = (requestUrl: URL) => {
	const webUiOrigin = getWebUiOrigin();

	if (!webUiOrigin || requestUrl.origin !== webUiOrigin) {
		return false;
	}

	return INTERNAL_API_PATH_PREFIXES.some((prefix) => requestUrl.pathname.startsWith(prefix));
};

/** Prevents the interceptor from trying to refresh the refresh endpoint itself. */
const isRefreshRequest = (requestUrl: URL) => requestUrl.pathname === '/api/v1/auths/refresh';

/** Checks whether the outgoing request is using bearer authentication. */
const usesBearerAuth = (request: Request) => {
	const authorizationHeader = request.headers.get('Authorization');
	return Boolean(
		authorizationHeader &&
			authorizationHeader.startsWith('Bearer ') &&
			authorizationHeader.slice('Bearer '.length).trim()
	);
};

/** Replaces legacy blank bearer headers with the current in-memory token when available. */
const normalizeInternalApiAuth = (request: Request) => {
	const requestUrl = new URL(request.url);
	if (!isInternalApiRequest(requestUrl) || isRefreshRequest(requestUrl)) {
		return request;
	}

	const latestAccessToken = getAccessTokenValue();
	const headers = new Headers(request.headers);
	const authorizationHeader = request.headers.get('Authorization');
	if (!authorizationHeader) {
		if (!latestAccessToken) {
			return request;
		}

		headers.set('Authorization', `Bearer ${latestAccessToken}`);
		return new Request(request, { headers });
	}

	if (!authorizationHeader.startsWith('Bearer ') || authorizationHeader.slice('Bearer '.length).trim()) {
		return request;
	}

	if (latestAccessToken) {
		headers.set('Authorization', `Bearer ${latestAccessToken}`);
	} else {
		headers.delete('Authorization');
	}

	return new Request(request, { headers });
};

/** Reads and removes interceptor-only headers before the request hits the network. */
const stripInterceptorHeaders = (request: Request) => {
	const headers = new Headers(request.headers);
	const skipAuthRetry = headers.get(SKIP_AUTH_RETRY_HEADER) === 'true';

	headers.delete(SKIP_AUTH_RETRY_HEADER);

	return {
		skipAuthRetry,
		request: new Request(request, { headers })
	};
};

/** Rebuilds a failed request with the latest access token after refresh succeeds. */
const buildRetryRequest = (request: Request) => {
	const latestAccessToken = getAccessTokenValue();
	if (!latestAccessToken) {
		return request;
	}

	const headers = new Headers(request.headers);
	headers.set('Authorization', `Bearer ${latestAccessToken}`);

	return new Request(request, { headers });
};

/** Decides whether a 401 should trigger a silent refresh-and-retry cycle. */
const shouldRetryUnauthorizedResponse = (
	request: Request,
	response: Response,
	allowRetry: boolean,
	skipAuthRetry: boolean
) => {
	if (!browser || !allowRetry || skipAuthRetry || response.status !== 401) {
		return false;
	}

	const requestUrl = new URL(request.url);
	if (!isInternalApiRequest(requestUrl) || isRefreshRequest(requestUrl)) {
		return false;
	}

	return usesBearerAuth(request);
};

/** Runs a request through the interceptor and retries once after a successful refresh. */
const performInterceptedFetch = async (request: Request, allowRetry: boolean) => {
	const { request: strippedRequest, skipAuthRetry } = stripInterceptorHeaders(request);
	const sanitizedRequest = normalizeInternalApiAuth(strippedRequest);
	const response = await getNativeFetch()(sanitizedRequest.clone());

	if (!shouldRetryUnauthorizedResponse(sanitizedRequest, response, allowRetry, skipAuthRetry)) {
		return response;
	}

	try {
		const sessionUser = await refreshAuthSession();
		if (!sessionUser?.token) {
			return response;
		}
	} catch (error) {
		if (isAuthFailure(error)) {
			clearAuthState();
		}
		return response;
	}

	return getNativeFetch()(buildRetryRequest(sanitizedRequest));
};

/** Installs the global browser fetch interceptor exactly once. */
export const installApiFetchInterceptor = () => {
	if (!browser || fetchInterceptorInstalled) {
		return;
	}

	nativeFetch = globalThis.fetch.bind(globalThis);
	globalThis.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
		const request = new Request(input, init);
		return performInterceptedFetch(request, true);
	}) as typeof fetch;
	fetchInterceptorInstalled = true;
};

/** Builds request headers, including auth injection and interceptor opt-out metadata. */
const buildRequestHeaders = (
	headersInit: HeadersInit | undefined,
	body: BodyInit | null | undefined,
	token: string | null | undefined,
	includeAuth: boolean,
	skipAuthRetry: boolean
) => {
	const headers = new Headers(headersInit);

	if (includeAuth && token && !headers.has('Authorization')) {
		headers.set('Authorization', `Bearer ${token}`);
	}

	if (!headers.has('Content-Type') && shouldSetJsonContentType(body)) {
		headers.set('Content-Type', 'application/json');
	}

	if (skipAuthRetry) {
		headers.set(SKIP_AUTH_RETRY_HEADER, 'true');
	}

	return headers;
};

/** Normalizes JSON and text error payloads into a single thrown value. */
const parseResponseError = async (response: Response): Promise<unknown> => {
	const contentType = response.headers.get('content-type') ?? '';

	if (contentType.includes('application/json')) {
		const payload = await response.json().catch(() => null);
		if (
			payload &&
			typeof payload === 'object' &&
			'detail' in payload &&
			typeof payload.detail === 'string'
		) {
			return payload.detail;
		}

		return payload ?? response.statusText;
	}

	const text = await response.text().catch(() => '');
	return text || response.statusText || UNAUTHORIZED_ERROR;
};

/** Sends a request through the global fetch path with client-managed headers. */
const executeRequest = async (url: string, options: ApiRequestOptions) => {
	const { token, includeAuth = true, retryOnUnauthorized = true, ...requestInit } = options;
	const resolvedToken = includeAuth ? resolveRequestToken(token) : null;

	return fetch(url, {
		...requestInit,
		headers: buildRequestHeaders(
			requestInit.headers,
			requestInit.body,
			resolvedToken,
			includeAuth,
			!retryOnUnauthorized
		)
	});
};

/** Returns the raw response while still applying client header and retry rules. */
export const apiRequest = async (url: string, options: ApiRequestOptions = {}) => {
	return executeRequest(url, options);
};

/** Executes a request and only validates that it completed successfully. */
export const apiVoid = async (url: string, options: ApiRequestOptions = {}) => {
	const response = await apiRequest(url, options);

	if (!response.ok) {
		throw await parseResponseError(response);
	}
};

/** Executes a request and parses a successful JSON response body. */
export const apiJson = async <T = unknown>(url: string, options: ApiRequestOptions = {}) => {
	const response = await apiRequest(url, options);

	if (!response.ok) {
		throw await parseResponseError(response);
	}

	if (response.status === 204) {
		return null as T;
	}

	const contentType = response.headers.get('content-type') ?? '';
	if (!contentType.includes('application/json')) {
		return null as T;
	}

	return (await response.json()) as T;
};

/** Executes a request and returns its successful text response body. */
export const apiText = async (url: string, options: ApiRequestOptions = {}) => {
	const response = await apiRequest(url, options);

	if (!response.ok) {
		throw await parseResponseError(response);
	}

	return response.text();
};

/** Executes a request and returns its successful blob response body. */
export const apiBlob = async (url: string, options: ApiRequestOptions = {}) => {
	const response = await apiRequest(url, options);

	if (!response.ok) {
		throw await parseResponseError(response);
	}

	return response.blob();
};
