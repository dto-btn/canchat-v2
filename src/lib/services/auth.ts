import { browser, dev } from '$app/environment';

import { WEBUI_API_BASE_URL } from '$lib/constants';
import type { SessionUser } from '$lib/stores';
import { authState, initialAuthState } from '$lib/stores/auth';

import { get } from 'svelte/store';

/**
 * Browser auth owner for short-lived access tokens.
 * The refresh token stays in an HTTP-only cookie; this module keeps the access
 * token in memory and coordinates bootstrap, refresh, and logout state.
 */
const LEGACY_ACCESS_TOKEN_STORAGE_KEY = 'token';
const AUTH_DEBUG_STORAGE_KEY = 'owui.auth.debug';
const AUTH_SYNC_CHANNEL_NAME = 'auth-session-channel';
const AUTH_RECOVERY_STORAGE_KEY = 'owui.auth.recovery';
const AUTH_SESSION_INVALIDATED_EVENT_NAME = 'owui:auth-session-invalidated';
const AUTH_FAILURE_PATTERNS = [
	'unauthorized',
	'not authenticated',
	'invalid token',
	'missing refresh token',
	'invalid refresh token',
	'refresh session is invalid',
	'session has expired',
	'sign in again'
];
const SESSION_AUTH_ENDPOINTS = new Set(['/auths/', '/auths/refresh']);

type AuthLogLevel = 'debug' | 'info' | 'warn' | 'error';
type AuthSyncEventType = 'logout' | 'session-expired' | 'session-restored';
type AuthSyncEvent = {
	type: AuthSyncEventType;
	issuedAt: number;
};
type AuthRequestErrorKind = 'auth' | 'transport' | 'sync' | 'unknown';
type AuthRequestError = {
	kind: AuthRequestErrorKind;
	message: string;
	status: number | null;
	payload?: unknown;
	cause?: unknown;
};
type AuthRecoveryCheckpoint = {
	path: string;
	reason: AuthSyncEventType;
	createdAt: number;
};
type AuthSessionInvalidatedDetail = {
	reason: 'refresh-auth-failure';
	issuedAt: number;
	message: string | null;
	status: number | null;
};

let accessTokenExpiryLogTimeout: number | null = null;
let lastObservedAccessTokenExpiry: number | null = null;
let lastLoggedExpiredAccessTokenAt: number | null = null;
let authSyncChannel: BroadcastChannel | null = null;

const isAuthLoggingEnabled = () => {
	if (!browser) {
		return false;
	}

	try {
		return dev || localStorage.getItem(AUTH_DEBUG_STORAGE_KEY) === 'true';
	} catch {
		return dev;
	}
};

export const logAuthEvent = (
	level: AuthLogLevel,
	event: string,
	details: Record<string, unknown> = {}
) => {
	if (!isAuthLoggingEnabled()) {
		return;
	}

	const message = `[auth] ${event}`;
	switch (level) {
		case 'debug':
			console.debug(message, details);
			break;
		case 'warn':
			console.warn(message, details);
			break;
		case 'error':
			console.error(message, details);
			break;
		default:
			console.info(message, details);
	}
};

const getAuthErrorMessage = (error: unknown): string => {
	if (typeof error === 'string') {
		return error;
	}

	if (error && typeof error === 'object') {
		if ('detail' in error && typeof error.detail === 'string') {
			return error.detail;
		}

		if ('message' in error && typeof error.message === 'string') {
			return error.message;
		}
	}

	return '';
};

const getNormalizedAuthErrorMessage = (error: unknown) => getAuthErrorMessage(error).toLowerCase();

const isSessionAuthRequest = (path: string | null | undefined) =>
	Boolean(path && SESSION_AUTH_ENDPOINTS.has(path));

const isAuthRequestError = (error: unknown): error is AuthRequestError => {
	return Boolean(
		error && typeof error === 'object' && 'kind' in error && 'message' in error && 'status' in error
	);
};

