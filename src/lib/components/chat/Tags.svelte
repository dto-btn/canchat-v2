<script>
	import {
		addTagById,
		deleteTagById,
		getAllTags,
		getTagsById,
		updateChatById
	} from '$lib/apis/chats';
	import { tags as _tags } from '$lib/stores';
	import { createEventDispatcher, onMount } from 'svelte';

	const dispatch = createEventDispatcher();

	import Tags from '../common/Tags.svelte';
	import { toast } from 'svelte-sonner';
	import { getRequestToken } from '$lib/services/auth';

	export let chatId = '';
	let tags = [];

	const getTags = async () => {
		return await getTagsById(getRequestToken(), chatId).catch(async (error) => {
			return [];
		});
	};

	const addTag = async (tagName) => {
		const res = await addTagById(getRequestToken(), chatId, tagName).catch(async (error) => {
			toast.error(`${error}`);
			return null;
		});
		if (!res) {
			return;
		}

		tags = await getTags();
		await updateChatById(getRequestToken(), chatId, {
			tags: tags
		});

		await _tags.set(await getAllTags(getRequestToken()));
		dispatch('add', {
			name: tagName
		});
	};

	const deleteTag = async (tagName) => {
		const res = await deleteTagById(getRequestToken(), chatId, tagName);
		tags = await getTags();
		await updateChatById(getRequestToken(), chatId, {
			tags: tags
		});

		await _tags.set(await getAllTags(getRequestToken()));
		dispatch('delete', {
			name: tagName
		});
	};

	onMount(async () => {
		if (chatId) {
			tags = await getTags();
		}
	});
</script>

<Tags
	{tags}
	on:delete={(e) => {
		deleteTag(e.detail);
	}}
	on:add={(e) => {
		addTag(e.detail);
	}}
/>
