import { MCP_API_BASE_URL } from '$lib/constants';
import i18next from 'i18next';

const getFetchErrorMessage = (err: any) =>
	err?.message === 'Failed to fetch' ? i18next.t('Failed to fetch') : err?.message;

export const verifyMCPConnection = async (
	token: string = '',
	url: string = '',
	key: string = ''
) => {
	let error = null;

	const res = await fetch(`${MCP_API_BASE_URL}/verify`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			Authorization: `Bearer ${token}`,
			'Content-Type': 'application/json'
		},
		body: JSON.stringify({
			url,
			key
		})
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = `MCP: ${err?.detail ?? err?.error?.message ?? getFetchErrorMessage(err) ?? i18next.t('MCP connection check failed. Please try again.')}`;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const getMCPConfig = async (token: string = '') => {
	let error = null;

	const res = await fetch(`${MCP_API_BASE_URL}/config`, {
		method: 'GET',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			...(token && { authorization: `Bearer ${token}` })
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = `MCP: ${err?.detail ?? err?.error?.message ?? getFetchErrorMessage(err) ?? i18next.t('Unable to load MCP configuration. Please try again.')}`;
			return [];
		});

	if (error) {
		throw error;
	}

	return res;
};

export const updateMCPConfig = async (token: string = '', config: object) => {
	let error = null;

	const res = await fetch(`${MCP_API_BASE_URL}/config/update`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			Authorization: `Bearer ${token}`,
			'Content-Type': 'application/json'
		},
		body: JSON.stringify(config)
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = `MCP: ${err?.detail ?? err?.error?.message ?? getFetchErrorMessage(err) ?? i18next.t('Unable to update MCP configuration. Please try again.')}`;
			return [];
		});

	if (error) {
		throw error;
	}

	return res;
};

export const getMCPURLs = async (token: string = '') => {
	let error = null;

	const res = await fetch(`${MCP_API_BASE_URL}/urls`, {
		method: 'GET',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			...(token && { authorization: `Bearer ${token}` })
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = `MCP: ${err?.detail ?? err?.error?.message ?? getFetchErrorMessage(err) ?? i18next.t('Unable to load MCP server URLs. Please try again.')}`;
			return [];
		});

	if (error) {
		throw error;
	}

	return res;
};

export const updateMCPURLs = async (token: string = '', urls: string[]) => {
	let error = null;

	const res = await fetch(`${MCP_API_BASE_URL}/urls/update`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			Authorization: `Bearer ${token}`,
			'Content-Type': 'application/json'
		},
		body: JSON.stringify({
			urls
		})
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = `MCP: ${err?.detail ?? err?.error?.message ?? getFetchErrorMessage(err) ?? i18next.t('Unable to update MCP server URLs. Please try again.')}`;
			return [];
		});

	if (error) {
		throw error;
	}

	return res;
};

export const getMCPTools = async (token: string = '') => {
	let error = null;

	const res = await fetch(`${MCP_API_BASE_URL}/tools`, {
		method: 'GET',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			...(token && { authorization: `Bearer ${token}` })
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = `MCP: ${err?.detail ?? err?.error?.message ?? getFetchErrorMessage(err) ?? i18next.t('Unable to load MCP tools. Please try again.')}`;
			return [];
		});

	if (error) {
		throw error;
	}

	return res?.tools || res || [];
};

