<script lang="ts">
	import { getI18n } from '$lib/utils/context';

	import { toast } from 'svelte-sonner';
	import { goto } from '$app/navigation';

	import { onMount } from 'svelte';
	const i18n = getI18n();

	import { page } from '$app/stores';
	import { models } from '$lib/stores';

	import { getModelById, updateModelById } from '$lib/apis/models';

	import { getModels } from '$lib/apis';
	import ModelEditor from '$lib/components/workspace/Models/ModelEditor.svelte';
	import { getRequestToken } from '$lib/services/auth';

	let model: any = null;

	onMount(async () => {
		const _id = $page.url.searchParams.get('id');
		if (_id) {
			model = await getModelById(getRequestToken(), _id).catch((e) => {
				return null;
			});

			if (!model) {
				goto('/workspace/models');
			}
		} else {
			goto('/workspace/models');
		}
	});

	const onSubmit = async (modelInfo: any) => {
		const res = await updateModelById(getRequestToken(), modelInfo.id, modelInfo);

		if (res) {
			await models.set(await getModels(getRequestToken()));
			toast.success($i18n.t('Model updated successfully'));
			await goto('/workspace/models');
		}
	};
</script>

{#if model}
	<ModelEditor edit={true} {model} {onSubmit} />
{/if}
