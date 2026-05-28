import { WEBUI_API_BASE_URL } from '$lib/constants';
import { apiBlob, apiJson } from '$lib/apis/client';

const triggerDownload = (blob: Blob, filename: string) => {
	const url = window.URL.createObjectURL(blob);
	const anchor = document.createElement('a');
	anchor.href = url;
	anchor.download = filename;
	document.body.appendChild(anchor);
	anchor.click();
	anchor.remove();
	window.URL.revokeObjectURL(url);
};

export const getGravatarUrl = async (email: string) => {
	return apiJson(`${WEBUI_API_BASE_URL}/utils/gravatar?email=${encodeURIComponent(email)}`, {
		method: 'GET',
		includeAuth: false,
		retryOnUnauthorized: false
	});
};

export const formatPythonCode = async (code: string) => {
	return apiJson(`${WEBUI_API_BASE_URL}/utils/code/format`, {
		method: 'POST',
		includeAuth: false,
		retryOnUnauthorized: false,
		body: JSON.stringify({
			code
		})
	});
};

export const downloadChatAsPDF = async (title: string, messages: object[]) => {
	return apiBlob(`${WEBUI_API_BASE_URL}/utils/pdf`, {
		method: 'POST',
		includeAuth: false,
		retryOnUnauthorized: false,
		body: JSON.stringify({
			title,
			messages
		})
	});
};

export const getHTMLFromMarkdown = async (md: string) => {
	const res = await apiJson<{ html: string }>(`${WEBUI_API_BASE_URL}/utils/markdown`, {
		method: 'POST',
		includeAuth: false,
		retryOnUnauthorized: false,
		body: JSON.stringify({
			md
		})
	});

	return res.html;
};

export const downloadDatabase = async (token: string) => {
	const blob = await apiBlob(`${WEBUI_API_BASE_URL}/utils/db/download`, {
		method: 'GET',
		token
	});

	triggerDownload(blob, 'webui.db');
};

export const downloadLiteLLMConfig = async (token: string) => {
	const blob = await apiBlob(`${WEBUI_API_BASE_URL}/utils/litellm/config`, {
		method: 'GET',
		token
	});

	triggerDownload(blob, 'config.yaml');
};
