<script lang="ts">
	import { getI18n } from '$lib/utils/context';

	import Info from '$lib/components/icons/Info.svelte';

	const i18n = getI18n();

	export let content = '';

	const translateIfKey = (text: string) => ($i18n.exists(text) ? $i18n.t(text) : text);

	const localizeErrorContent = (value: string) => {
		const normalized = value.replace('Network Problem', $i18n.t('Network Problem'));
		const prefixes = ['MCP: ', 'CrewAI MCP: ', 'OpenAI: ', 'Ollama: '];

		for (const prefix of prefixes) {
			if (normalized.startsWith(prefix)) {
				return `${prefix}${translateIfKey(normalized.slice(prefix.length))}`;
			}
		}

		return translateIfKey(normalized);
	};
</script>

<div class="flex my-2 gap-2.5 border px-4 py-3 border-red-600/10 bg-red-600/10 rounded-lg">
	<div class=" self-start mt-0.5">
		<Info className="size-5 text-red-700 dark:text-red-400" />
	</div>

	<div class=" self-center text-sm">
		{typeof content === 'string' ? localizeErrorContent(content) : JSON.stringify(content)}
	</div>
</div>
