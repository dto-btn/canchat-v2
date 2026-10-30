<script lang="ts">
	import { createEventDispatcher, tick } from 'svelte';
	import type { MetricsModelOption } from '$lib/apis/metrics';
	import { getI18n } from '$lib/utils/context';
	import ChevronDown from '$lib/components/icons/ChevronDown.svelte';
	import ChevronRight from '$lib/components/icons/ChevronRight.svelte';
	import ArchiveBox from '$lib/components/icons/ArchiveBox.svelte';
	import { OTHER_FAMILY, getModelFamily, groupModelsByFamily } from '$lib/utils/metricsModels';

	const i18n = getI18n();
	const dispatch = createEventDispatcher<{ change: string }>();

	export let activeModels: MetricsModelOption[] = [];
	export let archivedModels: MetricsModelOption[] = [];
	export let value: string | null = null;
	// Id of the visible label, so screen readers read "<label> <selected model>"
	export let labelledBy: string | null = null;
	// Prefix for element ids; change it if the dropdown appears twice on a page
	export let id = 'model-select';

	let open = false;
	let root: HTMLDivElement;
	let trigger: HTMLButtonElement;
	let panel: HTMLDivElement;
	let expanded: Record<string, boolean> = {};
	let archiveExpanded = false;
	// Archived family groups open separately from Active ones
	let archivedExpanded: Record<string, boolean> = {};

	$: groups = groupModelsByFamily(activeModels);
	$: archivedGroups = groupModelsByFamily(archivedModels);

	// Family names are brands; only "Other" is translated (literal key for i18n:parse)
	$: groupLabel = (name: string): string => (name === OTHER_FAMILY ? $i18n.t('Other') : name);

	$: selectedModel = [...activeModels, ...archivedModels].find((m) => m.id === value);
	$: selectedLabel = selectedModel?.name ?? value ?? '';
	$: selectedIsArchived = archivedModels.some((m) => m.id === value);

	async function openPanel() {
		open = true;
		// Expand the section holding the current selection
		if (selectedIsArchived && selectedModel) {
			archiveExpanded = true;
			archivedExpanded[getModelFamily(selectedModel)] = true;
		} else if (selectedModel) {
			expanded[getModelFamily(selectedModel)] = true;
		}
		await tick();
		const current = panel?.querySelector<HTMLElement>('[aria-pressed="true"]');
		(current ?? getItems()[0])?.focus();
	}

	function close(returnFocus = true) {
		open = false;
		if (returnFocus) trigger?.focus();
	}

	function toggleOpen() {
		if (open) close();
		else openPanel();
	}

	function select(model: MetricsModelOption) {
		value = model.id;
		close();
		dispatch('change', model.id);
	}

	// Every focusable row in the panel, in visual order
	function getItems(): HTMLElement[] {
		return panel ? Array.from(panel.querySelectorAll<HTMLElement>('button')) : [];
	}

	function handlePanelKeydown(e: KeyboardEvent) {
		const items = getItems();
		if (items.length === 0) return;
		const index = items.indexOf(document.activeElement as HTMLElement);
		let next: number | null = null;
		if (e.key === 'ArrowDown') next = index < items.length - 1 ? index + 1 : 0;
		else if (e.key === 'ArrowUp') next = index > 0 ? index - 1 : items.length - 1;
		else if (e.key === 'Home') next = 0;
		else if (e.key === 'End') next = items.length - 1;
		if (next !== null) {
			e.preventDefault();
			items[next].focus();
		}
	}

	function handleTriggerKeydown(e: KeyboardEvent) {
		if (!open && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) {
			e.preventDefault();
			openPanel();
		}
	}

	function handleKeydown(e: KeyboardEvent) {
		if (open && e.key === 'Escape') {
			e.preventDefault();
			close();
		}
	}

	function handleWindowClick(e: MouseEvent) {
		if (open && root && !root.contains(e.target as Node)) close(false);
	}

	// Close when focus leaves the dropdown (e.g. tabbing past the last item)
	function handleFocusOut(e: FocusEvent) {
		if (open && !root.contains(e.relatedTarget as Node | null)) close(false);
	}
</script>

<svelte:window on:click={handleWindowClick} on:keydown={handleKeydown} />