const getAuthRequestErrorKind = (
	status: number | null,
	error: unknown,
	path?: string | null
): AuthRequestErrorKind => {
	if (status === 409) {
		return 'sync';
	}

	const normalizedMessage = getNormalizedAuthErrorMessage(error);
	if (
		AUTH_FAILURE_PATTERNS.some((pattern) => normalizedMessage.includes(pattern)) ||
		status === 401 ||
		status === 403 ||
		(status === 400 && isSessionAuthRequest(path))
	) {
		return 'auth';
	}

	if (status === null || status >= 500) {
		return 'transport';
	}

	return 'unknown';
};

const buildAuthRequestError = (
	status: number | null,
	error: unknown,
	fallbackMessage: string,
	extra: Partial<Pick<AuthRequestError, 'payload' | 'cause'>> = {},
	path?: string | null
): AuthRequestError => ({
	kind: getAuthRequestErrorKind(status, error, path),
	message: getAuthErrorMessage(error) || fallbackMessage,
	status,
	...extra
});

const getAccessTokenRemainingSeconds = () => {
	const expiresAt = get(authState).accessTokenExpiresAt;
	if (!expiresAt) {
		return null;
	}

	return expiresAt - Math.floor(Date.now() / 1000);
};

const scheduleAccessTokenExpiryLog = (expiresAt: number) => {
	if (accessTokenExpiryLogTimeout !== null) {
		window.clearTimeout(accessTokenExpiryLogTimeout);
		accessTokenExpiryLogTimeout = null;
	}

	const delayMs = Math.max(expiresAt * 1000 - Date.now() + 500, 0);
	accessTokenExpiryLogTimeout = window.setTimeout(() => {
		accessTokenExpiryLogTimeout = null;
		const state = get(authState);
		if (!state.accessToken || state.accessTokenExpiresAt !== expiresAt) {
			return;
		}

		lastLoggedExpiredAccessTokenAt = expiresAt;
		logAuthEvent('warn', 'access-token-expired', {
			expiresAt,
			expired: true,
			remainingSeconds: getAccessTokenRemainingSeconds()
		});
	}, delayMs);
};

const stopAccessTokenExpiryLogging = () => {
	if (accessTokenExpiryLogTimeout !== null) {
		window.clearTimeout(accessTokenExpiryLogTimeout);
		accessTokenExpiryLogTimeout = null;
	}

	lastObservedAccessTokenExpiry = null;
	lastLoggedExpiredAccessTokenAt = null;
};

const syncAccessTokenExpiryLogging = () => {
	if (!browser) {
		return;
	}

	const state = get(authState);
	if (!state.accessToken || !state.accessTokenExpiresAt) {
		stopAccessTokenExpiryLogging();
		return;
	}

	if (lastObservedAccessTokenExpiry !== state.accessTokenExpiresAt) {
		lastObservedAccessTokenExpiry = state.accessTokenExpiresAt;
		lastLoggedExpiredAccessTokenAt = null;
		scheduleAccessTokenExpiryLog(state.accessTokenExpiresAt);
		return;
	}

	if (
		accessTokenExpiryLogTimeout === null &&
		lastLoggedExpiredAccessTokenAt !== state.accessTokenExpiresAt
	) {
		scheduleAccessTokenExpiryLog(state.accessTokenExpiresAt);
	}
};

if (browser) {
	authState.subscribe(() => {
		syncAccessTokenExpiryLogging();
	});
}

// Purge the old persisted token whenever auth state changes so memory stays authoritative.
const clearLegacyAccessToken = () => {
	if (!browser) {
		return;
	}

	localStorage.removeItem(LEGACY_ACCESS_TOKEN_STORAGE_KEY);
};

const getAuthSyncChannel = () => {
	if (!browser) {
		return null;
	}

	if (authSyncChannel === null) {
		authSyncChannel = new BroadcastChannel(AUTH_SYNC_CHANNEL_NAME);
	}

	return authSyncChannel;
};

