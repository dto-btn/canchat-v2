import { derived, writable } from 'svelte/store';

/**
 * Minimal auth lifecycle state.
 * Profile data stays in the shared user store; this store only tracks the
 * in-memory access token and refresh coordination.
 */
export type AuthState = {
	accessToken: string | null;
	accessTokenExpiresAt: number | null;
	isLoggingOut: boolean;
	// Shared in-flight refresh used to dedupe concurrent callers.
	refreshPromise: Promise<unknown> | null;
};

export const initialAuthState: AuthState = {
	accessToken: null,
	accessTokenExpiresAt: null,
	isLoggingOut: false,
	refreshPromise: null
};

export const authState = writable<AuthState>(initialAuthState);

export const accessToken = derived(authState, ($authState) => $authState.accessToken);
export const accessTokenExpiresAt = derived(
	authState,
	($authState) => $authState.accessTokenExpiresAt
);
export const authLogoutInProgress = derived(authState, ($authState) => $authState.isLoggingOut);
export const authRefreshPromise = derived(authState, ($authState) => $authState.refreshPromise);
