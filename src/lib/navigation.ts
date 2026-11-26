import { goto as kitGoto } from '$app/navigation';
import { get } from 'svelte/store';
import i18n from '$lib/i18n';
import { localizedRoute } from '$lib/utils/localizedRoutes';

export const goto: typeof kitGoto = (url, options) => {
	if (typeof url === 'string') {
		return kitGoto(localizedRoute(url, get(i18n).language), options);
	}
	return kitGoto(url, options);
};