const normalizeAuthRecoveryPath = (path: string | null | undefined) => {
	if (!path || path === '/auth') {
		return '/';
	}

	return path.startsWith('/auth?') || path.startsWith('/auth#') ? '/' : path;
};

export const broadcastAuthSyncEvent = (type: AuthSyncEventType) => {
	const channel = getAuthSyncChannel();
	if (!channel) {
		return;
	}

	channel.postMessage({
		type,
		issuedAt: Date.now()
	} satisfies AuthSyncEvent);
	logAuthEvent('debug', 'auth-sync-broadcast', { type });
};

export const subscribeToAuthSyncEvents = (handler: (event: AuthSyncEvent) => void) => {
	const channel = getAuthSyncChannel();
	if (!channel) {
		return () => {};
	}

	const listener = (event: MessageEvent<AuthSyncEvent>) => {
		if (event.data?.type) {
			handler(event.data);
		}
	};

	channel.addEventListener('message', listener);
	return () => {
		channel.removeEventListener('message', listener);
	};
};

const dispatchAuthSessionInvalidated = (
	reason: AuthSessionInvalidatedDetail['reason'],
	error?: unknown
) => {
	if (!browser) {
		return;
	}

	const message = getAuthErrorMessage(error) || null;
	const status = isAuthRequestError(error) ? error.status : null;

	window.dispatchEvent(
		new CustomEvent<AuthSessionInvalidatedDetail>(AUTH_SESSION_INVALIDATED_EVENT_NAME, {
			detail: {
				reason,
				issuedAt: Date.now(),
				message,
				status
			}
		})
	);
	logAuthEvent('warn', 'auth-session-invalidated', { reason, message, status });
};

export const subscribeToAuthSessionInvalidationEvents = (
	handler: (detail: AuthSessionInvalidatedDetail) => void | Promise<void>
) => {
	if (!browser) {
		return () => {};
	}

	const listener = (event: Event) => {
		const detail = (event as CustomEvent<AuthSessionInvalidatedDetail>).detail;
		if (!detail?.reason) {
			return;
		}

		handler(detail);
	};

	window.addEventListener(AUTH_SESSION_INVALIDATED_EVENT_NAME, listener as EventListener);
	return () => {
		window.removeEventListener(
			AUTH_SESSION_INVALIDATED_EVENT_NAME,
			listener as EventListener
		);
	};
};

export const saveAuthRecoveryCheckpoint = (
	path: string,
	reason: AuthSyncEventType = 'session-expired'
) => {
	if (!browser) {
		return;
	}

	const normalizedPath = normalizeAuthRecoveryPath(path);
	const checkpoint: AuthRecoveryCheckpoint = {
		path: normalizedPath,
		reason,
		createdAt: Date.now()
	};
	sessionStorage.setItem(AUTH_RECOVERY_STORAGE_KEY, JSON.stringify(checkpoint));
	logAuthEvent('info', 'auth-recovery-checkpoint-saved', {
		path: normalizedPath,
		reason
	});
};

export const consumeAuthRecoveryCheckpoint = () => {
	if (!browser) {
		return null;
	}

	const rawCheckpoint = sessionStorage.getItem(AUTH_RECOVERY_STORAGE_KEY);
	if (!rawCheckpoint) {
		return null;
	}

	sessionStorage.removeItem(AUTH_RECOVERY_STORAGE_KEY);

	try {
		const checkpoint = JSON.parse(rawCheckpoint) as AuthRecoveryCheckpoint;
		const normalizedPath = normalizeAuthRecoveryPath(checkpoint?.path);
		logAuthEvent('info', 'auth-recovery-checkpoint-consumed', {
			path: normalizedPath,
			reason: checkpoint?.reason ?? null
		});
		return normalizedPath;
	} catch {
		return '/';
	}
};

