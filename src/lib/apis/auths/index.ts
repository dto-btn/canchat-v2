import { WEBUI_API_BASE_URL } from '$lib/constants';
import { apiJson, apiVoid, type ApiRequestOptions } from '$lib/apis/client';
import type { SessionUser } from '$lib/stores';

const authsUrl = (path: string = '') => `${WEBUI_API_BASE_URL}/auths${path}`;

export type AdminConfig = {
	SHOW_ADMIN_DETAILS: boolean;
	WEBUI_URL: string;
	ENABLE_SIGNUP: boolean;
	ENABLE_API_KEY: boolean;
	ENABLE_API_KEY_ENDPOINT_RESTRICTIONS: boolean;
	API_KEY_ALLOWED_ENDPOINTS: string;
	ENABLE_CHANNELS: boolean;
	DEFAULT_USER_ROLE: string;
	ACCESS_TOKEN_EXPIRES_IN: string;
	REFRESH_TOKEN_EXPIRES_IN: string;
	JWT_EXPIRES_IN?: string | null;
	ENABLE_COMMUNITY_SHARING: boolean;
	ENABLE_MESSAGE_RATING: boolean;
};

export type LdapServerConfig = {
	label: string;
	host: string;
	port?: number | string | null;
	attribute_for_mail: string;
	attribute_for_username: string;
	app_dn: string;
	app_dn_password: string;
	search_base: string;
	search_filters: string;
	use_tls: boolean;
	certificate_path?: string | null;
	ciphers?: string | null;
};

export type LdapConfig = {
	ENABLE_LDAP: boolean | null;
};

const authJson = <T = unknown>(path: string, options: ApiRequestOptions = {}) =>
	apiJson<T>(authsUrl(path), options);

const authJsonBody = <T = unknown>(path: string, body: unknown, options: ApiRequestOptions = {}) =>
	authJson<T>(path, { ...options, body: JSON.stringify(body) });

// Cookie-backed auth endpoints still rely on the browser session cookie.
const authSessionJson = <T = unknown>(path: string, options: ApiRequestOptions = {}) =>
	authJson<T>(path, { credentials: 'include', ...options });

const authSessionJsonBody = <T = unknown>(
	path: string,
	body: unknown,
	options: ApiRequestOptions = {}
) => authSessionJson<T>(path, { ...options, body: JSON.stringify(body) });

const authSessionVoid = (path: string, options: ApiRequestOptions = {}) =>
	apiVoid(authsUrl(path), { credentials: 'include', ...options });

const readApiKey = async (method: 'GET' | 'POST', token: string) => {
	const response = await authJson<{ api_key?: string }>('/api_key', { method, token });
	return response?.api_key;
};

export const getAdminDetails = async (token: string) =>
	authJson('/admin/details', { method: 'GET', token });

export const getAdminConfig = async (token: string) =>
	authJson<AdminConfig>('/admin/config', { method: 'GET', token });

export const updateAdminConfig = async (token: string, body: AdminConfig) =>
	authJsonBody<AdminConfig>('/admin/config', body, { method: 'POST', token });

export const getSessionUser = async (token: string = ''): Promise<SessionUser | null> =>
	authSessionJson<SessionUser>('/', { method: 'GET', token });

export const ldapUserSignIn = async (user: string, password: string): Promise<SessionUser | null> =>
	authSessionJsonBody<SessionUser>(
		'/ldap',
		{ user, password },
		{
			method: 'POST',
			includeAuth: false,
			retryOnUnauthorized: false
		}
	);

export const getLdapConfig = async (token: string = '') =>
	authJson<LdapConfig>('/admin/config/ldap', { method: 'GET', token });

export const updateLdapConfig = async (token: string = '', enable_ldap: boolean) =>
	authJsonBody('/admin/config/ldap', { enable_ldap }, { method: 'POST', token });

export const getLdapServer = async (token: string = '') =>
	authJson<LdapServerConfig>('/admin/config/ldap/server', { method: 'GET', token });

export const updateLdapServer = async (token: string = '', body: LdapServerConfig) =>
	authSessionJsonBody<LdapServerConfig>('/admin/config/ldap/server', body, {
		method: 'POST',
		token
	});

export const accessTokenRefresh = async (token: string = ''): Promise<SessionUser | null> =>
	authSessionJson<SessionUser>('/refresh', {
		method: 'POST',
		token,
		retryOnUnauthorized: false
	});

export const userSignIn = async (email: string, password: string): Promise<SessionUser | null> =>
	authSessionJsonBody<SessionUser>(
		'/signin',
		{ email, password },
		{
			method: 'POST',
			includeAuth: false,
			retryOnUnauthorized: false
		}
	);

export const userSignUp = async (
	name: string,
	email: string,
	password: string,
	profile_image_url: string
): Promise<SessionUser | null> =>
	authSessionJsonBody<SessionUser>(
		'/signup',
		{ name, email, password, profile_image_url },
		{
			method: 'POST',
			includeAuth: false,
			retryOnUnauthorized: false
		}
	);

export const userSignOut = async () => {
	await authSessionVoid('/signout', {
		method: 'GET',
		includeAuth: false,
		retryOnUnauthorized: false
	});
};

export const addUser = async (
	token: string,
	name: string,
	email: string,
	password: string,
	role: string = 'pending'
) => authJsonBody('/add', { name, email, password, role }, { method: 'POST', token });

export const updateUserProfile = async (token: string, name: string, profileImageUrl: string) =>
	authJsonBody(
		'/update/profile',
		{ name, profile_image_url: profileImageUrl },
		{ method: 'POST', token }
	);

export const updateUserPassword = async (token: string, password: string, newPassword: string) =>
	authJsonBody(
		'/update/password',
		{ password, new_password: newPassword },
		{ method: 'POST', token }
	);

export const getSignUpEnabledStatus = async (token: string) =>
	authJson('/signup/enabled', { method: 'GET', token });

export const getDefaultUserRole = async (token: string) =>
	authJson('/signup/user/role', { method: 'GET', token });

export const updateDefaultUserRole = async (token: string, role: string) =>
	authJsonBody('/signup/user/role', { role }, { method: 'POST', token });

export const toggleSignUpEnabledStatus = async (token: string) =>
	authJson('/signup/enabled/toggle', { method: 'GET', token });

export const getJWTExpiresDuration = async (token: string) =>
	authJson('/token/expires', { method: 'GET', token });

export const updateJWTExpiresDuration = async (token: string, duration: string) =>
	authJsonBody('/token/expires/update', { duration }, { method: 'POST', token });

export const createAPIKey = async (token: string) => readApiKey('POST', token);

export const getAPIKey = async (token: string) => readApiKey('GET', token);

export const deleteAPIKey = async (token: string) =>
	authJson('/api_key', { method: 'DELETE', token });
