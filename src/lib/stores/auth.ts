import { derived, writable } from 'svelte/store';

/**
 * Minimal auth lifecycle state.
 * Profile data stays in the shared user store; this store only tracks the
 * in-memory access token and refresh coordination.
 */
export type AuthState = {
	accessToken: string | null;
	accessTokenExpiresAt: number | null;
	bootstrapStatus: 'idle' | 'pending' | 'ready';
	isLoggingOut: boolean;
	// Set when auth state is cleared due to an auth failure; read by the session-expiry handler.
	lastAuthFailureMessage: string | null;
	// Shared in-flight refresh used to dedupe concurrent callers.
	refreshPromise: Promise<unknown> | null;
	// Shared in-flight bootstrap used to dedupe session restore callers.
	bootstrapPromise: Promise<unknown> | null;
};

export const initialAuthState: AuthState = {
	accessToken: null,
	accessTokenExpiresAt: null,
	bootstrapStatus: 'idle',
	isLoggingOut: false,
	lastAuthFailureMessage: null,
	refreshPromise: null,
	bootstrapPromise: null
};

export const authState = writable<AuthState>(initialAuthState);

export const accessToken = derived(authState, ($authState) => $authState.accessToken);
export const accessTokenExpiresAt = derived(
	authState,
	($authState) => $authState.accessTokenExpiresAt
);
export const authBootstrapStatus = derived(authState, ($authState) => $authState.bootstrapStatus);
export const authBootstrapReady = derived(
	authState,
	($authState) => $authState.bootstrapStatus === 'ready'
);
export const authLogoutInProgress = derived(authState, ($authState) => $authState.isLoggingOut);
