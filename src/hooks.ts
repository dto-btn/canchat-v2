import type { Reroute } from '@sveltejs/kit';
import { internalRoute } from '$lib/utils/localizedRoutes';

export const reroute: Reroute = ({ url }) => {
	const internal = internalRoute(url.pathname);
	// Terms have separate localized pages, not a shared internal page.
	return internal?.replace(/\/$/, '') === '/terms' ? url.pathname : (internal ?? url.pathname);
};
