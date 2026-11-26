<script lang="ts">
	import { goto } from '$lib/navigation';
	import { onMount } from 'svelte';

	onMount(() => {
		goto('/admin/functions/create');
	});
</script>
