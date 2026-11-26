const routes: Record<string, string> = {
	'/workspace': '/espace-travail',
	'/workspace/models': '/espace-travail/modeles',
	'/workspace/models/create': '/espace-travail/modeles/creer',
	'/workspace/models/edit': '/espace-travail/modeles/modifier',
	'/workspace/knowledge': '/espace-travail/connaissances',
	'/workspace/knowledge/create': '/espace-travail/connaissances/creer',
	'/workspace/prompts': '/espace-travail/invites',
	'/workspace/prompts/create': '/espace-travail/invites/creer',
	'/workspace/prompts/edit': '/espace-travail/invites/modifier',
	'/workspace/prompts/view': '/espace-travail/invites/voir',
	'/workspace/tools': '/espace-travail/outils',
	'/workspace/tools/create': '/espace-travail/outils/creer',
	'/workspace/tools/edit': '/espace-travail/outils/modifier',
	'/workspace/metrics': '/espace-travail/metriques',
	'/workspace/functions/create': '/espace-travail/fonctions/creer',
	'/admin': '/admin',
	'/admin/users': '/admin/utilisateurs',
	'/admin/evaluations': '/admin/evaluations',
	'/admin/functions': '/admin/fonctions',
	'/admin/functions/create': '/admin/fonctions/creer',
	'/admin/functions/edit': '/admin/fonctions/modifier',
	'/admin/settings': '/admin/parametres'
};

const frenchRoutes = new Map(Object.entries(routes).map(([english, french]) => [french, english]));

export const routeLanguage = (pathname: string): 'en-GB' | 'fr-CA' | null => {
	if (pathname.startsWith('/en/') && internalRoute(pathname)) return 'en-GB';
	if (pathname.startsWith('/fr/') && internalRoute(pathname)) return 'fr-CA';
	return null;
};

export function internalRoute(pathname: string): string | null {
	const trailingSlash = pathname.endsWith('/') && pathname !== '/' ? '/' : '';
	const path = trailingSlash ? pathname.slice(0, -1) : pathname;

	if (path.startsWith('/en/')) {
		const english = path.slice(3);
		if (english in routes || /^\/workspace\/knowledge\/[^/]+$/.test(english)) {
			return english + trailingSlash;
		}
	} else if (path.startsWith('/fr/')) {
		const french = path.slice(3);
		const english = frenchRoutes.get(french);
		if (english) return english + trailingSlash;
		const match = french.match(/^\/espace-travail\/connaissances\/id\/([^/]+)$/);
		if (match) return `/workspace/knowledge/${match[1]}${trailingSlash}`;
	} else if (path in routes || /^\/workspace\/knowledge\/[^/]+$/.test(path)) {
		return path + trailingSlash;
	}

	return null;
}

export function localizedRoute(pathAndSuffix: string, language: string | undefined): string {
	const match = pathAndSuffix.match(/^([^?#]*)(.*)$/);
	if (!match) return pathAndSuffix;

	const internal = internalRoute(match[1]);
	if (!internal) return pathAndSuffix;

	const trailingSlash = internal.endsWith('/') ? '/' : '';
	const path = trailingSlash ? internal.slice(0, -1) : internal;
	if (language === 'fr-CA') {
		const french =
			routes[path] ??
			path.replace(/^\/workspace\/knowledge\/([^/]+)$/, '/espace-travail/connaissances/id/$1');
		return `/fr${french}${trailingSlash}${match[2]}`;
	}
	return `/en${path}${trailingSlash}${match[2]}`;
}
