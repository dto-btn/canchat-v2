<script lang="ts">
	import { getI18n } from '$lib/utils/context';

	import {
		WEBUI_NAME,
		showControls,
		showSidebar,
		user,
		config,
		suggestionCycle,
		ariaMessage
	} from '$lib/stores';

	import ModelSelector from '$lib/components/chat/ModelSelector.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import NavbarExtras from '$lib/components/common/NavbarExtras.svelte';

	import MenuLines from '$lib/components/icons/MenuLines.svelte';
	import AdjustmentsHorizontal from '$lib/components/icons/AdjustmentsHorizontal.svelte';
	import PencilSquare from '$lib/components/icons/PencilSquare.svelte';

	const i18n = getI18n();

	export let initNewChat: Function;
	export let title: string = $WEBUI_NAME;
	export let shareEnabled: boolean = false;
	export let chat;
	export let selectedModels;
	export let showModelSelector = true;

	const changeFocus = async (elementId) => {
		setTimeout(() => {
			document.getElementById(elementId)?.focus();
		}, 110);
	};
</script>

<nav class="sticky top-0 z-30 w-full px-1.5 py-1.5 -mb-8 flex items-center drag-region">
	<div
		class="bg-gradient-to-b via-50% from-white via-white to-transparent dark:from-gray-900 dark:via-gray-900 dark:to-transparent pointer-events-none absolute inset-0 -bottom-7 z-[-1] blur"
	></div>

	<div class="flex max-w-full w-full mx-auto px-1 pt-0.5 bg-transparent items-center">
		<!-- Left Section: Sidebar Toggle and Model Selector -->
		<div class="flex items-center gap-1">
			<!-- Sidebar Toggle -->
			<div
				class="{$showSidebar
					? 'md:hidden'
					: ''} self-start flex flex-none items-center text-gray-600 dark:text-gray-400"
			>
				<Tooltip content={$i18n.t('Show Sidebar')}>
					<button
						id="sidebar-toggle-button"
						class="m-auto self-center cursor-pointer px-2 py-2 flex rounded-xl hover:bg-gray-50 dark:hover:bg-gray-850 transition"
						on:click={async () => {
							showSidebar.set(!$showSidebar);
							ariaMessage.set($i18n.t('Sidebar expanded.'));
							await changeFocus('hide-sidebar-button');
						}}
						aria-label="Show Sidebar"
					>
						<MenuLines />
					</button>
				</Tooltip>
			</div>

			<!-- Model Selector -->
			<div class="overflow-hidden max-w-full py-0.5 {$showSidebar ? 'ml-1' : ''}">
				{#if showModelSelector}
					<ModelSelector bind:selectedModels showSetDefault={!shareEnabled} />
				{/if}
			</div>
		</div>

		<!-- Center Section: PROTECTED B Tooltip -->
		{#if $config?.features?.pbmm_env === true}
			<div class="hidden sm:flex justify-center items-center flex-1">
				<div
					class="text-xs leading-tight font-bold text-gray-600 dark:text-gray-400 uppercase tracking-widest"
				>
					{$i18n.t('PROTECTED B')}
				</div>
			</div>
		{/if}

		<!-- Right Section: New Chat, Controls, and Navbar Extras -->
		<div class="flex items-center gap-1 ml-auto">
			<!-- New Chat Button -->
			{#if !$showSidebar}
				<div class="self-start flex flex-none items-center text-gray-600 dark:text-gray-400">
					<Tooltip content={$i18n.t('New Chat')}>
						<button
							id="new-chat-button"
							class="flex m-auto self-center cursor-pointer p-2 rounded-xl text-gray-900 hover:bg-gray-50 dark:text-white dark:hover:bg-gray-850 transition"
							on:click={() => initNewChat()}
							aria-label={$i18n.t('New Chat')}
						>
							<PencilSquare className="size-5" strokeWidth="2" />
						</button>
					</Tooltip>
				</div>
			{/if}

			<!-- Controls Button -->
			{#if $user?.permissions?.chat?.controls}
				<div class="self-start flex flex-none items-center text-gray-600 dark:text-gray-400">
					<Tooltip content={$i18n.t('Controls')}>
						<button
							class="flex cursor-pointer p-2 rounded-xl text-gray-900 hover:bg-gray-50 dark:text-white dark:hover:bg-gray-850 transition"
							on:click={async () => {
								await showControls.set(!$showControls);
							}}
							aria-label="Controls"
						>
							<AdjustmentsHorizontal className="size-5" strokeWidth="0.5" />
						</button>
					</Tooltip>
				</div>
			{/if}

			<!-- Navbar Extras -->
			<div class="self-start flex flex-none items-center text-gray-600 dark:text-gray-400">
				<NavbarExtras />
			</div>
		</div>
	</div>
</nav>
