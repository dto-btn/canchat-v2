import { WEBUI_API_BASE_URL } from '$lib/constants';
import { apiJson } from '$lib/apis/client';

export type Channel = {
	id: string;
	name: string;
	data?: object | null;
	meta?: object | null;
	access_control?: object | null;
	[key: string]: unknown;
};

type ChannelForm = {
	name: string;
	data?: object;
	meta?: object;
	access_control?: object;
};

export const createNewChannel = async (token: string = '', channel: ChannelForm) => {
	return apiJson(`${WEBUI_API_BASE_URL}/channels/create`, {
		method: 'POST',
		token,
		headers: {
			Accept: 'application/json'
		},
		body: JSON.stringify({ ...channel })
	});
};

export const getChannels = async (token: string = ''): Promise<Channel[]> => {
	return apiJson<Channel[]>(`${WEBUI_API_BASE_URL}/channels/`, {
		method: 'GET',
		token,
		headers: {
			Accept: 'application/json'
		}
	});
};

export const getChannelById = async (token: string = '', channel_id: string) => {
	return apiJson(`${WEBUI_API_BASE_URL}/channels/${channel_id}`, {
		method: 'GET',
		token,
		headers: {
			Accept: 'application/json'
		}
	});
};

export const updateChannelById = async (
	token: string = '',
	channel_id: string,
	channel: ChannelForm
) => {
	return apiJson(`${WEBUI_API_BASE_URL}/channels/${channel_id}/update`, {
		method: 'POST',
		token,
		headers: {
			Accept: 'application/json'
		},
		body: JSON.stringify({ ...channel })
	});
};

export const deleteChannelById = async (token: string = '', channel_id: string) => {
	return apiJson(`${WEBUI_API_BASE_URL}/channels/${channel_id}/delete`, {
		method: 'DELETE',
		token,
		headers: {
			Accept: 'application/json'
		}
	});
};

export const getChannelMessages = async (
	token: string = '',
	channel_id: string,
	skip: number = 0,
	limit: number = 50
) => {
	return apiJson(
		`${WEBUI_API_BASE_URL}/channels/${channel_id}/messages?skip=${skip}&limit=${limit}`,
		{
			method: 'GET',
			token,
			headers: {
				Accept: 'application/json'
			}
		}
	);
};

export const getChannelThreadMessages = async (
	token: string = '',
	channel_id: string,
	message_id: string,
	skip: number = 0,
	limit: number = 50
) => {
	return apiJson(
		`${WEBUI_API_BASE_URL}/channels/${channel_id}/messages/${message_id}/thread?skip=${skip}&limit=${limit}`,
		{
			method: 'GET',
			token,
			headers: {
				Accept: 'application/json'
			}
		}
	);
};

type MessageForm = {
	parent_id?: string;
	content: string;
	data?: object;
	meta?: object;
};

export const sendMessage = async (token: string = '', channel_id: string, message: MessageForm) => {
	return apiJson(`${WEBUI_API_BASE_URL}/channels/${channel_id}/messages/post`, {
		method: 'POST',
		token,
		headers: {
			Accept: 'application/json'
		},
		body: JSON.stringify({ ...message })
	});
};

export const updateMessage = async (
	token: string = '',
	channel_id: string,
	message_id: string,
	message: MessageForm
) => {
	return apiJson(
		`${WEBUI_API_BASE_URL}/channels/${channel_id}/messages/${message_id}/update`,
		{
			method: 'POST',
			token,
			headers: {
				Accept: 'application/json'
			},
			body: JSON.stringify({ ...message })
		}
	);
};

export const addReaction = async (
	token: string = '',
	channel_id: string,
	message_id: string,
	name: string
) => {
	return apiJson(
		`${WEBUI_API_BASE_URL}/channels/${channel_id}/messages/${message_id}/reactions/add`,
		{
			method: 'POST',
			token,
			headers: {
				Accept: 'application/json'
			},
			body: JSON.stringify({ name })
		}
	);
};

export const removeReaction = async (
	token: string = '',
	channel_id: string,
	message_id: string,
	name: string
) => {
	return apiJson(
		`${WEBUI_API_BASE_URL}/channels/${channel_id}/messages/${message_id}/reactions/remove`,
		{
			method: 'POST',
			token,
			headers: {
				Accept: 'application/json'
			},
			body: JSON.stringify({ name })
		}
	);
};

export const deleteMessage = async (token: string = '', channel_id: string, message_id: string) => {
	return apiJson(
		`${WEBUI_API_BASE_URL}/channels/${channel_id}/messages/${message_id}/delete`,
		{
			method: 'DELETE',
			token,
			headers: {
				Accept: 'application/json'
			}
		}
	);
};
