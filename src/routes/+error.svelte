<script lang="ts">
	import { page } from '$app/stores';
</script>

<main class=" bg-white dark:bg-gray-800 min-h-screen">
	<div class=" flex h-full">
		<h1 class="m-auto my-10 dark:text-gray-300 text-3xl font-semibold">
			{$page.status}: {$page.error.message}
		</h1>
	</div>
</main>
