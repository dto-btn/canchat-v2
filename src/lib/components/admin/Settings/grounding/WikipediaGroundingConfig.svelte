<script lang="ts">
	import { getI18n } from '$lib/utils/context';

	import {
		EKH_KNOWLEDGE_SOURCES,
		WIKIPEDIA_GROUNDING_PROVIDERS,
		getRAGConfig
	} from '$lib/apis/retrieval';
	import type { EkhKnowledgeSource, WikipediaGroundingProvider } from '$lib/apis/retrieval';
	import Switch from '$lib/components/common/Switch.svelte';
	import { onMount } from 'svelte';
	import { getRequestToken } from '$lib/services/auth';
	import { toast } from 'svelte-sonner';

	const i18n = getI18n();

	export let config: {
		enabled: boolean;
		source: EkhKnowledgeSource;
		provider: WikipediaGroundingProvider;
	} = {
		enabled: false,
		source: EKH_KNOWLEDGE_SOURCES[0],
		provider: WIKIPEDIA_GROUNDING_PROVIDERS[0]
	};

	export let loaded = false;
	let loading = false;

	onMount(async () => {
		loading = true;
		try {
			const res = await getRAGConfig(getRequestToken());
			if (!res) {
				throw new Error('Grounding settings response was empty');
			}
			if (
				typeof res.enable_wikipedia_grounding !== 'boolean' ||
				!EKH_KNOWLEDGE_SOURCES.includes(res.ekh_knowledge_source)
			) {
				throw new Error('Grounding settings response was invalid');
			}
			config.enabled = res.enable_wikipedia_grounding;
			config.provider = WIKIPEDIA_GROUNDING_PROVIDERS.includes(res.wikipedia_grounding_provider)
				? res.wikipedia_grounding_provider
				: WIKIPEDIA_GROUNDING_PROVIDERS[0];
			config.source = res.ekh_knowledge_source;
			loaded = true;
		} catch (error) {
			console.error('Error loading grounding settings:', error);
			toast.error($i18n.t('Failed to load grounding settings'));
		} finally {
			loading = false;
		}
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
					{#if config.provider === WIKIPEDIA_GROUNDING_PROVIDERS[1]}
						{$i18n.t('Enterprise Knowledge Hub (EKH) grounding')}
					{:else}
						{$i18n.t('Wikipedia Grounding')}
					{/if}
				</h3>
				<p id="knowledge-grounding-desc" class="text-xs text-gray-600 dark:text-gray-400 mt-1">
					{#if config.provider === WIKIPEDIA_GROUNDING_PROVIDERS[1]}
						{$i18n.t('Enhance responses with the selected EKH source')}
					{:else}
						{$i18n.t('Enhance responses with Wikipedia knowledge')}
					{/if}
				</p>
			</div>
			<Switch
				bind:state={config.enabled}
				aria-labelledby="knowledge-grounding-label"
				aria-describedby="knowledge-grounding-desc"
				disabled={loading || !loaded}
			/>
		</div>

		{#if config.provider === WIKIPEDIA_GROUNDING_PROVIDERS[1]}
			<div class="flex items-center justify-between gap-3">
				<label
					for="ekh-grounding-source"
					class="text-xs font-medium text-gray-700 dark:text-gray-300"
				>
					{$i18n.t('Grounding source')}
				</label>
				<select
					id="ekh-grounding-source"
					class="dark:bg-gray-900 rounded border border-gray-300 dark:border-gray-600 px-2 py-1 text-xs bg-transparent focus-visible:ring-2 focus-visible:ring-emerald-600 focus-visible:ring-offset-1"
					bind:value={config.source}
					disabled={loading || !loaded}
				>
					<option value={EKH_KNOWLEDGE_SOURCES[0]}>{$i18n.t('Wikipedia')}</option>
					<option value={EKH_KNOWLEDGE_SOURCES[1]}>{$i18n.t('TBS Policies')}</option>
				</select>
			</div>

			{#if config.enabled}
				<div
					class="text-xs text-gray-600 dark:text-gray-400 bg-gray-50 dark:bg-gray-800 rounded-md p-3"
				>
					{#if config.source === EKH_KNOWLEDGE_SOURCES[1]}
						{$i18n.t('Grounding will use TBS Policies indexed in EKH.')}
					{:else}
						{$i18n.t('Grounding will use Wikipedia indexed in EKH.')}
					{/if}
				</div>
			{/if}
		{/if}
	</div>
</div>
