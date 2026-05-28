import { WEBUI_API_BASE_URL } from '$lib/constants';
import { apiJson } from '$lib/apis/client';
import { getUserPosition } from '$lib/utils';

type UserSettings = Record<string, unknown> & {
	timezone?: string | null;
};

export const getUserGroups = async (token: string) => {
	return apiJson(`${WEBUI_API_BASE_URL}/users/groups`, {
		method: 'GET',
		token
	});
};

export const getUserDefaultPermissions = async (token: string) => {
	return apiJson(`${WEBUI_API_BASE_URL}/users/default/permissions`, {
		method: 'GET',
		token
	});
};

export const updateUserDefaultPermissions = async (token: string, permissions: object) => {
	return apiJson(`${WEBUI_API_BASE_URL}/users/default/permissions`, {
		method: 'POST',
		token,
		body: JSON.stringify({
			...permissions
		})
	});
};

export const updateUserRole = async (token: string, id: string, role: string) => {
	return apiJson(`${WEBUI_API_BASE_URL}/users/update/role`, {
		method: 'POST',
		token,
		body: JSON.stringify({
			id: id,
			role: role
		})
	});
};

export const getUsers = async (token: string) => {
	return (
		(await apiJson(`${WEBUI_API_BASE_URL}/users/`, {
		method: 'GET',
			token
		})) ?? []
	);
};

export const getUserSettings = async (token: string = ''): Promise<UserSettings | null> => {
	return apiJson<UserSettings>(`${WEBUI_API_BASE_URL}/users/user/settings`, {
		method: 'GET',
		token
	});
};

export const updateUserSettings = async (token: string, settings: object) => {
	return apiJson(`${WEBUI_API_BASE_URL}/users/user/settings/update`, {
		method: 'POST',
		token,
		body: JSON.stringify({
			...settings
		})
	});
};

// Timezone-specific helper functions
export const getUserTimezoneFromSettings = async (token: string): Promise<string | null> => {
	try {
		const settings = await getUserSettings(token);
		return settings?.timezone || null;
	} catch (error) {
		console.error('Failed to get user timezone from settings:', error);
		return null;
	}
};

export const updateUserTimezone = async (token: string, timezone: string) => {
	try {
		const currentSettings = await getUserSettings(token);
		const updatedSettings = {
			...currentSettings,
			timezone: timezone
		};
		return await updateUserSettings(token, updatedSettings);
	} catch (error) {
		console.error('Failed to update user timezone:', error);
		throw error;
	}
};

export const detectAndUpdateUserTimezone = async (token: string) => {
	try {
		// Get current user's timezone from browser
		const detectedTimezone = Intl.DateTimeFormat().resolvedOptions().timeZone;

		// Get stored timezone preference
		const storedTimezone = await getUserTimezoneFromSettings(token);

		// Update only if timezone has changed or is not set
		if (!storedTimezone || storedTimezone !== detectedTimezone) {
			await updateUserTimezone(token, detectedTimezone);
			console.log(`Updated user timezone to: ${detectedTimezone}`);
		}

		return detectedTimezone;
	} catch (error) {
		console.error('Failed to detect and update user timezone:', error);
		// Return Toronto timezone as default for Canadian users
		return 'America/Toronto';
	}
};

export const getUserById = async (token: string, userId: string) => {
	return apiJson(`${WEBUI_API_BASE_URL}/users/${userId}`, {
		method: 'GET',
		token
	});
};

export const getUserInfo = async (token: string) => {
	return apiJson(`${WEBUI_API_BASE_URL}/users/user/info`, {
		method: 'GET',
		token
	});
};

export const getUserRole = async (token: string) => {
	return apiJson(`${WEBUI_API_BASE_URL}/users/user/role`, {
		method: 'GET',
		token
	});
};

export const updateUserInfo = async (token: string, info: object) => {
	return apiJson(`${WEBUI_API_BASE_URL}/users/user/info/update`, {
		method: 'POST',
		token,
		body: JSON.stringify({
			...info
		})
	});
};

export const getAndUpdateUserLocation = async (token: string) => {
	const location = await getUserPosition().catch((err) => {
		throw err;
	});

	if (location) {
		await updateUserInfo(token, { location: location });
		return location;
	} else {
		throw new Error('Failed to get user location');
	}
};

export const deleteUserById = async (token: string, userId: string) => {
	return apiJson(`${WEBUI_API_BASE_URL}/users/${userId}`, {
		method: 'DELETE',
		token
	});
};

type UserUpdateForm = {
	profile_image_url: string;
	email: string;
	name: string;
	password: string;
};

export const updateUserById = async (token: string, userId: string, user: UserUpdateForm) => {
	return apiJson(`${WEBUI_API_BASE_URL}/users/${userId}/update`, {
		method: 'POST',
		token,
		body: JSON.stringify({
			profile_image_url: user.profile_image_url,
			email: user.email,
			name: user.name,
			password: user.password !== '' ? user.password : undefined
		})
	});
};

export const getUserByDomain = async (
	token: string,
	startDate: number,
	endDate: number,
	domain: string | undefined = undefined
) => {
	const domainParam = domain ? `&domain=${encodeURIComponent(domain)}` : '';
	const url = `${WEBUI_API_BASE_URL}/users/count-per-domain?start_timestamp=${startDate}&end_timestamp=${endDate}${domainParam}`;

	return apiJson(url, {
		method: 'GET',
		token
	});
};
