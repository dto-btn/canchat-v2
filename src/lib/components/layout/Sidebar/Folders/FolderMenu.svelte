<script lang="ts">
	import { getI18n } from '$lib/utils/context';

	import { DropdownMenu } from 'bits-ui';
	import { flyAndScale } from '$lib/utils/transitions';
	import { createEventDispatcher } from 'svelte';

	const i18n = getI18n();
	const dispatch = createEventDispatcher();

	import Dropdown from '$lib/components/common/Dropdown.svelte';
	import GarbageBin from '$lib/components/icons/GarbageBin.svelte';
	import Pencil from '$lib/components/icons/Pencil.svelte';

	export let buttonClass = '';
	export let ariaLabel = '';
	export let buttonID = '';

	let show = false;
</script>

<Dropdown
	bind:show
	on:change={(e) => {
		if (e.detail === false) {
			dispatch('close');
		}
	}}
	{buttonClass}
	{ariaLabel}
	{buttonID}
>
	<slot />

	<div slot="content">
		<DropdownMenu.Content
			class="w-full max-w-[160px] rounded-lg px-1 py-1.5  z-50 bg-white dark:bg-gray-850 dark:text-white shadow-lg"
			sideOffset={-2}
			side="bottom"
			align="start"
			transition={flyAndScale}
		>
			<DropdownMenu.Item
				class="flex gap-2 items-center px-3 py-1.5 text-sm  cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-md"
				on:click={() => {
					dispatch('rename');
				}}
			>
				<Pencil strokeWidth="2" />
				<div class="flex items-center">{$i18n.t('Rename')}</div>
			</DropdownMenu.Item>

			<DropdownMenu.Item
				class="flex  gap-2  items-center px-3 py-1.5 text-sm  cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-md"
				on:click={() => {
					dispatch('delete');
				}}
			>
				<GarbageBin strokeWidth="2" />
				<div class="flex items-center">{$i18n.t('Delete')}</div>
			</DropdownMenu.Item>
		</DropdownMenu.Content>
	</div>
</Dropdown>