export const clearAuthRecoveryCheckpoint = () => {
	if (!browser) {
		return;
	}

	sessionStorage.removeItem(AUTH_RECOVERY_STORAGE_KEY);
	logAuthEvent('debug', 'auth-recovery-checkpoint-cleared');
};

const readAuthErrorPayload = async (response: Response) => {
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

		return payload ?? 'Unable to refresh session';
	}

	const text = await response.text().catch(() => '');
	return text || response.statusText || 'Unable to refresh session';
};

// Bootstrap and refresh bypass the shared API client so refresh handling cannot recurse.
const requestSessionUser = async (
	path: string,
	method: 'GET' | 'POST',
	token: string | null,
	headers: HeadersInit = {}
) => {
	logAuthEvent('debug', 'session-request-started', {
		path,
		method,
		hasBearerToken: Boolean(token)
	});

	let response: Response;
	try {
		response = await fetch(`${WEBUI_API_BASE_URL}${path}`, {
			method,
			headers: {
				...headers,
				...(token ? { Authorization: `Bearer ${token}` } : {})
			},
			credentials: 'include'
		});
	} catch (error) {
		const requestError = buildAuthRequestError(
			null,
			error,
			'Unable to reach the authentication service',
			{ cause: error },
			path
		);
		logAuthEvent('warn', 'session-request-failed', {
			path,
			method,
			status: null,
			error: requestError.message,
			kind: requestError.kind
		});
		throw requestError;
	}

	if (!response.ok) {
		const errorPayload = await readAuthErrorPayload(response);
		const requestError = buildAuthRequestError(
			response.status,
			errorPayload,
			'Unable to refresh session',
			{ payload: errorPayload },
			path
		);
		logAuthEvent('warn', 'session-request-failed', {
			path,
			method,
			status: response.status,
			error: requestError.message,
			kind: requestError.kind
		});
		throw requestError;
	}

	const sessionUser = (await response.json()) as SessionUser;
	logAuthEvent('info', 'session-request-succeeded', {
		path,
		method,
		expiresAt: sessionUser?.expires_at ?? null
	});
	return sessionUser;
};

export const getAuthState = () => get(authState);

export const getAccessTokenValue = () => get(authState).accessToken;

export const getRequestToken = () => getAccessTokenValue() ?? '';

export const getAccessTokenExpiryValue = () => get(authState).accessTokenExpiresAt;

export const hydrateAuthState = () => {
	// Access tokens are memory-only now. Keep startup focused on purging any stale legacy token.
	clearLegacyAccessToken();

	authState.update((state) => ({
		...state,
		accessToken: null,
		accessTokenExpiresAt: null,
		bootstrapStatus: 'idle',
		bootstrapPromise: null,
		refreshPromise: null,
		isLoggingOut: false
	}));

	logAuthEvent('debug', 'auth-state-hydrated');
	return null;
};

export const setAuthSession = (
	sessionUser: Pick<SessionUser, 'token' | 'expires_at'> | null | undefined
) => {
	const nextAccessToken = sessionUser?.token ?? null;
	const nextAccessTokenExpiresAt = sessionUser?.expires_at ?? null;

	clearLegacyAccessToken();
	authState.update((state) => ({
		...state,
		accessToken: nextAccessToken,
		accessTokenExpiresAt: nextAccessTokenExpiresAt,
		isLoggingOut: false,
		refreshPromise: null
	}));
	logAuthEvent('info', 'auth-session-updated', {
		hasAccessToken: Boolean(nextAccessToken),
		expiresAt: nextAccessTokenExpiresAt,
		remainingSeconds:
			nextAccessTokenExpiresAt !== null
				? nextAccessTokenExpiresAt - Math.floor(Date.now() / 1000)
				: null
	});
	return nextAccessToken;
};