<div class="relative" bind:this={root} on:focusout={handleFocusOut}>
	<button
		type="button"
		{id}
		bind:this={trigger}
		aria-expanded={open}
		aria-controls={open ? `${id}-panel` : undefined}
		aria-labelledby={labelledBy ? `${labelledBy} ${id}` : undefined}
		class="flex items-center justify-between gap-2 w-56 p-2 text-sm text-left border border-gray-400 rounded-md shadow-sm focus:ring-blue-500 focus:border-blue-500 bg-white dark:bg-gray-800 dark:border-gray-600 dark:text-gray-200"
		on:click={toggleOpen}
		on:keydown={handleTriggerKeydown}
	>
		<span class="truncate" title={value ?? ''}>
			{selectedLabel || $i18n.t('Select Model:')}
			{#if selectedIsArchived}
				<span class="text-xs text-gray-500">({$i18n.t('Archived')})</span>
			{/if}
		</span>
		<ChevronDown className="w-4 h-4 shrink-0" />
	</button>

	{#if open}
		<!-- tabindex -1: clicking blank space in the panel keeps it open -->
		<!-- svelte-ignore a11y-no-static-element-interactions -->
		<div
			id="{id}-panel"
			tabindex="-1"
			bind:this={panel}
			on:keydown={handlePanelKeydown}
			class="absolute z-30 mt-1 w-72 max-h-96 overflow-y-auto rounded-md border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 shadow-lg py-1 text-sm"
		>
			{#if groups.length === 0 && archivedModels.length === 0}
				<div class="px-3 py-2 text-gray-500">{$i18n.t('No models found')}</div>
			{/if}

			{#if groups.length > 0}
				<div class="px-3 pt-1 pb-1 text-xs font-semibold uppercase text-gray-500">
					{$i18n.t('Active')}
				</div>
			{/if}

			{#each groups as group, i (group.name)}
				<button
					type="button"
					aria-controls="{id}-group-{i}"
					class="flex w-full items-center gap-1.5 px-3 py-1.5 font-medium text-gray-800 dark:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700 focus:outline-none focus-visible:bg-gray-100 dark:focus-visible:bg-gray-700"
					aria-expanded={!!expanded[group.name]}
					on:click={() => (expanded[group.name] = !expanded[group.name])}
				>
					{#if expanded[group.name]}
						<ChevronDown className="w-3.5 h-3.5" />
					{:else}
						<ChevronRight className="w-3.5 h-3.5" />
					{/if}
					<span class="flex-1 text-left">{groupLabel(group.name)}</span>
					<span class="text-xs text-gray-500">{group.models.length}</span>
				</button>
				<div id="{id}-group-{i}">
					{#if expanded[group.name]}
						{#each group.models as model (model.id)}
							<button
								type="button"
								aria-pressed={model.id === value}
								title={model.id}
								class="block w-full truncate pl-9 pr-3 py-1.5 text-left hover:bg-gray-100 dark:hover:bg-gray-700 focus:outline-none focus-visible:bg-gray-100 dark:focus-visible:bg-gray-700 {model.id ===
								value
									? 'bg-blue-50 text-blue-700 dark:bg-gray-700 dark:text-blue-300'
									: 'text-gray-700 dark:text-gray-300'}"
								on:click={() => select(model)}
							>
								{model.name}
							</button>
						{/each}
					{/if}
				</div>
			{/each}

			{#if archivedModels.length > 0}
				<div class="my-1 border-t border-gray-200 dark:border-gray-700"></div>
				<button
					type="button"
					aria-controls="{id}-archived"
					class="flex w-full items-center gap-1.5 px-3 py-1.5 font-medium text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-700 focus:outline-none focus-visible:bg-gray-100 dark:focus-visible:bg-gray-700"
					aria-expanded={archiveExpanded}
					on:click={() => (archiveExpanded = !archiveExpanded)}
				>
					{#if archiveExpanded}
						<ChevronDown className="w-3.5 h-3.5" />
					{:else}
						<ChevronRight className="w-3.5 h-3.5" />
					{/if}
					<ArchiveBox className="w-3.5 h-3.5" strokeWidth="2" />
					<span class="flex-1 text-left">{$i18n.t('Archived')}</span>
					<span class="text-xs text-gray-500">{archivedModels.length}</span>
				</button>
				<div id="{id}-archived">
					{#if archiveExpanded}
						{#each archivedGroups as group, i (group.name)}
							<button
								type="button"
								aria-controls="{id}-archived-group-{i}"
								class="flex w-full items-center gap-1.5 pl-6 pr-3 py-1.5 font-medium text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-700 focus:outline-none focus-visible:bg-gray-100 dark:focus-visible:bg-gray-700"
								aria-expanded={!!archivedExpanded[group.name]}
								on:click={() => (archivedExpanded[group.name] = !archivedExpanded[group.name])}
							>
								{#if archivedExpanded[group.name]}
									<ChevronDown className="w-3.5 h-3.5" />
								{:else}
									<ChevronRight className="w-3.5 h-3.5" />
								{/if}
								<span class="flex-1 text-left">{groupLabel(group.name)}</span>
								<span class="text-xs text-gray-500">{group.models.length}</span>
							</button>
							<div id="{id}-archived-group-{i}">
								{#if archivedExpanded[group.name]}
									{#each group.models as model (model.id)}
										<button
											type="button"
											aria-pressed={model.id === value}
											title={model.id}
											class="block w-full truncate pl-12 pr-3 py-1.5 text-left hover:bg-gray-100 dark:hover:bg-gray-700 focus:outline-none focus-visible:bg-gray-100 dark:focus-visible:bg-gray-700 {model.id ===
											value
												? 'bg-blue-50 text-blue-700 dark:bg-gray-700 dark:text-blue-300'
												: 'text-gray-500 dark:text-gray-400'}"
											on:click={() => select(model)}
										>
											{model.name}
										</button>
									{/each}
								{/if}
							</div>
						{/each}
					{/if}
				</div>
			{/if}
		</div>
	{/if}
</div>
