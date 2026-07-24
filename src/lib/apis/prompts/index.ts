import { WEBUI_API_BASE_URL } from '$lib/constants';
import { apiJson } from '$lib/apis/client';

const requestPromptJson = <T>(path: string, token: string = '', options: RequestInit = {}) => {
	const headers = new Headers(options.headers);
	headers.set('Accept', 'application/json');

	return apiJson<T>(`${WEBUI_API_BASE_URL}${path}`, {
		...options,
		token,
		headers
	});
};

type PromptItem = {
	command: string;
	title: string;
	content: string;
	access_control?: null | object;
};

export const createNewPrompt = async (token: string, prompt: PromptItem) => {
	return requestPromptJson('/prompts/create', token, {
		method: 'POST',
		body: JSON.stringify({
			...prompt,
			command: `/${prompt.command}`
		})
	});
};

export const getPrompts = async (
	token: string = '',
	options: { page?: number; limit?: number; search?: string } = {}
) => {
	const { page = 1, limit = 20, search } = options;
	const params = new URLSearchParams({
		page: page.toString(),
		limit: limit.toString()
	});

	if (search && search.trim()) {
		params.append('search', search.trim());
	}

	return requestPromptJson(`/prompts/paginated?${params}`, token, { method: 'GET' });
};

export const getPromptList = async (
	token: string = '',
	options: { page?: number; limit?: number; search?: string } = {}
) => {
	const { page = 1, limit = 20, search } = options;
	const params = new URLSearchParams({
		page: page.toString(),
		limit: limit.toString()
	});

	if (search && search.trim()) {
		params.append('search', search.trim());
	}

	return requestPromptJson(`/prompts/list/paginated?${params}`, token, { method: 'GET' });
};

export const getPromptsCount = async (token: string = '', search?: string) => {
	const params = new URLSearchParams();
	if (search && search.trim()) {
		params.append('search', search.trim());
	}

	return requestPromptJson(`/prompts/count?${params}`, token, { method: 'GET' });
};

// Legacy functions for backward compatibility (now calling original endpoints)
export const getPromptsLegacy = async (token: string = '') => {
	return requestPromptJson('/prompts/', token, { method: 'GET' });
};

export const getPromptListLegacy = async (token: string = '') => {
	return requestPromptJson('/prompts/list', token, { method: 'GET' });
};

export const getPromptByCommand = async (token: string, command: string) => {
	// URL encode the command to properly handle special characters like question marks
	const encodedCommand = encodeURIComponent(command);

	return requestPromptJson(`/prompts/command/${encodedCommand}`, token, { method: 'GET' });
};

export const updatePromptByCommand = async (token: string, prompt: PromptItem) => {
	// URL encode the command to properly handle special characters like question marks
	const encodedCommand = encodeURIComponent(prompt.command);

	return requestPromptJson(`/prompts/command/${encodedCommand}/update`, token, {
		method: 'POST',
		body: JSON.stringify({
			...prompt,
			command: `/${prompt.command}`
		})
	});
};

export const deletePromptByCommand = async (token: string, command: string) => {
	command = command.charAt(0) === '/' ? command.slice(1) : command;

	// URL encode the command to properly handle special characters like question marks
	const encodedCommand = encodeURIComponent(command);

	return requestPromptJson(`/prompts/command/${encodedCommand}/delete`, token, {
		method: 'DELETE'
	});
};