const recoverAuthSessionFromSyncFailure = async (
	source: 'bootstrap' | 'refresh'
): Promise<SessionUser> => {
	logAuthEvent('info', `${source}-auth-session-sync-started`);

	try {
		const sessionUser = await requestSessionUser('/auths/', 'GET', null);
		setAuthSession(sessionUser);
		logAuthEvent('info', `${source}-auth-session-sync-succeeded`, {
			expiresAt: sessionUser?.expires_at ?? null
		});
		return sessionUser;
	} catch (error) {
		logAuthEvent('warn', `${source}-auth-session-sync-failed`, {
			error: getAuthErrorMessage(error),
			kind: isAuthRequestError(error) ? error.kind : 'unknown'
		});
		if (isAuthFailure(error)) {
			clearAuthState();
			if (source === 'refresh') {
				dispatchAuthSessionInvalidated('refresh-auth-failure', error);
			}
		}
		throw error;
	}
};

export const bootstrapAuthSession = async (token: string | null = null) => {
	const currentState = get(authState);
	if (currentState.bootstrapPromise) {
		logAuthEvent('debug', 'bootstrap-auth-session-reused-inflight-promise', {
			hasBootstrapToken: Boolean(token)
		});
		return currentState.bootstrapPromise as Promise<SessionUser | null>;
	}

	logAuthEvent('info', 'bootstrap-auth-session-started', {
		hasBootstrapToken: Boolean(token)
	});

	const bootstrapPromise: Promise<SessionUser | null> = requestSessionUser(
		'/auths/',
		'GET',
		token
	)
		.then((sessionUser) => {
			setAuthSession(sessionUser);
			logAuthEvent('info', 'bootstrap-auth-session-succeeded', {
				expiresAt: sessionUser?.expires_at ?? null
			});
			return sessionUser;
		})
		.catch((error) => {
			logAuthEvent('warn', 'bootstrap-auth-session-failed', {
				error: getAuthErrorMessage(error),
				kind: isAuthRequestError(error) ? error.kind : 'unknown'
			});
			if (isAuthSyncFailure(error)) {
				return recoverAuthSessionFromSyncFailure('bootstrap');
			}
			if (isAuthFailure(error)) {
				clearAuthState();
			}
			throw error;
		})
		.finally(() => {
			authState.update((state) => ({
				...state,
				bootstrapStatus: 'ready',
				bootstrapPromise:
					state.bootstrapPromise === bootstrapPromise ? null : state.bootstrapPromise
			}));
		});

	authState.update((state) => ({
		...state,
		bootstrapStatus: 'pending',
		bootstrapPromise
	}));

	return bootstrapPromise;
};

export const isAuthFailure = (error: unknown) => {
	if (isAuthRequestError(error)) {
		return error.kind === 'auth';
	}

	const message = getNormalizedAuthErrorMessage(error);
	return AUTH_FAILURE_PATTERNS.some((pattern) => message.includes(pattern));
};

export const isAuthSyncFailure = (error: unknown) => {
	return isAuthRequestError(error) ? error.kind === 'sync' : false;
};

export const isAuthTransportFailure = (error: unknown) => {
	if (isAuthRequestError(error)) {
		return error.kind === 'transport';
	}

	const message = getNormalizedAuthErrorMessage(error);
	return (
		error instanceof TypeError ||
		message.includes('failed to fetch') ||
		message.includes('load failed') ||
		message.includes('network')
	);
};

export const clearAuthState = () => {
	const previousState = get(authState);
	logAuthEvent('info', 'auth-state-cleared', {
		hadAccessToken: Boolean(previousState.accessToken),
		expiresAt: previousState.accessTokenExpiresAt
	});
	clearLegacyAccessToken();
	authState.set({
		...initialAuthState,
		bootstrapStatus: previousState.bootstrapStatus,
		bootstrapPromise: null
	});
};

export const startLogout = () => {
	logAuthEvent('info', 'logout-started');
	authState.update((state) => ({
		...state,
		isLoggingOut: true,
		refreshPromise: null
	}));
};

