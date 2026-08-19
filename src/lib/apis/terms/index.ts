import { apiJson } from '$lib/apis/client';
import { TERMS_VERSION, WEBUI_API_BASE_URL } from '$lib/constants';

export type TermsStatus = {
	id: string;
	user_id: string;
	accepted_at: number;
	version: string;
};

const termsVersion = () => TERMS_VERSION?.trim() || '0.0.0';
const termsApiUrl = (path: string) => `${WEBUI_API_BASE_URL}/terms${path}`;

export const getTermsStatus = async (token: string = '') => {
	return apiJson<TermsStatus | null>(termsApiUrl(`/status/${encodeURIComponent(termsVersion())}`), {
		method: 'GET',
		token
	});
};

export const acceptTerms = async (token: string = '') => {
	return apiJson<TermsStatus>(termsApiUrl('/accept'), {
		method: 'POST',
		token,
		body: JSON.stringify({ version: termsVersion() })
	});
};
