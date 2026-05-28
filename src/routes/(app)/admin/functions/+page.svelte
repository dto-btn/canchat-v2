<script>
	import { onMount } from 'svelte';
	import { functions } from '$lib/stores';

	import { getFunctions } from '$lib/apis/functions';
	import Functions from '$lib/components/admin/Functions.svelte';
	import { getRequestToken } from '$lib/services/auth';

	onMount(async () => {
		await Promise.all([
			(async () => {
				functions.set(await getFunctions(getRequestToken()));
			})()
		]);
	});
</script>

{#if $functions !== null}
	<Functions />
{/if}
