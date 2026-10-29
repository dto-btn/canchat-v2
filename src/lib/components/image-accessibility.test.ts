import { createInstance } from 'i18next';
import { writable, type Writable } from 'svelte/store';
import { createServer, type ViteDevServer } from 'vite';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';

import en from '$lib/i18n/locales/en-GB/translation.json';
import fr from '$lib/i18n/locales/fr-CA/translation.json';
import type { Model } from '$lib/stores';

let server: ViteDevServer;

beforeAll(async () => {
	// Use the application's Vite pipeline to compile Svelte components for SSR.
	server = await createServer({
		server: { middlewareMode: true, watch: null, hmr: false }
	});
	const stores = await server.ssrLoadModule('/src/lib/stores/index.ts');
	const models: Writable<Model[]> = stores.models;
	models.set([
		{ id: 'alpha', name: 'Alpha', owned_by: 'openai', external: false },
		{ id: 'beta', name: 'Beta', owned_by: 'openai', external: false },
		{ id: 'unnamed', name: '', owned_by: 'openai', external: false }
	]);
}, 30_000);

afterAll(async () => {
	await server?.close();
});

type SsrComponent = {
	render: (
		props: Record<string, unknown>,
		options: { context: Map<string, unknown> }
	) => { html: string };
};

function isSsrComponent(component: unknown): component is SsrComponent {
	return (
		typeof component === 'object' &&
		component !== null &&
		'render' in component &&
		typeof component.render === 'function'
	);
}

async function render(
	path: string,
	locale: string,
	props: Record<string, unknown> = {}
): Promise<string> {
	const { default: component } = await server.ssrLoadModule(`/src/lib/components/${path}.svelte`);
	if (!isSsrComponent(component)) {
		throw new Error('Expected a server-rendered Svelte component');
	}

	const i18n = createInstance();
	await i18n.init({
		lng: locale,
		fallbackLng: 'en-GB',
		returnEmptyString: false,
		interpolation: { escapeValue: false },
		resources: {
			'en-GB': { translation: en },
			'fr-CA': { translation: fr }
		}
	});

	return component.render(props, { context: new Map([['i18n', writable(i18n)]]) }).html;
}

describe.each([
	{
		locale: 'en-GB',
		logo: 'CANChat logo',
		home: 'Home',
		chat: 'Chat',
		select: 'Select',
		profile: 'View profile for Alex'
	},
	{
		locale: 'fr-CA',
		logo: 'Logo de CANChat',
		home: 'Accueil',
		chat: 'Chat',
		select: 'Sélectionner',
		profile: 'Voir le profil : Alex'
	}
])('Image accessibility in $locale', ({ locale, logo, home, chat, select, profile }) => {
	it('names the standalone onboarding logo', async () => {
		const html = await render('OnBoarding', locale);
		expect(html).toContain(`alt="${logo}"`);
	});

	it('names image-only navigation buttons and keeps their images decorative', async () => {
		const html = await render('app/AppSidebar', locale);
		for (const label of [home, chat]) {
			const button = html.match(
				new RegExp(`<button\\b[^>]*aria-label="${label}"[^>]*>([\\s\\S]*?)</button>`)
			);
			expect(button, `Navigation button ${label}`).not.toBeNull();
			expect(button?.[1]).toMatch(/<img\b[^>]*alt=""/);
		}
	});

	it.each([undefined, '', '/user.png', 'data:image/png;base64,AA=='])(
		'keeps message avatars decorative for source %s',
		async (src) => {
			const html = await render(
				'chat/Messages/ProfileImage',
				locale,
				src === undefined ? {} : { src }
			);
			expect(html).toContain('alt=""');
			expect(html).not.toContain('alt="profile"');
		}
	);

	it('names the profile dropdown independently of its decorative avatar', async () => {
		const html = await render('channel/Messages/Message/ProfilePreview', locale, {
			user: { id: 'alex', name: 'Alex' }
		});
		expect(html).toContain(`aria-label="${profile}"`);
	}, 10_000);

	it('distinguishes image-only model buttons by their model names', async () => {
		const html = await render('chat/ChatPlaceholder', locale, { modelIds: ['alpha', 'beta'] });
		expect(html).toContain(`alt="${select} Alpha"`);
		expect(html).toContain(`alt="${select} Beta"`);
	});

	it('falls back to the model ID when the name is empty', async () => {
		const { user } = await server.ssrLoadModule('/src/lib/stores/index.ts');
		user.set({ id: 'alex', name: 'Alex', role: 'user' });
		try {
			const html = await render('chat/ChatPlaceholder', locale, {
				modelIds: ['unnamed']
			});
			expect(html).toContain(`alt="${select} unnamed"`);
		} finally {
			user.set(undefined);
		}
	}, 20_000);

	it.each(['layout/Navbar', 'channel/Navbar'])(
		'renders one user menu trigger in %s',
		async (path) => {
			const { user } = await server.ssrLoadModule('/src/lib/stores/index.ts');
			user.set({ id: 'alex', name: 'Alex', role: 'user', profile_image_url: '/user.png' });
			try {
				const html = await render(path, locale, { showModelSelector: false, selectedModels: [] });
				const label = locale === 'fr-CA' ? 'Menu utilisateur' : 'User Menu';
				const triggers = html.match(
					new RegExp(`<button\\b[^>]*aria-label="${label}"[^>]*>[\\s\\S]*?</button>`, 'g')
				);
				expect(triggers).toHaveLength(1);
				expect(triggers?.[0].match(/<button\b/g)).toHaveLength(1);
				expect(triggers?.[0]).toMatch(/<img\b[^>]*alt=""/);
			} finally {
				user.set(undefined);
			}
		},
		20_000
	);
});
