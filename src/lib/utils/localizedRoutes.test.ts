import { describe, expect, it } from 'vitest';
import { reroute } from '../../hooks';
import { internalRoute, localizedRoute, routeLanguage } from './localizedRoutes';

describe('localized routes', () => {
	it.each([
		['/terms', '/fr/conditions'],
		['/workspace', '/fr/espace-travail'],
		['/workspace/models', '/fr/espace-travail/modeles'],
		['/workspace/models/create', '/fr/espace-travail/modeles/creer'],
		['/workspace/models/edit', '/fr/espace-travail/modeles/modifier'],
		['/workspace/knowledge', '/fr/espace-travail/connaissances'],
		['/workspace/knowledge/create', '/fr/espace-travail/connaissances/creer'],
		['/workspace/prompts', '/fr/espace-travail/invites'],
		['/workspace/prompts/create', '/fr/espace-travail/invites/creer'],
		['/workspace/prompts/edit', '/fr/espace-travail/invites/modifier'],
		['/workspace/prompts/view', '/fr/espace-travail/invites/voir'],
		['/workspace/tools', '/fr/espace-travail/outils'],
		['/workspace/tools/create', '/fr/espace-travail/outils/creer'],
		['/workspace/tools/edit', '/fr/espace-travail/outils/modifier'],
		['/workspace/metrics', '/fr/espace-travail/metriques'],
		['/workspace/functions/create', '/fr/espace-travail/fonctions/creer'],
		['/admin', '/fr/admin'],
		['/admin/users', '/fr/admin/utilisateurs'],
		['/admin/evaluations', '/fr/admin/evaluations'],
		['/admin/functions', '/fr/admin/fonctions'],
		['/admin/functions/create', '/fr/admin/fonctions/creer'],
		['/admin/functions/edit', '/fr/admin/fonctions/modifier'],
		['/admin/settings', '/fr/admin/parametres']
	])('round-trips %s', (english, french) => {
		expect(localizedRoute(english, 'fr-CA')).toBe(french);
		expect(internalRoute(french)).toBe(english);
		expect(localizedRoute(french, 'en-GB')).toBe(`/en${english}`);
		expect(internalRoute(`/en${english}`)).toBe(english);
	});

	it('preserves query strings, hashes, encoded IDs and trailing slashes', () => {
		const original = '/workspace/knowledge/creer/?id=a%2Fb#section';
		const french = '/fr/espace-travail/connaissances/id/creer/?id=a%2Fb#section';
		expect(localizedRoute(original, 'fr-CA')).toBe(french);
		expect(localizedRoute(french, 'en-GB')).toBe(`/en${original}`);
	});

	it('does not confuse a knowledge ID with the create route', () => {
		expect(internalRoute('/fr/espace-travail/connaissances/creer')).toBe(
			'/workspace/knowledge/create'
		);
		expect(internalRoute('/fr/espace-travail/connaissances/id/creer')).toBe(
			'/workspace/knowledge/creer'
		);
	});

	it('switches terms routes while preserving URL state', () => {
		expect(localizedRoute('/en/terms/?source=menu#section', 'fr-CA')).toBe(
			'/fr/conditions/?source=menu#section'
		);
		expect(localizedRoute('/fr/conditions?source=menu#section', 'en-GB')).toBe(
			'/en/terms?source=menu#section'
		);
	});

	it.each([
		['/en/terms', 'en-GB'],
		['/fr/conditions', 'fr-CA'],
		['/en/terms/', 'en-GB'],
		['/fr/conditions/', 'fr-CA']
	])('detects the locale of %s', (path, language) => {
		expect(routeLanguage(path)).toBe(language);
	});

	it('keeps localized terms pages as real routes while rerouting workspace pages', async () => {
		for (const path of ['/en/terms', '/fr/conditions', '/en/terms/', '/fr/conditions/']) {
			expect(await reroute({ url: new URL(path, 'https://example.test'), fetch })).toBe(path);
		}
		expect(
			await reroute({ url: new URL('/fr/espace-travail/modeles', 'https://example.test'), fetch })
		).toBe('/workspace/models');
	});

	it('leaves unrelated routes unchanged and recognizes only scoped language paths', () => {
		expect(localizedRoute('/auth?next=/admin', 'fr-CA')).toBe('/auth?next=/admin');
		expect(routeLanguage('/fr/admin/utilisateurs')).toBe('fr-CA');
		expect(routeLanguage('/en/workspace/models')).toBe('en-GB');
		expect(routeLanguage('/admin/users')).toBeNull();
	});
});
