<script lang="ts">
	import ChevronDown from '$lib/components/icons/ChevronDown.svelte';
	import ChevronUp from '$lib/components/icons/ChevronUp.svelte';
	import Collapsible from '$lib/components/common/Collapsible.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Clipboard from '$lib/components/icons/Clipboard.svelte';
	import { copyToClipboard } from '$lib/utils';
	import { getI18n } from '$lib/utils/context';

	const i18n = getI18n();

	export let status = { urls: [], query: '' };
	let state = false;
	$: copyTooltipContent = $i18n.t('Copy to clipboard');

	const copyQueryToClipboard = async () => {
		if (!status?.query) {
			return;
		}

		await copyToClipboard(status.query);
		copyTooltipContent = $i18n.t('Copied to clipboard');
		setTimeout(() => {
			copyTooltipContent = $i18n.t('Copy to clipboard');
		}, 2000);
	};
</script>

<Collapsible bind:open={state} className="w-full space-y-1">
	<div
		class="flex items-start gap-2 text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 transition"
	>
		<div class="min-w-0 flex-1">
			<slot />
		</div>

		<div class="mt-0.5 shrink-0">
			{#if state}
				<ChevronUp strokeWidth="3.5" className="size-3.5 " />
			{:else}
				<ChevronDown strokeWidth="3.5" className="size-3.5 " />
			{/if}
		</div>
	</div>
	<div
		class="text-sm border border-gray-300/30 dark:border-gray-700/50 rounded-xl mb-1.5"
		slot="content"
	>
		{#if status?.query}
			<span
				class="flex w-full items-center p-3 px-4 border-b border-gray-300/30 dark:border-gray-700/50 group/item justify-between font-normal text-gray-800 dark:text-gray-300 no-underline"
			>
				<div class="min-w-0 flex-1 line-clamp-1">
					{status.query}
				</div>

				<Tooltip content={copyTooltipContent} tippyOptions={{ hideOnClick: false }}>
					<button
						type="button"
						class="ml-3 shrink-0 rounded-lg p-1.5 text-gray-500 hover:bg-gray-100 hover:text-gray-700 dark:text-gray-400 dark:hover:bg-gray-800 dark:hover:text-gray-200 transition"
						aria-label={copyTooltipContent}
						on:click|stopPropagation={copyQueryToClipboard}
					>
						<Clipboard className="size-4" strokeWidth="1.5" />
					</button>
				</Tooltip>
			</span>
		{/if}

		{#each status.urls as url, urlIdx}
			<a
				href={url}
				target="_blank"
				class="flex w-full items-center p-3 px-4 {urlIdx === status.urls.length - 1
					? ''
					: 'border-b border-gray-300/30 dark:border-gray-700/50'} group/item justify-between font-normal text-gray-800 dark:text-gray-300"
			>
				<div class=" line-clamp-1">
					{url}
				</div>

				<div
					class=" ml-1 text-white dark:text-gray-900 group-hover/item:text-gray-600 dark:group-hover/item:text-white transition"
				>
					<!--  -->
					<svg
						xmlns="http://www.w3.org/2000/svg"
						viewBox="0 0 16 16"
						fill="currentColor"
						class="size-4"
					>
						<path
							fill-rule="evenodd"
							d="M4.22 11.78a.75.75 0 0 1 0-1.06L9.44 5.5H5.75a.75.75 0 0 1 0-1.5h5.5a.75.75 0 0 1 .75.75v5.5a.75.75 0 0 1-1.5 0V6.56l-5.22 5.22a.75.75 0 0 1-1.06 0Z"
							clip-rule="evenodd"
						/>
					</svg>
				</div>
			</a>
		{/each}
	</div>
</Collapsible>