export const endLogout = () => {
	logAuthEvent('info', 'logout-ended');
	authState.update((state) => ({
		...state,
		isLoggingOut: false
	}));
};

export const hasAccessTokenExpired = (bufferSeconds: number = 0) => {
	const expiresAt = get(authState).accessTokenExpiresAt;
	if (!expiresAt) {
		return true;
	}

	return expiresAt <= Math.floor(Date.now() / 1000) + bufferSeconds;
};

export const ensureFreshAccessToken = async (bufferSeconds: number = 30) => {
	const currentState = get(authState);
	if (currentState.isLoggingOut) {
		return null;
	}

	if (currentState.bootstrapPromise) {
		try {
			const sessionUser = (await currentState.bootstrapPromise) as SessionUser | null;
			return sessionUser?.token ?? get(authState).accessToken;
		} catch {
			return get(authState).accessToken;
		}
	}

	if (currentState.accessToken && !hasAccessTokenExpired(bufferSeconds)) {
		return currentState.accessToken;
	}

	if (currentState.bootstrapStatus === 'ready' && !currentState.accessToken) {
		return null;
	}

	try {
		const sessionUser = await refreshAuthSession();
		return sessionUser?.token ?? get(authState).accessToken;
	} catch (error) {
		logAuthEvent('warn', 'ensure-fresh-access-token-failed', {
			error: getAuthErrorMessage(error),
			kind: isAuthRequestError(error) ? error.kind : 'unknown'
		});
		return get(authState).accessToken;
	}
};

export const refreshAuthSession = async (): Promise<SessionUser | null> => {
	const currentState = get(authState);
	if (currentState.isLoggingOut) {
		logAuthEvent('debug', 'refresh-skipped-during-logout');
		return null;
	}

	// Share one in-flight refresh across parallel callers and 401 retries.
	if (currentState.refreshPromise) {
		logAuthEvent('debug', 'refresh-reused-inflight-promise');
		return currentState.refreshPromise as Promise<SessionUser | null>;
	}

	logAuthEvent('info', 'refresh-started', {
		hasAccessToken: Boolean(currentState.accessToken),
		expiresAt: currentState.accessTokenExpiresAt,
		remainingSeconds: getAccessTokenRemainingSeconds()
	});

	const refreshPromise: Promise<SessionUser | null> = requestRefreshSession(
		currentState.accessToken ?? null
	)
		.then((sessionUser) => {
			if (!sessionUser) {
				logAuthEvent('warn', 'refresh-returned-empty-session');
				clearAuthState();
				return null;
			}

			setAuthSession(sessionUser as SessionUser);
			logAuthEvent('info', 'refresh-succeeded', {
				expiresAt: sessionUser.expires_at ?? null,
				remainingSeconds:
					sessionUser.expires_at != null
						? sessionUser.expires_at - Math.floor(Date.now() / 1000)
						: null
			});
			return sessionUser as SessionUser;
		})
		.catch((error) => {
			if (isAuthSyncFailure(error)) {
				return recoverAuthSessionFromSyncFailure('refresh');
			}

			const authFailure = isAuthFailure(error);
			logAuthEvent('warn', 'refresh-failed', {
				error: getAuthErrorMessage(error),
				kind: isAuthRequestError(error) ? error.kind : 'unknown',
				authFailure
			});
			if (authFailure) {
				clearAuthState();
				dispatchAuthSessionInvalidated('refresh-auth-failure', error);
			}
			throw error;
		})
		.finally(() => {
			logAuthEvent('debug', 'refresh-finished');
			authState.update((state) =>
				state.refreshPromise === refreshPromise ? { ...state, refreshPromise: null } : state
			);
		});

	authState.update((state) => ({
		...state,
		refreshPromise
	}));

	return refreshPromise;
};

const requestRefreshSession = async (token: string | null) => {
	return requestSessionUser('/auths/refresh', 'POST', token, {
		'Content-Type': 'application/json'
	});
};