export const callMCPTool = async (
	token: string = '',
	tool_name: string,
	parameters: object = {}
) => {
	let error = null;

	const res = await fetch(`${MCP_API_BASE_URL}/tools/call`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			Authorization: `Bearer ${token}`,
			'Content-Type': 'application/json'
		},
		body: JSON.stringify({
			tool_name,
			parameters
		})
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = `MCP: ${err?.detail ?? err?.error?.message ?? getFetchErrorMessage(err) ?? i18next.t('MCP tool call failed. Please try again.')}`;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const getBuiltinServers = async (token: string = '') => {
	let error = null;

	const res = await fetch(`${MCP_API_BASE_URL}/servers/builtin`, {
		method: 'GET',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			...(token && { authorization: `Bearer ${token}` })
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = `MCP: ${err?.detail ?? err?.error?.message ?? getFetchErrorMessage(err) ?? i18next.t('Unable to load built-in MCP servers. Please try again.')}`;
			return { servers: [] };
		});

	if (error) {
		throw error;
	}

	return res;
};

export const restartBuiltinServer = async (token: string = '', serverName: string = '') => {
	let error = null;

	const res = await fetch(`${MCP_API_BASE_URL}/servers/builtin/${serverName}/restart`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			Authorization: `Bearer ${token}`,
			'Content-Type': 'application/json'
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = `MCP: ${err?.detail ?? err?.error?.message ?? getFetchErrorMessage(err) ?? i18next.t('Unable to restart built-in MCP server. Please try again.')}`;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

// External Server Management API Functions

export const getExternalServers = async (token: string = '') => {
	let error = null;

	const res = await fetch(`${MCP_API_BASE_URL}/servers/external`, {
		method: 'GET',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			...(token && { authorization: `Bearer ${token}` })
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = `MCP: ${err?.detail ?? err?.error?.message ?? getFetchErrorMessage(err) ?? i18next.t('Unable to load external MCP servers. Please try again.')}`;
			return { servers: [] };
		});

	if (error) {
		throw error;
	}

	return res;
};

export const createExternalServer = async (token: string = '', serverData: object) => {
	let error = null;

	const res = await fetch(`${MCP_API_BASE_URL}/servers/external`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			Authorization: `Bearer ${token}`,
			'Content-Type': 'application/json'
		},
		body: JSON.stringify(serverData)
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = `MCP: ${err?.detail ?? err?.error?.message ?? getFetchErrorMessage(err) ?? i18next.t('Unable to create external MCP server. Please try again.')}`;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const getExternalServer = async (token: string = '', serverId: string = '') => {
	let error = null;

	const res = await fetch(`${MCP_API_BASE_URL}/servers/external/${serverId}`, {
		method: 'GET',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			...(token && { authorization: `Bearer ${token}` })
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = `MCP: ${err?.detail ?? err?.error?.message ?? getFetchErrorMessage(err) ?? i18next.t('Unable to load external MCP server. Please try again.')}`;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const updateExternalServer = async (
	token: string = '',
	serverId: string = '',
	serverData: object
) => {
	let error = null;

	const res = await fetch(`${MCP_API_BASE_URL}/servers/external/${serverId}`, {
		method: 'PUT',
		headers: {
			Accept: 'application/json',
			Authorization: `Bearer ${token}`,
			'Content-Type': 'application/json'
		},
		body: JSON.stringify(serverData)
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = `MCP: ${err?.detail ?? err?.error?.message ?? getFetchErrorMessage(err) ?? i18next.t('Unable to update external MCP server. Please try again.')}`;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const deleteExternalServer = async (token: string = '', serverId: string = '') => {
	let error = null;

	const res = await fetch(`${MCP_API_BASE_URL}/servers/external/${serverId}`, {
		method: 'DELETE',
		headers: {
			Accept: 'application/json',
			Authorization: `Bearer ${token}`,
			'Content-Type': 'application/json'
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = `MCP: ${err?.detail ?? err?.error?.message ?? getFetchErrorMessage(err) ?? i18next.t('Unable to delete external MCP server. Please try again.')}`;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const startExternalServer = async (token: string = '', serverId: string = '') => {
	let error = null;

	const res = await fetch(`${MCP_API_BASE_URL}/servers/external/${serverId}/start`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			Authorization: `Bearer ${token}`,
			'Content-Type': 'application/json'
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = `MCP: ${err?.detail ?? err?.error?.message ?? getFetchErrorMessage(err) ?? i18next.t('Unable to start external MCP server. Please try again.')}`;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const stopExternalServer = async (token: string = '', serverId: string = '') => {
	let error = null;

	const res = await fetch(`${MCP_API_BASE_URL}/servers/external/${serverId}/stop`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			Authorization: `Bearer ${token}`,
			'Content-Type': 'application/json'
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = `MCP: ${err?.detail ?? err?.error?.message ?? getFetchErrorMessage(err) ?? i18next.t('Unable to stop external MCP server. Please try again.')}`;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const restartExternalServer = async (token: string = '', serverId: string = '') => {
	let error = null;

	const res = await fetch(`${MCP_API_BASE_URL}/servers/external/${serverId}/restart`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			Authorization: `Bearer ${token}`,
			'Content-Type': 'application/json'
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = `MCP: ${err?.detail ?? err?.error?.message ?? getFetchErrorMessage(err) ?? i18next.t('Unable to restart external MCP server. Please try again.')}`;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};
