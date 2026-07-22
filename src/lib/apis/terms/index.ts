import { WEBUI_BASE_URL, TERMS_VERSION } from '$lib/constants';

export const getTermsStatus = async (token: string) => {
	let error = null;
	const termsVersion = TERMS_VERSION?.trim() || '0.0.0';

	const res = await fetch(
		`${WEBUI_BASE_URL}/api/v1/terms/status/${encodeURIComponent(termsVersion)}`,
		{
			method: 'GET',
			headers: {
				Accept: 'application/json',
				'Content-Type': 'application/json',
				authorization: `Bearer ${token}`
			}
		}
	)
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = err;
			console.log(err);
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const acceptTerms = async (token: string) => {
	let error = null;
	const termsVersion = TERMS_VERSION?.trim() || '0.0.0';

	const res = await fetch(
		`${WEBUI_BASE_URL}/api/v1/terms/accept?terms_version=${encodeURIComponent(termsVersion)}`,
		{
			method: 'POST',
			headers: {
				Accept: 'application/json',
				'Content-Type': 'application/json',
				authorization: `Bearer ${token}`
			}
		}
	)
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = err;
			console.log(err);
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};
