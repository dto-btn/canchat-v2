import { WEBUI_API_BASE_URL } from '$lib/constants';
import { apiJson, type ApiRequestOptions } from '$lib/apis/client';
import { getTimeRange } from '$lib/utils';

const chatApi = async <T = unknown>(path: string, options: ApiRequestOptions = {}) => {
	return apiJson<T>(`${WEBUI_API_BASE_URL}${path}`, {
		...options,
		headers: {
			Accept: 'application/json',
			...(options.headers ?? {})
		}
	});
};

const withTimeRange = (chat: any) => ({
	...chat,
	time_range: getTimeRange(chat.updated_at)
});

export const createNewChat = async (token: string, chat: object) => {
	return chatApi('/chats/new', {
		method: 'POST',
		token,
		body: JSON.stringify({
			chat: chat
		})
	});
};

export const importChat = async (
	token: string,
	chat: object,
	meta: object | null,
	pinned?: boolean,
	folderId?: string | null
) => {
	return chatApi('/chats/import', {
		method: 'POST',
		token,
		body: JSON.stringify({
			chat: chat,
			meta: meta ?? {},
			pinned: pinned,
			folder_id: folderId
		})
	});
};

export const getChatList = async (token: string = '', page: number | null = null): Promise<any> => {
	const searchParams = new URLSearchParams();

	if (page !== null) {
		searchParams.append('page', `${page}`);
	}

	const res = (await chatApi<any[]>(`/chats/?${searchParams.toString()}`, {
		method: 'GET',
		token
	})) ?? [];

	return res.map(withTimeRange);
};

export const getChatListByUserId = async (token: string = '', userId: string) => {
	const res = (await chatApi<any[]>(`/chats/list/user/${userId}`, {
		method: 'GET',
		token
	})) ?? [];

	return res.map(withTimeRange);
};

export const getArchivedChatList = async (token: string = '') => {
	return chatApi('/chats/archived', {
		method: 'GET',
		token
	});
};

export const getAllChats = async (token: string) => {
	return chatApi('/chats/all', {
		method: 'GET',
		token
	});
};

export const getChatListBySearchText = async (
	token: string,
	text: string,
	page: number = 1
): Promise<any> => {
	const normalizedText = text.replace(/étiquette:/gi, 'tag:');

	const searchParams = new URLSearchParams();
	searchParams.append('text', normalizedText);
	searchParams.append('page', `${page}`);

	const res = (await chatApi<any[]>(`/chats/search?${searchParams.toString()}`, {
		method: 'GET',
		token
	})) ?? [];

	return res.map(withTimeRange);
};

export const getChatsByFolderId = async (token: string, folderId: string) => {
	return chatApi(`/chats/folder/${folderId}`, {
		method: 'GET',
		token
	});
};

export const getAllArchivedChats = async (token: string) => {
	return chatApi('/chats/all/archived', {
		method: 'GET',
		token
	});
};

export const getAllUserChats = async (token: string) => {
	return chatApi('/chats/all/db', {
		method: 'GET',
		token
	});
};

export const getAllTags = async (token: string = ''): Promise<any> => {
	return chatApi('/chats/all/tags', {
		method: 'GET',
		token
	});
};

export const getPinnedChatList = async (token: string = ''): Promise<any> => {
	const res = (await chatApi<any[]>('/chats/pinned', {
		method: 'GET',
		token
	})) ?? [];

	return res.map(withTimeRange);
};

export const getChatListByTagName = async (token: string = '', tagName: string) => {
	const res = (await chatApi<any[]>('/chats/tags', {
		method: 'POST',
		token,
		body: JSON.stringify({
			name: tagName
		})
	})) ?? [];

	return res.map(withTimeRange);
};

export const getChatById = async (token: string, id: string) => {
	return chatApi(`/chats/${id}`, {
		method: 'GET',
		token
	});
};

export const getChatByShareId = async (token: string, share_id: string) => {
	return chatApi(`/chats/share/${share_id}`, {
		method: 'GET',
		token
	});
};

export const getChatPinnedStatusById = async (token: string, id: string) => {
	return chatApi(`/chats/${id}/pinned`, {
		method: 'GET',
		token
	});
};

export const toggleChatPinnedStatusById = async (token: string, id: string) => {
	return chatApi(`/chats/${id}/pin`, {
		method: 'POST',
		token
	});
};

export const cloneChatById = async (token: string, id: string, title?: string) => {
	return chatApi(`/chats/${id}/clone`, {
		method: 'POST',
		token,
		body: JSON.stringify({
			...(title && { title: title })
		})
	});
};

export const cloneSharedChatById = async (token: string, id: string) => {
	return chatApi(`/chats/${id}/clone/shared`, {
		method: 'POST',
		token
	});
};

export const shareChatById = async (token: string, id: string) => {
	return chatApi(`/chats/${id}/share`, {
		method: 'POST',
		token
	});
};

export const updateChatFolderIdById = async (token: string, id: string, folderId?: string) => {
	return chatApi(`/chats/${id}/folder`, {
		method: 'POST',
		token,
		body: JSON.stringify({
			folder_id: folderId
		})
	});
};

export const archiveChatById = async (token: string, id: string) => {
	return chatApi(`/chats/${id}/archive`, {
		method: 'POST',
		token
	});
};

export const deleteSharedChatById = async (token: string, id: string) => {
	return chatApi(`/chats/${id}/share`, {
		method: 'DELETE',
		token
	});
};

export const updateChatById = async (token: string, id: string, chat: object) => {
	return chatApi(`/chats/${id}`, {
		method: 'POST',
		token,
		body: JSON.stringify({
			chat: chat
		})
	});
};

export const deleteChatById = async (token: string, id: string) => {
	return chatApi(`/chats/${id}`, {
		method: 'DELETE',
		token
	});
};

export const deleteMultipleChats = async (token: string, chatIds: string[]) => {
	return chatApi('/chats/bulk', {
		method: 'DELETE',
		token,
		body: JSON.stringify({
			chat_ids: chatIds
		})
	});
};

export const getTagsById = async (token: string, id: string) => {
	return chatApi(`/chats/${id}/tags`, {
		method: 'GET',
		token
	});
};

export const addTagById = async (token: string, id: string, tagName: string) => {
	return chatApi(`/chats/${id}/tags`, {
		method: 'POST',
		token,
		body: JSON.stringify({
			name: tagName
		})
	});
};

export const deleteTagById = async (token: string, id: string, tagName: string) => {
	return chatApi(`/chats/${id}/tags`, {
		method: 'DELETE',
		token,
		body: JSON.stringify({
			name: tagName
		})
	});
};

export const deleteTagsById = async (token: string, id: string) => {
	return chatApi(`/chats/${id}/tags/all`, {
		method: 'DELETE',
		token
	});
};

export const deleteAllChats = async (token: string) => {
	return chatApi('/chats/', {
		method: 'DELETE',
		token
	});
};

export const archiveAllChats = async (token: string) => {
	return chatApi('/chats/archive/all', {
		method: 'POST',
		token
	});
};
