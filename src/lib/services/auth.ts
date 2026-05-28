import { browser } from '$app/environment';

import { WEBUI_API_BASE_URL } from '$lib/constants';
import type { SessionUser } from '$lib/stores';
import { authState, initialAuthState } from '$lib/stores/auth';

import { get } from 'svelte/store';

/**
 * Browser auth owner for short-lived access tokens.
 * The refresh token stays in an HTTP-only cookie; this module keeps the access
 * token in memory and coordinates bootstrap, refresh, and logout state.
 */
const ACCESS_TOKEN_STORAGE_KEY = 'token';
const AUTH_FAILURE_PATTERNS = [
	'unauthorized',
	'not authenticated',
	'invalid token',
	'missing refresh token',
	'invalid refresh token'
];

// Purge the old persisted token whenever auth state changes so memory stays authoritative.
const clearLegacyAccessToken = () => {
	if (!browser) {
		return;
	}

	localStorage.removeItem(ACCESS_TOKEN_STORAGE_KEY);
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
	const response = await fetch(`${WEBUI_API_BASE_URL}${path}`, {
		method,
		headers: {
			...headers,
			...(token ? { Authorization: `Bearer ${token}` } : {})
		},
		credentials: 'include'
	});

	if (!response.ok) {
		throw await readAuthErrorPayload(response);
	}

	return (await response.json()) as SessionUser;
};

export const getAuthState = () => get(authState);

export const getAccessTokenValue = () => get(authState).accessToken;

export const getRequestToken = () => getAccessTokenValue() ?? '';

export const getAccessTokenExpiryValue = () => get(authState).accessTokenExpiresAt;

export const hydrateAuthState = () => {
	// The old flow persisted access tokens; keep a one-time migration read and clear it immediately.
	const persistedAccessToken = browser ? localStorage.getItem(ACCESS_TOKEN_STORAGE_KEY) : null;
	const previousState = get(authState);

	clearLegacyAccessToken();

	authState.update((state) => ({
		...state,
		accessToken: persistedAccessToken,
		accessTokenExpiresAt:
			persistedAccessToken === previousState.accessToken ? previousState.accessTokenExpiresAt : null,
		isLoggingOut: false
	}));

	return persistedAccessToken;
};

const getAuthErrorMessage = (error: unknown): string => {
	if (typeof error === 'string') {
		return error.toLowerCase();
	}

	if (error && typeof error === 'object') {
		if ('detail' in error && typeof error.detail === 'string') {
			return error.detail.toLowerCase();
		}

		if ('message' in error && typeof error.message === 'string') {
			return error.message.toLowerCase();
		}
	}

	return '';
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
	return nextAccessToken;
};

export const bootstrapAuthSession = async (token: string | null = null) => {
	try {
		const sessionUser = await requestSessionUser('/auths/', 'GET', token);
		setAuthSession(sessionUser);
		return sessionUser;
	} catch (error) {
		clearAuthState();
		throw error;
	}
};

export const isAuthFailure = (error: unknown) => {
	const message = getAuthErrorMessage(error);
	return AUTH_FAILURE_PATTERNS.some((pattern) => message.includes(pattern));
};

export const clearAuthState = () => {
	clearLegacyAccessToken();
	authState.set(initialAuthState);
};

export const startLogout = () => {
	authState.update((state) => ({
		...state,
		isLoggingOut: true,
		refreshPromise: null
	}));
};

export const endLogout = () => {
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

export const refreshAuthSession = async (): Promise<SessionUser | null> => {
	const currentState = get(authState);
	if (currentState.isLoggingOut) {
		return null;
	}

	// Share one in-flight refresh across parallel callers and 401 retries.
	if (currentState.refreshPromise) {
		return currentState.refreshPromise as Promise<SessionUser | null>;
	}

	const refreshPromise: Promise<SessionUser | null> = requestRefreshSession(
		currentState.accessToken ?? null
	)
		.then((sessionUser) => {
			if (!sessionUser) {
				clearAuthState();
				return null;
			}

			setAuthSession(sessionUser as SessionUser);
			return sessionUser as SessionUser;
		})
		.catch((error) => {
			clearAuthState();
			throw error;
		})
		.finally(() => {
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
