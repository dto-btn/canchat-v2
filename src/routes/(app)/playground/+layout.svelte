<script lang="ts">
	import { onMount } from 'svelte';
	import { getI18n } from '$lib/utils/context';
	import { WEBUI_NAME, showSidebar, functions } from '$lib/stores';
	import NavbarExtras from '$lib/components/common/NavbarExtras.svelte';
	import MenuLines from '$lib/components/icons/MenuLines.svelte';
	import { page } from '$app/stores';

	const i18n = getI18n();

	onMount(async () => {});
</script>

<svelte:head>
	<title>
		{$i18n.t('Playground')} | {$WEBUI_NAME}
	</title>
</svelte:head>

<div
	class=" flex flex-col w-full h-screen max-h-[100dvh] transition-width duration-200 ease-in-out {$showSidebar
		? 'md:max-w-[calc(100%-260px)]'
		: ''} max-w-full"
>
	<nav class="px-2.5 pt-1 backdrop-blur-xl w-full drag-region">
		<div class="flex items-center justify-between">
			<!-- Left Section: Sidebar Toggle and Links -->
			<div class="flex items-center gap-1">
				<!-- Sidebar Toggle -->
				<div class="{$showSidebar ? 'md:hidden' : ''} flex flex-none items-center self-end">
					<button
						id="sidebar-toggle-button"
						class="cursor-pointer p-1.5 flex rounded-xl hover:bg-gray-100 dark:hover:bg-gray-850 transition"
						on:click={() => {
							showSidebar.set(!$showSidebar);
						}}
						aria-label="Toggle Sidebar"
					>
						<div class="m-auto self-center">
							<MenuLines />
						</div>
					</button>
				</div>

				<!-- Links -->
				<div
					class="flex gap-1 scrollbar-none overflow-x-auto w-fit text-center text-sm font-medium rounded-full bg-transparent pt-1"
				>
					<a
						class="min-w-fit rounded-full p-1.5 {['/playground', '/playground/'].includes(
							$page.url.pathname
						)
							? ''
							: 'text-gray-300 dark:text-gray-600 hover:text-gray-700 dark:hover:text-white'} transition"
						href="/playground">{$i18n.t('Chat')}</a
					>

					<!-- <a
						class="min-w-fit rounded-full p-1.5 {$page.url.pathname.includes('/playground/notes')
							? ''
							: 'text-gray-300 dark:text-gray-600 hover:text-gray-700 dark:hover:text-white'} transition"
						href="/playground/notes">{$i18n.t('Notes')}</a
					> -->

					<a
						class="min-w-fit rounded-full p-1.5 {$page.url.pathname.includes(
							'/playground/completions'
						)
							? ''
							: 'text-gray-300 dark:text-gray-600 hover:text-gray-700 dark:hover:text-white'} transition"
						href="/playground/completions">{$i18n.t('Completions')}</a
					>
				</div>
			</div>

			<!-- Right Section: Navbar Extras -->
			<NavbarExtras />
		</div>
	</nav>

	<div class="flex-1 max-h-full overflow-y-auto">
		<slot />
	</div>
</div>
