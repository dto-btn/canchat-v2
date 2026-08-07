<script>
	import { onMount } from 'svelte';
	import { models } from '$lib/stores';
	import { getModels } from '$lib/apis';
	import Models from '$lib/components/workspace/Models.svelte';
	import { getRequestToken } from '$lib/services/auth';

	onMount(async () => {
		await Promise.all([
			(async () => {
				models.set(await getModels(getRequestToken()));
			})()
		]);
	});
</script>

{#if $models !== null}
	<Models />
{/if}
