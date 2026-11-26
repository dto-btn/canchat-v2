import type { Reroute } from '@sveltejs/kit';
import { internalRoute } from '$lib/utils/localizedRoutes';

export const reroute: Reroute = ({ url }) => internalRoute(url.pathname) ?? url.pathname;
