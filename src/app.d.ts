// See https://kit.svelte.dev/docs/types#app
// for information about these interfaces

declare module '@sveltejs/svelte-virtual-list' {
	import type { SvelteComponentTyped } from 'svelte';

	export default class VirtualList extends SvelteComponentTyped<
		{
			items?: unknown[];
			height?: number;
			itemHeight?: number;
			rowHeight?: number;
		},
		{},
		{ default: { item: unknown } }
	> {}
}

declare global {
	namespace App {
		// interface Error {}
		// interface Locals {}
		// interface PageData {}
		// interface Platform {}
	}
}
