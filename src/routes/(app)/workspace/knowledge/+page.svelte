<script>
	import { onMount } from 'svelte';
	import { knowledge } from '$lib/stores';

	import { getKnowledgeBases } from '$lib/apis/knowledge';
	import Knowledge from '$lib/components/workspace/Knowledge.svelte';
	import { getRequestToken } from '$lib/services/auth';

	onMount(async () => {
		await Promise.all([
			(async () => {
				knowledge.set(await getKnowledgeBases(getRequestToken()));
			})()
		]);
	});
</script>

{#if $knowledge !== null}
	<Knowledge />
{/if}
