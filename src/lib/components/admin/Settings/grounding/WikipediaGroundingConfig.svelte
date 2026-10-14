<script lang="ts">
	import { getI18n } from '$lib/utils/context';

	import { getRAGConfig } from '$lib/apis/retrieval';
	import Switch from '$lib/components/common/Switch.svelte';
	import { onMount } from 'svelte';
	import { getRequestToken } from '$lib/services/auth';

	const i18n = getI18n();

	export let config: { enabled: boolean; source: 'wikipedia' | 'tbs-policies' } = {
		enabled: false,
		source: 'wikipedia'
	};

	let loading = false;

	onMount(async () => {
		loading = true;
		const res = await getRAGConfig(getRequestToken());
		if (res) {
			config.enabled = res?.enable_wikipedia_grounding ?? true;
			config.source = res?.ekh_knowledge_source === 'tbs-policies' ? 'tbs-policies' : 'wikipedia';
		}
		loading = false;
	});
</script>

<div class="bg-white dark:bg-gray-900 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
	<div class="space-y-4">
		<div class="flex items-center justify-between">
			<div>
				<h3
					id="knowledge-grounding-label"
					class="text-sm font-semibold text-gray-900 dark:text-gray-100"
				>
					{$i18n.t('Knowledge Grounding')}
				</h3>
				<p class="text-xs text-gray-600 dark:text-gray-400 mt-1">
					{$i18n.t('Enhance responses with the selected EKH source')}
				</p>
			</div>
			<Switch bind:state={config.enabled} aria-labelledby="knowledge-grounding-label" />
		</div>

		<div class="flex items-center justify-between gap-3">
			<label
				for="ekh-grounding-source"
				class="text-xs font-medium text-gray-700 dark:text-gray-300"
			>
				{$i18n.t('Grounding source')}
			</label>
			<select
				id="ekh-grounding-source"
				class="dark:bg-gray-900 rounded px-2 py-1 text-xs bg-transparent outline-none"
				bind:value={config.source}
				disabled={loading}
			>
				<option value="wikipedia">{$i18n.t('Wikipedia')}</option>
				<option value="tbs-policies">{$i18n.t('TBS Policies')}</option>
			</select>
		</div>

		{#if config.enabled}
			<div
				class="text-xs text-gray-600 dark:text-gray-400 bg-gray-50 dark:bg-gray-800 rounded-md p-3"
			>
				{#if config.source === 'tbs-policies'}
					{$i18n.t('Grounding will use TBS Policies indexed in EKH.')}
				{:else}
					{$i18n.t('Grounding will use Wikipedia indexed in EKH.')}
				{/if}
			</div>
		{/if}
	</div>
</div>
