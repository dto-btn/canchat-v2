import { WEBUI_API_BASE_URL } from '$lib/constants';
import { apiJson } from '$lib/apis/client';

const requestToolJson = <T>(path: string, token: string = '', options: RequestInit = {}) => {
	const headers = new Headers(options.headers);
	headers.set('Accept', 'application/json');

	return apiJson<T>(`${WEBUI_API_BASE_URL}${path}`, {
		...options,
		token,
		headers
	});
};

export const createNewTool = async (token: string, tool: object) => {
	return requestToolJson('/tools/create', token, {
		method: 'POST',
		body: JSON.stringify({
			...tool
		})
	});
};

export const getTools = async (token: string = '') => {
	return requestToolJson('/tools/', token, { method: 'GET' });
};

export const getToolList = async (token: string = '') => {
	return requestToolJson('/tools/list', token, { method: 'GET' });
};

export const exportTools = async (token: string = '') => {
	return requestToolJson('/tools/export', token, { method: 'GET' });
};

export const getToolById = async (token: string, id: string) => {
	return requestToolJson(`/tools/id/${id}`, token, { method: 'GET' });
};

export const updateToolById = async (token: string, id: string, tool: object) => {
	return requestToolJson(`/tools/id/${id}/update`, token, {
		method: 'POST',
		body: JSON.stringify({
			...tool
		})
	});
};

export const deleteToolById = async (token: string, id: string) => {
	return requestToolJson(`/tools/id/${id}/delete`, token, { method: 'DELETE' });
};

export const getToolValvesById = async (token: string, id: string) => {
	return requestToolJson(`/tools/id/${id}/valves`, token, { method: 'GET' });
};

export const getToolValvesSpecById = async (token: string, id: string) => {
	return requestToolJson(`/tools/id/${id}/valves/spec`, token, { method: 'GET' });
};

export const updateToolValvesById = async (token: string, id: string, valves: object) => {
	return requestToolJson(`/tools/id/${id}/valves/update`, token, {
		method: 'POST',
		body: JSON.stringify({
			...valves
		})
	});
};

export const getUserValvesById = async (token: string, id: string) => {
	return requestToolJson(`/tools/id/${id}/valves/user`, token, { method: 'GET' });
};

export const getUserValvesSpecById = async (token: string, id: string) => {
	return requestToolJson(`/tools/id/${id}/valves/user/spec`, token, { method: 'GET' });
};

export const updateUserValvesById = async (token: string, id: string, valves: object) => {
	return requestToolJson(`/tools/id/${id}/valves/user/update`, token, {
		method: 'POST',
		body: JSON.stringify({
			...valves
		})
	});
};
