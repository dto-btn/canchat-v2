<script lang="ts">
	import { getI18n } from '$lib/utils/context';

	import { DropdownMenu } from 'bits-ui';
	import { flyAndScale } from '$lib/utils/transitions';
	import { createEventDispatcher } from 'svelte';
	const dispatch = createEventDispatcher();

	import Dropdown from '$lib/components/common/Dropdown.svelte';
	import GarbageBin from '$lib/components/icons/GarbageBin.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import EllipsisHorizontal from '$lib/components/icons/EllipsisHorizontal.svelte';

	const i18n = getI18n();

	export let onClose: Function = () => {};

	let show = false;
</script>

<Dropdown
	bind:show
	on:change={(e) => {
		if (e.detail === false) {
			onClose();
		}
	}}
	align="end"
>
	<Tooltip content={$i18n.t('More')}>
		<slot
			><button
				class="self-center w-fit text-sm p-1.5 dark:text-gray-300 dark:hover:text-white hover:bg-black/5 dark:hover:bg-white/5 rounded-xl"
				type="button"
				on:click={(e) => {
					e.stopPropagation();
					show = true;
				}}
			>
				<EllipsisHorizontal className="size-5" />
			</button>
		</slot>
	</Tooltip>

	<div slot="content">
		<DropdownMenu.Content
			class="w-full max-w-[160px] rounded-xl px-1 py-1.5 border border-gray-300/30 dark:border-gray-700/50 z-50 bg-white dark:bg-gray-850 dark:text-white shadow"
			sideOffset={-2}
			side="bottom"
			align="end"
			transition={flyAndScale}
		>
			<DropdownMenu.Item
				class="flex  gap-2  items-center px-3 py-2 text-sm  font-medium cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-md"
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
