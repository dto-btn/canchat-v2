<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { createNewFolder, getFolders, type Folder } from '$lib/apis/folders';
	import { updateChatFolderIdById } from '$lib/apis/chats';
	import { getRequestToken } from '$lib/services/auth';
	import { getI18n } from '$lib/utils/context';
	import Modal from '$lib/components/common/Modal.svelte';

	const i18n = getI18n();
	const dispatch = createEventDispatcher();

	export let show = false;
	export let chatId = '';
	export let returnFocusSelector = '';

	let folders: Folder[] = [];
	let destination = '';
	let newFolderName = '';
	let createFolder = false;
	let loading = true;
	let saving = false;

	const folderPath = (folder: Folder): string => {
		const names = [folder.name];
		const visited = new Set([folder.id]);
		let parent = folders.find((item) => item.id === folder.parent_id);
		while (parent && !visited.has(parent.id)) {
			visited.add(parent.id);
			names.unshift(parent.name);
			parent = folders.find((item) => item.id === parent?.parent_id);
		}
		return names.join(' / ');
	};

	const loadFolders = async () => {
		loading = true;
		try {
			folders = await getFolders(getRequestToken());
		} catch (error) {
			toast.error(`${error}`);
			show = false;
		} finally {
			loading = false;
		}
	};

	$: if (show) {
		destination = '';
		newFolderName = '';
		createFolder = false;
		loadFolders();
	}

	const moveChat = async () => {
		if (saving || loading) return;
		if (createFolder && !newFolderName.trim()) {
			toast.error($i18n.t('Folder name cannot be empty.'));
			return;
		}
		saving = true;
		try {
			if (createFolder) {
				const folder: Folder = await createNewFolder(getRequestToken(), newFolderName.trim());
				folders = [...folders, folder];
				destination = folder.id;
				createFolder = false;
			}
			await updateChatFolderIdById(getRequestToken(), chatId, destination || undefined);
			toast.success($i18n.t('Chat moved successfully'));
			show = false;
			let folder = folders.find((item) => item.id === destination);
			const visited = new Set<string>();
			while (folder?.parent_id && !visited.has(folder.id)) {
				visited.add(folder.id);
				const parent = folders.find((item) => item.id === folder?.parent_id);
				if (!parent) break;
				folder = parent;
			}
			dispatch('change', {
				focusId: folder ? `folder-${folder.id}-button` : `chat-menu-${chatId}`
			});
		} catch (error) {
			toast.error(`${error}`);
		} finally {
			saving = false;
		}
	};
</script>

<Modal bind:show size="sm" title={$i18n.t('Move to folder')} {returnFocusSelector}>
	<form class="p-5 space-y-4" on:submit|preventDefault={moveChat}>
		<h2 class="text-lg font-semibold">{$i18n.t('Move to folder')}</h2>
		<label class="block">
			<span>{$i18n.t('Destination folder')}</span>
			<select
				class="w-full rounded-lg p-2 dark:bg-gray-800"
				bind:value={destination}
				aria-busy={loading}
				disabled={saving || createFolder}
			>
				<option value="">{$i18n.t('No folder')}</option>
				{#each [...folders].sort( (a, b) => folderPath(a).localeCompare(folderPath(b)) ) as folder (folder.id)}
					<option value={folder.id}>{folderPath(folder)}</option>
				{/each}
			</select>
		</label>
		<label class="flex items-center gap-2">
			<input type="checkbox" bind:checked={createFolder} disabled={loading || saving} />
			{$i18n.t('Create new folder')}
		</label>
		{#if createFolder}
			<label class="block">
				<span>{$i18n.t('Folder name')}</span>
				<input
					class="w-full rounded-lg p-2 dark:bg-gray-800"
					bind:value={newFolderName}
					disabled={saving}
				/>
			</label>
		{/if}
		<div class="flex justify-end gap-3">
			<button type="button" class="rounded-lg px-3 py-2" on:click={() => (show = false)}
				>{$i18n.t('Cancel')}</button
			>
			<button
				type="submit"
				class="rounded-lg px-3 py-2 bg-gray-200 dark:bg-gray-700"
				disabled={loading || saving}>{$i18n.t('Move')}</button
			>
		</div>
	</form>
</Modal>
